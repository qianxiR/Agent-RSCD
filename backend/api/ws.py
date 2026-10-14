"""WebSocket 聊天路由 (/api/v1/agent/ws/chat)。

★ chat_service 单例由 main.py 在启动时通过 ``set_chat_service`` 注入,
  避免 router 层反向实例化 AgentChatService (依赖循环风险)。
"""
import asyncio
import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.agent.chat_service import ToolChatRequest
from backend.agent.ws_manager import get_ws_manager
from backend.api.deps import agent_db
from backend.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)

# 由 main.py 启动时注入; 在此之前为 None, 但 WebSocket 端点不会被调用 (服务未就绪)。
_chat_service = None


def set_chat_service(service) -> None:
    """由 main.py 调用, 注入 AgentChatService 单例。"""
    global _chat_service
    _chat_service = service


def _user_id_from_token(token):
    """
    从 JWT payload 解出 user_id (sub), 不验签。
    入参: token (str|None) 连接 URL 上的 ?token=
    出参: user_id 字符串; token 缺失/格式非法返回 None (回落前端传的 user_id)
    边界: 仅做 base64 解码 + JSON 解析, 任何异常都静默降级, 不阻断连接。
    """
    if not token or token.count(".") != 2:
        return None
    try:
        import base64
        import json
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)  # 补齐 base64 padding
        data = json.loads(base64.urlsafe_b64decode(payload_b64))
        return data.get("sub") or None
    except Exception:
        return None


@router.websocket("/api/v1/agent/ws/chat")
async def websocket_chat(websocket: WebSocket, session_id: str = None, token: str = None):
    """
    WebSocket 全双工聊天端点

    ★ v2.1 多对话并行:
      同一 WebSocket 连接可同时运行多个对话。每条下行消息均携带
      conversation_id, 前端据此路由到对应的对话 Tab。

    ★ 双服务架构 (2026-10): token 为 Java 业务服务签发的 JWT (可选)。
      一阶段仅解码 payload 取 sub 作为 user_id (无验签, 透传用途),
      覆盖前端自由传的 user_id; 完整验签在后续权限升级中引入。

    消息协议:
      客户端 → 服务端:
        {type: "chat_request", prompt: "...", conversation_id: "...", ...}
        {type: "stop_chat", conversation_id: "..."}     ← 可选, 不传则停止全部
        {type: "tool_result", request_id: "...", ...}
        {type: "get_active_tasks"}                       ← 查询当前活跃对话
        {type: "pong"}

      服务端 → 客户端:
        {type: "thinking", content: "...", conversation_id: "..."}
        {type: "tool_call", tool_calls: [...], conversation_id: "..."}
        {type: "frontend_action", ..., conversation_id: "..."}
        {type: "content", content: "...", conversation_id: "..."}
        {type: "error", content: "...", conversation_id: "..."}
        {type: "done", conversation_id: "..."}
        {type: "chat_stopped", conversation_id: "..."}
        {type: "active_tasks_list", conversations: [...]}
        {type: "ping"}
    """
    ws_manager = get_ws_manager()
    sid = await ws_manager.connect(websocket, session_id)
    # JWT 透传: 连接级解析一次 user_id (无验签, 供权限升级前的身份贯通)
    token_user_id = _user_id_from_token(token)

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "unknown")
            logger.info(f"[WS] Received: type={msg_type}")

            if msg_type == "chat_request":
                # ★ conversation_id 贯通: 前端带则续接, 不带则新建并回传
                user_id = token_user_id or data.get("user_id", "study_user")
                prompt_text = data.get("prompt", "")
                conv_id = data.get("conversation_id")
                project_id = data.get("project_id")  # ★ 可选: 所属项目
                images = data.get("images") or []
                selected_layers = data.get("selected_layers") or []

                if conv_id:
                    # 续接已有会话 (agent_db 模式下校验存在性, 不存在也允许新建记录)
                    if not agent_db.get_conversation(conv_id):
                        agent_db.create_conversation(conv_id, user_id, title=prompt_text[:20], project_id=project_id)

                    # ★ v2.1: 检查该对话是否已在运行中
                    active_convs = ws_manager.get_active_conversations(sid)
                    if conv_id in active_convs:
                        await ws_manager.send_to_session(sid, {
                            "type": "error",
                            "content": f"对话 {conv_id[:8]}... 正在处理中, 请等待完成或先停止",
                            "conversation_id": conv_id,
                        })
                        continue
                else:
                    # 新建会话
                    conv_id = str(uuid.uuid4())
                    agent_db.create_conversation(conv_id, user_id, title=prompt_text[:20], project_id=project_id)

                # ★ 回传 conversation_id 让前端保存 (前端在 session_started 事件里接住)
                await ws_manager.send_to_session(sid, {
                    "type": "session_started",
                    "conversation_id": conv_id,
                })

                req = ToolChatRequest(
                    prompt=prompt_text,
                    model=data.get("model", settings.chat_model),
                    temperature=data.get("temperature", settings.default_temperature),
                    conversation_id=conv_id,
                    user_id=user_id,
                    project_id=project_id,  # ★ 所属分组 (沙盒 work_dir 来源)
                    images=images,
                    selected_layers=selected_layers,
                    multi_round=data.get("multi_round", True),
                )

                # ★ 记录 checkpoint (本轮开始前的最后一条消息 id, 供停止回滚用)
                checkpoint_id = agent_db.get_last_message_id(conv_id)

                # ★ v2.1: 在后台任务中执行 Agent 推理, 注册 task (带 conversation_id) 以供取消
                task = asyncio.create_task(
                    _chat_service.tool_chat_ws(req, sid, ws_manager, checkpoint_id=checkpoint_id)
                )
                ws_manager.register_task(sid, conv_id, task)
                logger.info(f"[WS] Task started: session={sid}, conv={conv_id}, "
                           f"active_count={ws_manager.get_task_count(sid)}")

            elif msg_type == "stop_chat":
                # ★ v2.1: 支持按 conversation_id 精确停止, 不传则停止全部
                conv_id = data.get("conversation_id")
                if conv_id:
                    cancelled = ws_manager.cancel_task(sid, conversation_id=conv_id)
                    logger.info(f"[WS] stop_chat: conv={conv_id}, cancelled={cancelled}")
                    # 停止确认必须与模型线程清理解耦，先恢复前端交互，再由取消分支完成回滚和资源收尾。
                    await ws_manager.send_to_session(sid, {
                        "type": "chat_stopped",
                        "conversation_id": conv_id,
                        "cancelled": cancelled,
                    })
                else:
                    # 停止该 session 下所有活跃对话
                    cancelled = ws_manager.cancel_task(sid)
                    logger.info(f"[WS] stop_chat (all): cancelled={cancelled}, sid={sid}")
                    if cancelled == 0:
                        await ws_manager.send_to_session(sid, {
                            "type": "chat_stopped",
                            "session_id": sid,
                        })

            elif msg_type == "get_active_tasks":
                # ★ v2.1 新增: 前端查询当前 session 下有哪些对话在后台运行
                active_convs = ws_manager.get_active_conversations(sid)
                await ws_manager.send_to_session(sid, {
                    "type": "active_tasks_list",
                    "conversations": active_convs,
                    "count": len(active_convs),
                })

            elif msg_type == "tool_result":
                # ★ 前端工具执行结果回传 → 解除 wait_for_frontend_result 阻塞
                await ws_manager.handle_client_message(sid, data)

            elif msg_type == "pong":
                pass

    except WebSocketDisconnect:
        ws_manager.disconnect(sid)
        logger.info(f"[WS] Disconnected: {sid}")
