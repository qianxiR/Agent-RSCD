"""
WebSocket 连接管理器 (Agent 层)
- 入参: websocket 连接对象、session_id
- 方法: 连接生命周期管理、消息广播、前端结果等待、多对话并行任务管理
- 出参: 通过 await 暴露前端工具执行结果

核心机制:
  使用 asyncio.Event 实现后端阻塞等待前端工具执行结果:
  1. 后端发送 frontend_action 给前端, 生成 request_id
  2. 调用 wait_for_frontend_result(request_id) 阻塞
  3. 前端执行完毕后发送 {type: "tool_result", request_id: ...}
  4. handle_client_message 匹配 request_id, set() 事件, 解除阻塞

★ 多对话并行支持 (v2.1):
  - active_tasks 从 session→task 改为 session→{conv_id→task}
  - 同一 WebSocket 连接可同时运行多个对话的 Agent 推理任务
  - cancel_task 支持按 conversation_id 精确取消, 也可取消全部
"""
import asyncio
import uuid
import logging
from typing import Dict, Any, Optional, List

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebSocketManager:
    def __init__(self):
        # session_id → WebSocket 连接
        self.connections: Dict[str, WebSocket] = {}
        # request_id → asyncio.Event (等待前端结果的事件)
        self.pending_events: Dict[str, asyncio.Event] = {}
        # request_id → 前端返回的结果数据
        self.frontend_results: Dict[str, Dict[str, Any]] = {}
        # ★ session_id → {conversation_id → asyncio.Task} (多对话并行)
        self.active_tasks: Dict[str, Dict[str, asyncio.Task]] = {}

    def register_task(self, session_id: str, conversation_id: str, task: asyncio.Task):
        """
        注册活跃的 Agent 推理任务 (供 stop_chat 取消用)。
        - 同一 session 下允许多个 conversation 并行运行
        """
        if session_id not in self.active_tasks:
            self.active_tasks[session_id] = {}
        self.active_tasks[session_id][conversation_id] = task
        logger.info(f"Task registered: session={session_id}, conv={conversation_id}")

    def cancel_task(self, session_id: str, conversation_id: str = None) -> int:
        """
        取消指定 session 的活跃任务。
        - conversation_id 为 None: 取消该 session 下所有活跃任务
        - conversation_id 指定: 只取消该对话的任务
        返回实际取消的任务数。
        """
        if session_id not in self.active_tasks:
            logger.info(f"No active tasks for session: {session_id}")
            return 0

        cancelled_count = 0

        if conversation_id:
            # 取消指定对话
            task = self.active_tasks[session_id].pop(conversation_id, None)
            if task and not task.done():
                task.cancel()
                cancelled_count = 1
                logger.info(f"Task cancelled: session={session_id}, conv={conversation_id}")
            else:
                logger.info(f"No active task to cancel: session={session_id}, conv={conversation_id}")
            # 清理空 session 条目
            if not self.active_tasks[session_id]:
                del self.active_tasks[session_id]
        else:
            # 取消全部
            for conv_id, task in list(self.active_tasks[session_id].items()):
                if not task.done():
                    task.cancel()
                    cancelled_count += 1
                    logger.info(f"Task cancelled: session={session_id}, conv={conv_id}")
            del self.active_tasks[session_id]

        return cancelled_count

    def get_active_conversations(self, session_id: str) -> List[str]:
        """
        返回指定 session 下所有正在运行的 conversation_id 列表。
        - 自动清理已完成的 task
        """
        if session_id not in self.active_tasks:
            return []

        active = []
        finished = []
        for conv_id, task in self.active_tasks[session_id].items():
            if task.done():
                finished.append(conv_id)
            else:
                active.append(conv_id)

        # 清理已完成的
        for conv_id in finished:
            del self.active_tasks[session_id][conv_id]
            logger.info(f"Task auto-cleaned (done): session={session_id}, conv={conv_id}")

        if not self.active_tasks[session_id]:
            del self.active_tasks[session_id]

        return active

    def get_task_count(self, session_id: str) -> int:
        """返回指定 session 下活跃任务数"""
        if session_id not in self.active_tasks:
            return 0
        # 清理已完成的任务
        self.get_active_conversations(session_id)
        return len(self.active_tasks.get(session_id, {}))

    async def connect(self, websocket: WebSocket, session_id: Optional[str] = None) -> str:
        """
        接受 WebSocket 连接并注册到管理器
        - session_id: 可选, 复用已有会话; 不传则生成新 ID
        """
        await websocket.accept()
        sid = session_id or str(uuid.uuid4())
        self.connections[sid] = websocket
        logger.info(f"WebSocket connected: session={sid}")
        return sid

    def disconnect(self, session_id: str):
        """移除连接并清理所有关联任务"""
        self.connections.pop(session_id, None)
        # 清理该 session 的所有活跃任务
        if session_id in self.active_tasks:
            for conv_id, task in list(self.active_tasks[session_id].items()):
                if not task.done():
                    task.cancel()
                    logger.info(f"Task cancelled on disconnect: session={session_id}, conv={conv_id}")
            del self.active_tasks[session_id]
        logger.info(f"WebSocket disconnected: session={session_id}")

    async def send_to_session(self, session_id: str, data: Dict[str, Any]):
        """向指定会话发送 JSON 消息"""
        ws = self.connections.get(session_id)
        if ws:
            await ws.send_json(data)

    async def wait_for_frontend_result(self, request_id: str) -> Dict[str, Any]:
        """
        阻塞等待前端工具执行结果
        - 超时时间由 config.ws_frontend_result_timeout 控制
        - 返回前端发送的 tool_result 数据
        """
        event = asyncio.Event()
        self.pending_events[request_id] = event
        try:
            timeout = 120
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return self.frontend_results.pop(request_id, {"status": "error", "msg": "no result"})
        except asyncio.TimeoutError:
            self.pending_events.pop(request_id, None)
            return {"status": "error", "code": "TIMEOUT", "msg": f"前端执行超时({timeout}s)"}

    async def handle_client_message(self, session_id: str, data: Dict[str, Any]):
        """
        处理客户端主动发送的消息
        - tool_result: 前端工具执行完毕, 触发 pending event 解除阻塞
        - pong: 心跳响应
        """
        msg_type = data.get("type", "")
        if msg_type == "tool_result":
            request_id = data.get("request_id")
            if request_id and request_id in self.pending_events:
                self.frontend_results[request_id] = data
                self.pending_events[request_id].set()
                logger.info(f"Frontend result received: request_id={request_id}")


# 全局单例
ws_manager = WebSocketManager()


def get_ws_manager() -> WebSocketManager:
    return ws_manager
