"""
核心 Agent 聊天服务 (Agent 层)
- 入参: ToolChatRequest (prompt, model, conversation_id 等)
- 方法: WebSocket 全双工处理
- 出参: 通过 ws_manager.send_to_session 推送各阶段结果

完整处理循环:
  1. 构建消息上下文 (system prompt + 三层记忆 + 历史)
  2. 流式调用 LLM (astream) → 实时推送每个 thinking token
  3. LLM 流结束 → 从累积的 AIMessage 中提取 tool_calls
  4. 若有 tool_calls:
     - 通知前端 (tool_call 事件)
     - 执行工具函数
     - 若需前端配合: 发送 frontend_action → 阻塞等待 tool_result
     - 将工具结果追加到消息列表
     - 继续下一轮 LLM 调用 (无限循环, 直到 LLM 不再返回 tool_calls 给出最终回复)
  5. LLM 无 tool_calls 时: 流式输出的内容即为最终回复
  6. 收尾: persist_turn + maybe_summarize (每轮); 长期记忆仅对话停止时提取
  7. 发送 done 事件

★ v2.1 多对话并行:
  - 所有下行消息均携带 conversation_id, 前端据此路由到对应对话 UI
  - CancelledError 不再向上传播, 避免影响同 session 下其他对话任务
  - 任务完成后自动从 ws_manager 清理注册

关键改造: ainvoke → astream
  原方案 ainvoke 阻塞等待 LLM 完整响应, 思考过程对用户不可见
  现方案 astream 逐 token 推送, 用户可以实时看到 AI 的推理过程:
    - 每个流式 chunk 的 content 都作为 thinking 事件推送到前端
    - 流结束后, 若检测到 tool_calls 则 thinking 内容确为"思考过程"
    - 若无 tool_calls, 则 thinking 内容即为最终回复, 切换为 content 事件
"""
import uuid
import logging
import asyncio
import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    SystemMessage,
    ToolMessage,
)

from backend.config import settings
from backend.model.llm_client import create_llm, create_llm_with_tools
from backend.model.tools.tool_registry import (
    get_tools_for_llm, get_tool_by_name, get_tool_category_map,
)
from backend.agent.ws_manager import WebSocketManager
from backend.agent.memory import memory_context as memory_mod
from backend.agent.memory import agent_db
from backend.agent.memory import pattern_tracker
from backend.agent.memory.task_state import (
    apply_step_observation,
    build_plan_context_text,
    can_finalize_plan,
    create_plan_state,
    load_plan_state,
    persist_plan_state,
    start_tool_step,
)
from backend.agent.memory.lesson_policy import parse_accepted_lesson_contracts
from backend.agent.runtime.observation_builder import (
    build_observation_tool_message,
    build_repair_followup_system_message,
    should_disable_tools_for_next_repair_turn,
)
from backend.agent.runtime.skill_execution import apply_skill_selection_to_plan
from backend.agent.observability.decision_audit import (
    append_jsonl,
    audit_repair_plan_following,
    extract_actual_decision,
)
from backend.agent.observability.repair_success import (
    build_repair_attempt,
    evaluate_repair_success,
)

# 确保工具模块已加载, 触发注册
# model.tools: GeoServer/数据库/图层/遥感工具 (工具箱主体)
# agent.tools: 记忆工具 (跟随记忆留在 agent 层)
import backend.model.tools
import backend.agent.tools

logger = logging.getLogger(__name__)


class ToolChatRequest(BaseModel):
    """聊天请求模型"""
    prompt: str
    model: str = settings.dashscope_model
    temperature: float = settings.default_temperature
    stream: bool = True
    conversation_id: Optional[str] = None
    user_id: str = "study_user"
    project_id: Optional[str] = None  # ★ 所属分组 (沙盒 work_dir 的来源)
    images: List[Dict[str, Any]] = Field(default_factory=list)
    selected_layers: List[Dict[str, Any]] = Field(default_factory=list)
    multi_round: bool = True


def _safe_attachment_name(value: str) -> str:
    """
    入参:
      - value: 图层名或文件名候选字符串。
    方法:
      - 仅保留 ASCII 字母、数字、点、下划线和短横线。
      - 空值兜底为 attachment, 避免生成非法路径。
    出参:
      - str, 可用于本地文件名的安全名称。
    """
    import re
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", value or "").strip("._-")
    return cleaned or "attachment"


def _preview_url_for_upload_path(file_path: Path) -> str:
    """
    入参:
      - file_path: 位于 settings.samseg_upload_root 下的本地文件路径。
    方法:
      - 计算相对 samseg/send 根目录的路径。
      - 生成现有 /api/v1/upload/{filepath}?preview=1 预览 URL。
    出参:
      - str, 前端可直接用于缩略图预览的 URL。
    """
    from urllib.parse import quote
    root = Path(settings.samseg_upload_root).resolve()
    rel = file_path.resolve().relative_to(root).as_posix()
    return f"/api/v1/upload/{quote(rel)}?preview=1"


def _normalize_chat_images(images: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    入参:
      - images: 前端上传附件列表, 每项应包含 name/path/preview_url/source。
    方法:
      - 过滤无 path 的无效项。
      - 只保留 Agent 和前端展示需要的稳定字段。
    出参:
      - list[dict], 规范化后的影像附件列表。
    """
    normalized = []
    for item in images or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        normalized.append({
            "name": str(item.get("name") or Path(path).name),
            "path": path,
            "preview_url": str(item.get("preview_url") or item.get("url") or ""),
            "source": str(item.get("source") or "upload"),
            "workspace": str(item.get("workspace") or ""),
            "layer_name": str(item.get("layer_name") or ""),
            "full_name": str(item.get("full_name") or ""),
        })
    return normalized


async def _download_selected_layers_for_chat(
    selected_layers: List[Dict[str, Any]],
    conversation_id: str,
) -> List[Dict[str, Any]]:
    """
    入参:
      - selected_layers: 前端 @ 图层选择器传入的 GeoServer 图层列表。
      - conversation_id: 当前会话 ID, 用于隔离下载目录。
    方法:
      - 校验图层为 raster。
      - 调 geoserver_client.download_raster 把服务端图层下载到 samseg/send/{conv}/server_layers。
      - 返回与上传影像相同形态的附件对象。
    出参:
      - list[dict], 已下载到本地的 GeoServer 影像附件。
    """
    if not selected_layers:
        return []

    from backend.data import geoserver_client

    conv = _safe_attachment_name(conversation_id or "_anonymous")
    target_dir = Path(settings.samseg_upload_root).resolve() / conv / "server_layers"
    target_dir.mkdir(parents=True, exist_ok=True)

    downloaded = []
    for layer in selected_layers:
        if not isinstance(layer, dict):
            continue
        workspace = str(layer.get("workspace") or "").strip()
        layer_name = str(layer.get("layer_name") or layer.get("name") or "").strip()
        full_name = str(layer.get("full_name") or (f"{workspace}:{layer_name}" if workspace and layer_name else layer_name)).strip()
        if not full_name or ":" not in full_name:
            raise ValueError("图层引用缺少 workspace 或 layer_name")
        workspace, bare_name = full_name.split(":", 1)
        layer_type = await asyncio.to_thread(geoserver_client.get_layer_type, full_name)
        if layer_type != "raster":
            raise ValueError(f"仅支持选择栅格影像图层: {full_name} (当前类型: {layer_type or 'unknown'})")

        safe_base = _safe_attachment_name(full_name.replace(":", "_"))
        dest = target_dir / f"{safe_base}_{uuid.uuid4().hex[:6]}.tif"
        local_path = await asyncio.to_thread(
            geoserver_client.download_raster,
            full_name,
            workspace,
            str(dest),
        )
        if not local_path:
            raise ValueError(f"GeoServer 图层下载失败: {full_name}")
        local_file = Path(local_path).resolve()
        downloaded.append({
            "name": local_file.name,
            "path": str(local_file),
            "preview_url": _preview_url_for_upload_path(local_file),
            "source": "geoserver",
            "workspace": workspace,
            "layer_name": bare_name,
            "full_name": full_name,
        })
    return downloaded


async def _emit_memory_updated(session_id, conversation_id, ws_manager, items, kind):
    """
    向前端推送 memory_updated 事件 (轻提示用).
    - items: [{key, value, category, source}, ...]
    - kind: "summary" | "long_term" | "pattern"
    推送失败仅日志 (WS 可能已断开), 不影响记忆已落库.
    """
    try:
        await ws_manager.send_to_session(session_id, {
            "type": "memory_updated",
            "conversation_id": conversation_id,
            "kind": kind,
            "items": items,
        })
    except Exception as e:
        logger.warning(f"[Memory] memory_updated 推送失败 conv={conversation_id}: {e}")


# ==================== P3 任务持久化: 输入输出脱敏/摘要 ====================

def _sanitize_task_input(tool_args: dict) -> dict:
    """
    工具入参脱敏: 路径只保留 basename, 避免 agent-files 绝对路径污染任务日志.
    其余字段原样保留 (classes/question 等业务参数量小, 直接存).
    """
    if not isinstance(tool_args, dict):
        return {"raw": str(tool_args)[:200]}
    safe = {}
    for k, v in tool_args.items():
        if isinstance(v, str) and ("path" in k.lower() or k.endswith("_path") or "/" in v or "\\" in v):
            # 路径类字段: 只保留 basename
            safe[k] = v.replace("\\", "/").split("/")[-1] if v else v
        elif isinstance(v, str) and len(v) > 500:
            safe[k] = v[:500] + "...(truncated)"
        else:
            safe[k] = v
    return safe


def _summarize_task_output(tool_result: dict) -> dict:
    """
    工具出参摘要: 只保留关键字段 (type/stats/summary/output_path), 丢弃大字段 (legend/指令参数).
    避免 frontend_action 的 instruction.params (含 URL 等) 全量进任务表.

    ★ v2.5: 路径字段抽取改用 _result.extract_artifact_path, 兼容新旧命名
      (artifact_path / output_path / geojson_path / local_path / shp_path).
    """
    if not isinstance(tool_result, dict):
        return {"type": str(type(tool_result))}
    summary = {}
    for key in ("type", "summary", "description"):
        if key in tool_result:
            val = tool_result[key]
            if isinstance(val, str) and len(val) > 500:
                val = val[:500] + "...(truncated)"
            summary[key] = val
    # data 字段可能含 stats, 抽出来存
    data = tool_result.get("data")
    if isinstance(data, dict):
        if "stats" in data:
            summary["stats"] = data["stats"]
        if "elapsed" in data:
            summary["elapsed"] = data["elapsed"]
        # ★ v2.5: 抽产物路径 (兼容新旧命名), 供任务表展示
        try:
            from backend.model.tools._result import extract_artifact_path
            artifact = extract_artifact_path(data)
            if artifact:
                summary["artifact_path"] = artifact
        except ImportError:
            pass
    return summary


def _is_report_download_request(prompt: str) -> bool:
    """
    入参:
      - prompt: 用户本轮输入文本。
    方法:
      - 只识别“下载/打开/获取/保存 + 报告/PDF/文件”的延续意图。
      - 该判断用于复用最近工具产出的 report_download.url, 避免把报告下载误判为 GeoServer 图层导出。
    出参:
      - bool, True 表示本轮应优先尝试复用已有报告下载入口。
    """
    text = (prompt or "").strip().lower()
    if not text:
        return False
    download_terms = ["下载", "打开", "获取", "保存", "download"]
    report_terms = ["报告", "pdf", "文件", "report"]
    return any(term in text for term in download_terms) and any(term in text for term in report_terms)


def _extract_report_download_from_payload(payload: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """
    入参:
      - payload: 单条 ToolMessage 解析后的工具结果 dict。
    方法:
      - 按 segment_image/report_worker 当前返回结构查找 report_download.url、auto_report.report_url、artifact_url。
      - 只接受 PDF 或 report 下载端点, 防止把影像、矢量或 GeoServer 图层 URL 误当成报告。
    出参:
      - dict: {url, filename, caption}; 未找到时返回 None。
    """
    if not isinstance(payload, dict):
        return None

    candidates = []
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    auto_report = data.get("auto_report") if isinstance(data.get("auto_report"), dict) else {}
    known_report_path = auto_report.get("report_path") or data.get("artifact_path") or ""
    instruction = payload.get("instruction") if isinstance(payload.get("instruction"), dict) else {}
    params = instruction.get("params") if isinstance(instruction.get("params"), dict) else {}
    report_download = params.get("report_download") if isinstance(params.get("report_download"), dict) else {}
    if report_download:
        candidates.append({**report_download, "local_path": report_download.get("local_path") or known_report_path})

    if auto_report.get("report_url"):
        candidates.append({
            "url": auto_report.get("report_url") or "",
            "filename": Path(auto_report.get("report_path") or "report.pdf").name,
            "caption": "分割监测报告 (pdf)",
            "local_path": auto_report.get("report_path") or "",
        })
    if data.get("artifact_url"):
        candidates.append({
            "url": data.get("artifact_url") or "",
            "filename": Path(data.get("artifact_path") or "report.pdf").name,
            "caption": "分割监测报告 (pdf)",
            "local_path": data.get("artifact_path") or "",
        })
    if instruction.get("action") == "download" and params.get("url"):
        candidates.append(params)

    for item in candidates:
        url = str(item.get("url") or "")
        lowered = url.lower()
        if not url:
            continue
        if "/download/report/" not in lowered and ".pdf" not in lowered:
            continue
        filename = str(item.get("filename") or Path(url.split("?", 1)[0]).name or "report.pdf")
        return {
            "url": url,
            "filename": filename,
            "caption": str(item.get("caption") or "监测报告 (pdf)"),
            "local_path": str(item.get("local_path") or ""),
        }
    return None


def _find_latest_report_download(messages: List) -> Optional[Dict[str, str]]:
    """
    入参:
      - messages: 当前会话消息列表, 通常来自 agent_db.load_messages 或 build_context_messages。
    方法:
      - 从后往前扫描 ToolMessage, 解析 JSON 工具结果。
      - 返回最近一次可下载报告 URL, 使“下载这个报告”不再重新调用下载工具。
    出参:
      - dict: {url, filename, caption}; 未找到时返回 None。
    """
    for message in reversed(messages or []):
        if not isinstance(message, ToolMessage):
            continue
        try:
            payload = json.loads(message.content or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        download = _extract_report_download_from_payload(payload)
        if download:
            return download
    return None


def _find_latest_report_download_from_worker_tasks(
    conversation_id: str,
) -> Optional[Dict[str, str]]:
    """
    入参:
      - conversation_id: 当前对话 ID。
    方法:
      - 解耦后报告 URL 不再在 segment ToolMessage 里, 改由 report_agent worker 写入 ai_task.output。
      - 倒序查最近 report_agent 且 done 的 worker 任务, 再 get_task 取 output 校验 passed。
      - list_tasks 返回简表不含 output, 必须逐个 get_task 取详情。
    出参:
      - dict: {url, filename, caption, local_path, report_task_id}; 未找到返回 None。
    """
    candidates = agent_db.list_tasks(
        conversation_id=conversation_id,
        agent_role="report_agent",
        status="done",
        limit=10,
    )
    for meta in candidates or []:
        task = agent_db.get_task(meta.get("id")) or {}
        output = task.get("output") or {}
        if output.get("status") != "passed":
            continue
        artifacts = output.get("artifacts") or {}
        url = output.get("report_url") or artifacts.get("report_url") or ""
        if "/download/report/" not in url.lower() and ".pdf" not in url.lower():
            continue
        path = output.get("report_path") or artifacts.get("report_path") or ""
        return {
            "url": url,
            "filename": Path(path or url.split("?", 1)[0]).name or "report.pdf",
            "caption": "分割监测报告 (pdf)",
            "local_path": path,
            "report_task_id": meta.get("id"),
        }
    return None


async def _dispatch_segment_report_worker(
    tool_result: Dict[str, Any],
    parent_task_id: Optional[int],
    conversation_id: str,
    user_id: str,
    session_id: str,
    ws_manager: WebSocketManager,
) -> None:
    """
    入参:
      - tool_result: segment_image 的返回 (frontend_action render_image)。
      - parent_task_id: segment_image 的 ai_task 主键, 用于父子关联。
      - conversation_id / user_id / session_id: 当前会话上下文。
      - ws_manager: 用于 worker 完成后推送下载事件。
    方法:
      - 从 tool_result.data 抽 image_path/classes/polygon_layer/base_layer/vector_stats 组装 payload。
      - 调 task_manager.assign_agent_task 派发 report_agent 异步 worker (不阻塞 ReAct)。
      - 派发成功后起 _await_and_push_report_download 回调, worker 跑完推 download 事件。
      - 任一环节失败仅日志, 不影响已完成的分割结果。
    出参:
      - None。
    """
    import backend.agent.team.task_manager as task_manager
    from backend.agent.team.agent_roles import REPORT_AGENT

    data = tool_result.get("data") if isinstance(tool_result.get("data"), dict) else {}
    payload = {
        "task_type": "segmentation_auto_report",
        "image_path": data.get("image_path") or "",
        "classes": data.get("classes") or "",
        "polygon_layer": data.get("polygon_layer") or {},
        "base_layer": data.get("base_layer") or {},
        "vector_stats": data.get("vector_stats") or {},
    }
    try:
        task_id = await task_manager.assign_agent_task(
            conversation_id=conversation_id,
            user_id=user_id,
            agent_role=REPORT_AGENT,
            goal="分割完成后自动生成监测报告",
            payload=payload,
            parent_task_id=parent_task_id,
        )
    except Exception as exc:
        logger.warning(f"[ReportDispatch] 派发 report_agent 失败 conv={conversation_id[:8]}: {exc}")
        return
    if task_id is None:
        logger.warning(f"[ReportDispatch] report_agent 任务创建失败 conv={conversation_id[:8]} (agent_db 不可用?)")
        return
    logger.info(f"[ReportDispatch] 已派发 report_agent task={task_id} conv={conversation_id[:8]}")
    asyncio.create_task(_await_and_push_report_download(
        task_id=task_id,
        session_id=session_id,
        conversation_id=conversation_id,
        ws_manager=ws_manager,
    ))


async def _await_and_push_report_download(
    task_id: int,
    session_id: str,
    conversation_id: str,
    ws_manager: WebSocketManager,
) -> None:
    """
    入参:
      - task_id: report_agent worker 的 ai_task 主键。
      - session_id / conversation_id: 推送事件归属。
      - ws_manager: 推送 frontend_action/download。
    方法:
      - 等 worker asyncio task 跑完 (worker 内部已吞异常写 ai_task, await 不抛)。
      - 从 ai_task.output 取 report_url/report_path/overlay_image_path。
      - 仅 status==done 且 output.status==passed 时推 download 事件, 复用现有事件形态。
      - WS 断开或 worker 失败仅日志, 不影响已完成对话。
    出参:
      - None。
    """
    import backend.agent.team.task_manager as task_manager

    try:
        worker = task_manager.get_worker_task(task_id)
        if worker is not None:
            await worker
        task = agent_db.get_task(task_id) or {}
        output = task.get("output") or {}
        if task.get("status") != "done" or output.get("status") != "passed":
            logger.info(
                f"[ReportDispatch] report_agent task={task_id} 未成功 "
                f"(status={task.get('status')}, output_status={output.get('status')}), 不推送下载"
            )
            return
        artifacts = output.get("artifacts") or {}
        report_url = output.get("report_url") or artifacts.get("report_url") or ""
        report_path = output.get("report_path") or artifacts.get("report_path") or ""
        overlay_path = output.get("overlay_image_path") or artifacts.get("overlay_image_path") or ""
        if not report_url:
            logger.warning(f"[ReportDispatch] report_agent task={task_id} passed 但无 report_url")
            return
        await ws_manager.send_to_session(session_id, {
            "type": "frontend_action",
            "event_type": "download",
            "event_data": {
                "url": report_url,
                "filename": Path(report_path or "").name or "monitor_report.pdf",
                "caption": "分割监测报告 (pdf)",
                "local_path": report_path,
            },
            "description": "分割监测报告已生成, 正在触发下载",
            "request_id": f"report_download_{task_id}_{uuid.uuid4().hex[:6]}",
            "conversation_id": conversation_id,
            "report_task_id": task_id,
            "overlay_image_path": overlay_path,
        })
        logger.info(f"[ReportDispatch] report_agent task={task_id} 完成, 已推送下载 conv={conversation_id[:8]}")
    except Exception as exc:
        logger.warning(f"[ReportDispatch] 推送报告下载失败 task={task_id}: {exc}")


async def _async_memory_finale(
    conversation_id: str,
    user_id: str,
    all_messages: list,
    new_start_idx: int,
    session_id: str,
    ws_manager: WebSocketManager,
):
    """
    后台异步收尾: 短期摘要 (done 事件已发, 不阻塞用户).
    长期记忆提取已移至对话停止时 (tool_chat_ws CancelledError 分支).
    内部串行执行, 失败仅日志; 完成后推送 memory_updated 让前端弹轻提示.
    """
    fired_items = []
    try:
        # 1. 长期记忆 (偏好/事实提取): 与自纠捕捉器并行, 同一 done 触发点两路同时启动.
        #    enable_preference_extraction 默认 true (config.py), 与下方 _async_self_correction_scan
        #    各自沉淀: 本路偏好/事实 (preference/fact), 自纠路错误案例 (lesson/workflow).
        #    若需只保留错误案例学习, 设 ENABLE_PREFERENCE_EXTRACTION=false 单关本路.
        try:
            saved = await memory_mod.extract_and_save_long_term_memory(
                conversation_id, user_id, all_messages, new_start_idx
            )
            if saved:
                fired_items.append({
                    "key": "用户画像",
                    "value": f"已学习 {saved} 条偏好",
                    "category": "preference",
                    "source": "long_term",
                })
        except Exception as e:
            logger.error(f"[Memory] 异步长期记忆提取失败 conv={conversation_id}: {e}", exc_info=True)

        # 1. 短期记忆: 历史超阈值触发摘要
        try:
            summarized = await memory_mod.maybe_summarize(
                conversation_id, all_messages, new_start_idx
            )
            if summarized:
                fired_items.append({
                    "key": "会话摘要",
                    "value": "已自动总结早期对话",
                    "category": "summary",
                    "source": "summary",
                })
        except Exception as e:
            logger.error(f"[Memory] 异步摘要失败 conv={conversation_id}: {e}", exc_info=True)

        # 2. 推送 memory_updated (汇总本次后台学习的内容)
        if fired_items:
            await _emit_memory_updated(
                session_id, conversation_id, ws_manager, fired_items, "long_term"
            )
    except Exception as e:
        # 兜底: 整个后台任务异常也不影响已完成对话
        logger.error(f"[Memory] _async_memory_finale 异常 conv={conversation_id}: {e}", exc_info=True)


async def _async_self_correction_scan(
    conversation_id: str,
    user_id: str,
    all_messages: list,
    session_id: str,
    ws_manager: WebSocketManager,
):
    """自纠捕捉 (后台异步, 不阻塞用户).

    扫描本轮消息序列, 识别"工具失败 → Agent thinking 反思 → 修复重调成功"模式,
    把"失败原因 + 修复手段"沉淀为全局教训记忆 (category=lesson, user_id=global).

    下次任何用户/会话遇到同类问题, system_prompt 会注入这些教训, Agent 直接规避.

    设计:
      - fire-and-forget: done 已发, 这里纯后台跑, 失败仅日志
      - 不调 LLM: 用规则识别 (O(n) 扫消息), 实时且零成本
      - 去重: 同类错误 1 小时内只记一次, 避免刷库
      - 严重度过滤: 只记 high/medium (low 多为参数小错, 价值低)
    """
    try:
        from backend.agent.memory.self_correction import scan_and_save_corrections
        saved = await scan_and_save_corrections(all_messages, user_id=user_id)
        if saved:
            # 推送轻提示给前端 (让用户知道 Agent 学到了什么)
            items = [
                {
                    "key": f"教训: {s['tool_name']}",
                    "value": s["error_msg"][:80],
                    "category": "lesson",
                    "source": "self_correction",
                }
                for s in saved
            ]
            await _emit_memory_updated(
                session_id, conversation_id, ws_manager, items, "lesson"
            )
            logger.info(
                f"[SelfCorrection] conv={conversation_id} 沉淀 {len(saved)} 条教训"
            )
    except Exception as e:
        logger.warning(f"[SelfCorrection] 自纠扫描失败 conv={conversation_id}: {e}")


# ==================== LLM 异常分类路由 (P0-1) ====================

def _classify_llm_error(exc: Exception) -> str:
    """
    ★ P0-1: 把 LLM 调用抛出的异常归为三类, 驱动不同的恢复策略。

    入参:
        - exc: LLM.astream / ainvoke 抛出的任意 Exception
    方法:
        DashScope (qwen-plus 走 openai 兼容协议) 把"上下文超限"归到 BadRequestError,
        无法纯靠异常类型识别, 必须 isinstance + message/code 关键词双判定。
        - token_exceed: BadRequestError 且 message/code 命中 context 超限关键词
        - rate_limit:  RateLimitError (429)
        - others:      超时 / 连接 / 认证 / 其他 BadRequestError / 未知
    出参: "token_exceed" | "rate_limit" | "others"
    """
    try:
        from openai import RateLimitError, BadRequestError
    except ImportError:
        # openai SDK 不可用 → 无法分类, 一律走 others (仍可重试)
        return "others"

    msg = str(exc).lower()
    code = str(getattr(exc, "code", "") or "").lower()
    token_signals = [
        "context_length_exceeded", "maximum context length", "context window",
        "token limit", "上下文长度", "prompt is too long", "input too long",
    ]
    # token 超限: 必须双重判定 (其他 BadRequestError 如参数错误不该走压缩)
    if isinstance(exc, BadRequestError):
        if any(sig in msg or sig in code for sig in token_signals):
            return "token_exceed"
        return "others"
    if isinstance(exc, RateLimitError):
        return "rate_limit"
    return "others"


class AgentChatService:
    """
    Agent 聊天服务 - 核心引擎

    两种工作模式:
      1. SSE 模式 (tool_chat_stream): 单向流式, 前端操作不阻塞后端
      2. WebSocket 模式 (tool_chat_ws): 全双工, 后端可等待前端操作结果

    ★ v2.1: 多对话并行 — 每条消息携带 conversation_id, 同一 WebSocket 连接可
      同时运行多个对话的推理任务。每个任务独立拥有自己的上下文和状态。

    记忆/上下文: 不再自维护历史 dict, 全部委托给 agent.memory 模块。
    - memory.build_context_messages(): 从 agent_db 加载历史 + trim 裁剪
    - memory.persist_turn(): 收尾时持久化本轮新消息 (含 tool_calls / ToolMessage)
    - agent_db 不可用时, memory 自动降级到内存 dict, 行为同原始实现
    """

    def __init__(self):
        # 历史管理已下沉到 memory.py + agent_db.py, 此处无需自维护
        pass

    async def _stream_llm_thinking_ws(
        self,
        llm,
        all_messages: List,
        session_id: str,
        conversation_id: str,
        ws_manager: WebSocketManager,
    ) -> AIMessage:
        """
        流式调用 LLM 并实时推送 thinking token (WebSocket 模式)
        - 入参: llm 实例、消息列表、session_id、conversation_id、ws_manager
        - 方法: astream 逐 chunk 推送 thinking 事件
        - 出参: 累积完成的 AIMessage (含 content + tool_calls)

        流式处理逻辑:
          LLM 的 astream 会按 token 生成 AIMessageChunk:
          - 每个 chunk 含 content (文本片段) 或 tool_call_chunks (工具调用片段)
          - 将所有 chunk 累积, 最终合并为完整的 AIMessage
          - 在累积过程中, 每个 content chunk 都作为 thinking 推送到前端
        """
        collected_chunks: List[AIMessageChunk] = []

        async for chunk in llm.astream(all_messages):
            collected_chunks.append(chunk)

            # 推送 thinking token (LLM 当前生成的文本片段)
            if chunk.content:
                await ws_manager.send_to_session(session_id, {
                    "type": "thinking",
                    "content": chunk.content,
                    "conversation_id": conversation_id,  # ★ 多对话路由
                })

            # ★ 每个 chunk 之间显式检查是否被取消 (httpx 底层可能不检测)
            await asyncio.sleep(0)

        # 将所有 chunk 合并为一条完整的 AIMessage
        if not collected_chunks:
            return AIMessage(content="")

        full_message = collected_chunks[0]
        for chunk in collected_chunks[1:]:
            full_message = full_message + chunk

        # ★ 显式缓存命中率日志 (stream_usage 开启时, 最后一个 chunk 携带 usage_metadata)
        if settings.enable_context_cache and settings.log_cache_hit_rate:
            um = getattr(full_message, "usage_metadata", None)
            if um:
                cached = (um.get("input_token_details") or {}).get("cache_read", 0)
                total_in = um.get("input_tokens", 0)
                rate = cached / total_in * 100 if total_in else 0
                logger.info(
                    f"[Cache] conv={conversation_id} hit={cached}/{total_in} ({rate:.0f}%) "
                    f"output={um.get('output_tokens', 0)}"
                )

        return AIMessage(
            content=full_message.content,
            tool_calls=full_message.tool_calls,
            id=full_message.id,
            additional_kwargs={
                **(getattr(full_message, "additional_kwargs", {}) or {}),
                "thinking_content": full_message.content or "",
            },
        )

    # ==================== WebSocket 模式 ====================

    async def _force_compress_and_retrim(
        self,
        conversation_id: str,
        all_messages: List,
        llm,
        new_start_idx: int,
    ):
        """
        ★ P0-1: token_exceed 时的紧急上下文压缩 + 重载。

        入参:
            - conversation_id: 会话 id
            - all_messages: 外层消息列表引用 (会被原地替换内容)
            - llm: LLM 实例 (build_context_messages 形参需要, 实际未使用)
            - new_start_idx: 外层当前的本轮起点 index
        方法:
            ① 记录压缩前 token;
            ② maybe_summarize(force_level2=True) 强制 Level 2 LLM 摘要 (删旧消息);
            ③ build_context_messages 重新从 DB 装载已摘要后的更短历史;
            ④ 校验 after < before × target_ratio, 降不够则判失败 (防无限重试);
            ⑤ all_messages.clear()+extend() 原地替换, 保持外层引用有效。
        出参: (压缩是否成功, 新的 new_start_idx) — 失败返回 (False, None)
        """
        before_tokens = memory_mod._count_message_tokens(all_messages)
        try:
            ok = await memory_mod.maybe_summarize(
                conversation_id, all_messages, new_start_idx, force_level2=True
            )
        except Exception as e:
            logger.error(f"[WS] 强制压缩异常 conv={conversation_id}: {e}")
            return False, None
        if not ok:
            logger.warning(f"[WS] 强制压缩未触发 (消息不足或摘要失败) conv={conversation_id}")
            return False, None

        # 重新装载: 取本轮用户输入 (all_messages 最后一条 HumanMessage 的 content)
        current_human_content = ""
        for m in reversed(all_messages):
            content = getattr(m, "content", None)
            if isinstance(content, str) and content:
                current_human_content = content
                break
        try:
            new_msgs, new_idx = memory_mod.build_context_messages(
                conversation_id, current_human_content, llm
            )
        except Exception as e:
            logger.error(f"[WS] 压缩后重载上下文失败 conv={conversation_id}: {e}")
            return False, None

        after_tokens = memory_mod._count_message_tokens(new_msgs)
        if after_tokens >= before_tokens * settings.llm_force_compress_target_ratio:
            logger.error(
                f"[WS] 强制压缩未有效降 token conv={conversation_id}: "
                f"{before_tokens} -> {after_tokens} (需低于 {before_tokens * settings.llm_force_compress_target_ratio:.0f})"
            )
            return False, None

        # ★ 原地替换: 保持外层 all_messages 引用有效 (clear+extend 而非重新赋值)
        all_messages.clear()
        all_messages.extend(new_msgs)
        logger.info(
            f"[WS] 强制压缩成功 conv={conversation_id}: {before_tokens} -> {after_tokens} "
            f"new_start_idx={new_idx}"
        )
        return True, new_idx

    async def tool_chat_ws(
        self,
        req: ToolChatRequest,
        session_id: str,
        ws_manager: WebSocketManager,
        checkpoint_id: int = None,  # ★ 本轮开始前的最后一条消息 id (停止回滚用)
    ):
        """
        WebSocket 全双工处理

        ★ v2.1 多对话并行:
          - 所有下行消息携带 conversation_id
          - CancelledError 不再向上传播 (避免影响同 session 其他对话)
          - 任务完成后自动从 ws_manager 清理

        与 SSE 模式的关键区别:
          1. thinking token 直接实时推送 (无需先收集再输出)
          2. 当工具返回 frontend_action 时, 后端通过 ws_manager.wait_for_frontend_result()
             阻塞等待前端执行结果, 实现真正的双向交互
          3. 支持 asyncio.CancelledError → 回滚到 checkpoint → 推送 chat_stopped
        """
        conversation_id = req.conversation_id or str(uuid.uuid4())

        # ★ 注入运行时上下文 (供 memory_tools / sandbox_tools 读取当前 conv_id / user_id / project_id)
        # ContextVar 在 asyncio 任务间隔离, 多对话并行不会串号
        # ★ project_id 优先用 req 传入, 没有则从 DB 查 (续接会话场景)
        from backend.agent.runtime.context_vars import set_runtime_context
        _project_id = req.project_id
        if not _project_id:
            conv_meta = agent_db.get_conversation(conversation_id)
            if conv_meta:
                _project_id = conv_meta.get("project_id") or ""
        set_runtime_context(conversation_id, req.user_id, _project_id or "")

        try:
            # 获取工具并绑定 LLM
            tools = get_tools_for_llm()
            llm = create_llm_with_tools(tools, req.model, req.temperature)
            llm_without_tools = create_llm(req.model, req.temperature)
            disable_tools_next_turn = False
            pending_repair_audit = None
            pending_repair_attempt = None
            current_plan, plan_task_id = load_plan_state(conversation_id)
            if current_plan and current_plan.get("status") == "completed":
                current_plan, plan_task_id = None, None
            accepted_lessons = parse_accepted_lesson_contracts(
                agent_db.load_user_memory("global", category="lesson")
            )
            # ★ 本轮聊天图片收集: chat_image action 产生的图片, 待最终回复落库时挂到最后一条 assistant 消息
            _turn_images = []
            uploaded_images = _normalize_chat_images(req.images)
            selected_layer_images = await _download_selected_layers_for_chat(
                req.selected_layers,
                conversation_id,
            )
            input_images = uploaded_images + selected_layer_images

            # ★ 构建上下文: agent_db 历史载入 + trim 裁剪 + 当前 Human 拼装
            all_messages, new_start_idx = memory_mod.build_context_messages(
                conversation_id,
                req.prompt,
                llm,
                user_id=req.user_id,
                images=input_images,
            )

            # ★ v2.3: 立即持久化用户消息, 防止停止时丢失上下文
            #   原架构中 HumanMessage 与 AI/Tool 在 persist_turn 统一落库,
            #   但用户中途停止 → CancelledError → 回滚 → HumanMessage 未入库 → 丢失。
            #   现在提前落库, 停止回滚只删 AI/Tool 消息, 用户请求不丢失。
            memory_mod.persist_user_message(conversation_id, all_messages, new_start_idx)
            # checkpoint 重新获取: 现在包含了刚持久化的 HumanMessage,
            # 停止回滚时 HumanMessage 会被保留, 用户说"继续"能识别上下文
            checkpoint_id = agent_db.get_last_message_id(conversation_id)

            existing_report_download = _find_latest_report_download(all_messages)
            if not existing_report_download:
                existing_report_download = _find_latest_report_download_from_worker_tasks(conversation_id)
            if _is_report_download_request(req.prompt) and not existing_report_download:
                existing_report_download = _find_latest_report_download(
                    agent_db.load_messages(conversation_id, limit=50)
                )
                if not existing_report_download:
                    existing_report_download = _find_latest_report_download_from_worker_tasks(conversation_id)
            if _is_report_download_request(req.prompt) and existing_report_download:
                await ws_manager.send_to_session(session_id, {
                    "type": "frontend_action",
                    "event_type": "download",
                    "event_data": existing_report_download,
                    "description": "正在下载已有监测报告",
                    "request_id": f"report_download_{uuid.uuid4().hex[:8]}",
                    "conversation_id": conversation_id,
                })
                content = (
                    "已找到刚才生成的监测报告，已触发下载。\n\n"
                    f"- 报告文件: {existing_report_download.get('filename')}\n"
                    f"- 本地路径: {existing_report_download.get('local_path') or '未记录'}\n"
                    f"- 下载入口: {existing_report_download.get('url')}"
                )
                for i in range(0, len(content), 5):
                    await ws_manager.send_to_session(session_id, {
                        "type": "content",
                        "content": content[i : i + 5],
                        "conversation_id": conversation_id,
                    })
                    await asyncio.sleep(0.02)
                all_messages.append(AIMessage(
                    content=content,
                    additional_kwargs={"thinking_content": content},
                ))
                memory_mod.persist_turn(conversation_id, all_messages, new_start_idx + 1)
                agent_db.update_conversation_status(conversation_id, "completed")
                await ws_manager.send_to_session(session_id, {
                    "type": "done",
                    "conversation_id": conversation_id,
                })
                return

            # 阶段 2: Agent 推理循环 (无限循环: 仅在 LLM 给出最终回复 / 调用失败 / 用户停止时退出)
            # ★ 不再设工具调用轮次上限 — 复杂任务不再被强制截断, 模型可自行多轮调用直到收敛给出最终回复。
            #   安全出口: ① LLM 返回无 tool_calls 的最终回复 → 正常 done; ② LLM 调用异常 → return;
            #   ③ 用户停止 → asyncio.CancelledError → 回滚。死循环时由用户手动停止兜底。
            #   iteration 仅用于日志计数 (无上限), 不再作为终止条件。
            iteration = 0
            while True:
                iteration += 1
                logger.info(f"[WS] Iteration {iteration}, conv={conversation_id}")

                # ★ P0-1: LLM 调用分类重试 — 单次失败不再终止整轮。
                #   token_exceed → 强制压缩历史后重试; rate_limit → 退避后重试;
                #   others (超时/连接/未知) → 立即重试。四道防无限重试闸:
                #   ① llm_retry_count 上限; ② 压缩后 token 须降到 85% 以下;
                #   ③ CancelledError 单独透传; ④ compressed_once 限制压缩只做一次。
                #   重试在同一 iteration 内, 不 increment iteration、不碰 checkpoint。
                llm_retry_count = 0
                compressed_once = False
                while True:
                    try:
                        # ★ 流式调用 LLM, 每个 thinking token 实时推送到前端
                        active_llm = llm_without_tools if disable_tools_next_turn else llm
                        disable_tools_next_turn = False
                        ai_message = await self._stream_llm_thinking_ws(
                            active_llm, all_messages, session_id, conversation_id, ws_manager
                        )
                        if pending_repair_audit:
                            actual_decision = extract_actual_decision(ai_message)
                            audit = audit_repair_plan_following(
                                case_id=f"{conversation_id}:{iteration}",
                                repair_plan=pending_repair_audit.get("repair_plan") or {},
                                actual_decision=actual_decision,
                                expected_terms=pending_repair_audit.get("expected_terms") or [],
                                forbidden_terms=pending_repair_audit.get("forbidden_terms") or [],
                            )
                            append_jsonl(Path("logs") / "agent-runtime-audit.jsonl", {
                                "conversation_id": conversation_id,
                                "iteration": iteration,
                                "source_tool": pending_repair_audit.get("source_tool") or "",
                                "audit": audit,
                            })
                            if actual_decision.get("has_tool_call"):
                                pending_repair_attempt = build_repair_attempt(
                                    conversation_id=conversation_id,
                                    iteration=iteration,
                                    source_tool=pending_repair_audit.get("source_tool") or "",
                                    repair_plan=pending_repair_audit.get("repair_plan") or {},
                                    audit=audit,
                                )
                            pending_repair_audit = None
                        break  # 成功, 跳出重试循环, 继续外层推理
                    except asyncio.CancelledError:
                        raise  # ★ 用户停止必须透传, 绝不吞 (否则停止回滚失效)
                    except Exception as e:
                        err_class = _classify_llm_error(e)
                        llm_retry_count += 1
                        logger.warning(
                            f"[WS] LLM 调用失败 conv={conversation_id} iter={iteration} "
                            f"class={err_class} retry={llm_retry_count}/{settings.llm_retry_max}: {e}"
                        )
                        if llm_retry_count > settings.llm_retry_max:
                            await ws_manager.send_to_session(session_id, {
                                "type": "error",
                                "content": f"LLM 调用连续失败 ({err_class}), 已重试 {settings.llm_retry_max} 次: {str(e)}",
                                "conversation_id": conversation_id,
                            })
                            return

                        if err_class == "token_exceed":
                            if compressed_once:
                                # 已压缩过仍超限 → 放弃, 防反复压缩
                                await ws_manager.send_to_session(session_id, {
                                    "type": "error",
                                    "content": f"上下文超限且压缩后仍超限, 请开启新会话: {str(e)}",
                                    "conversation_id": conversation_id,
                                })
                                return
                            await ws_manager.send_to_session(session_id, {
                                "type": "thinking",
                                "content": "⏳ 上下文过长, 正在压缩历史…",
                                "conversation_id": conversation_id,
                            })
                            compress_ok, new_idx = await self._force_compress_and_retrim(
                                conversation_id, all_messages, llm, new_start_idx
                            )
                            if not compress_ok:
                                await ws_manager.send_to_session(session_id, {
                                    "type": "error",
                                    "content": f"上下文超限且自动压缩失败, 请开启新会话: {str(e)}",
                                    "conversation_id": conversation_id,
                                })
                                return
                            new_start_idx = new_idx  # ★ 更新外层起点, 防 persist_turn 范围错位
                            compressed_once = True
                            # 继续重试循环 (不 sleep)
                        elif err_class == "rate_limit":
                            await ws_manager.send_to_session(session_id, {
                                "type": "thinking",
                                "content": f"⏳ 服务繁忙, {settings.llm_retry_rate_limit_sleep}s 后重试…",
                                "conversation_id": conversation_id,
                            })
                            await asyncio.sleep(settings.llm_retry_rate_limit_sleep)
                        else:  # others: 超时/连接/认证/未知 → 立即重试
                            await ws_manager.send_to_session(session_id, {
                                "type": "thinking",
                                "content": f"⏳ 网络异常, 重试中 ({llm_retry_count}/{settings.llm_retry_max})…",
                                "conversation_id": conversation_id,
                            })

                # 无工具调用 → thinking 内容即为最终回复
                if not ai_message.tool_calls:
                    content = ai_message.content or ""
                    plan_allowed, plan_reason = can_finalize_plan(current_plan)
                    blocked_step = next(
                        (
                            item for item in (current_plan or {}).get("steps", [])
                            if item.get("required", True) and item.get("status") == "blocked"
                        ),
                        None,
                    )
                    next_action = ((blocked_step or {}).get("repair_plan") or {}).get("next_action") or ""
                    waiting_for_user = next_action.startswith("request_")
                    if not plan_allowed and not waiting_for_user:
                        all_messages.append(AIMessage(
                            content=content,
                            additional_kwargs={"thinking_content": ai_message.content or content},
                        ))
                        all_messages.append(SystemMessage(content=(
                            f"显式计划完成门已拦截本次收尾: {plan_reason}。"
                            "不得向用户宣告完成；立即执行当前步骤的 next_action 或 repair_plan，"
                            "并在获得 agent_validation.status=passed 后再收尾。\n"
                            f"{build_plan_context_text(current_plan)}"
                        )))
                        continue
                    if content:
                        for i in range(0, len(content), 5):
                            await ws_manager.send_to_session(session_id, {
                                "type": "content",
                                "content": content[i : i + 5],
                                "conversation_id": conversation_id,
                            })
                            await asyncio.sleep(0.02)

                    # ★ 持久化本轮新消息: HumanMessage 已提前落库, 这里只存 AIMessage(最终回复)
                    all_messages.append(AIMessage(
                        content=content,
                        additional_kwargs={"thinking_content": ai_message.content or content},
                    ))
                    memory_mod.persist_turn(conversation_id, all_messages, new_start_idx + 1)
                    # ★ 聊天图片持久化: 把本轮 chat_image 图片挂到刚落库的最终 assistant 消息, 刷新后回填
                    if _turn_images:
                        agent_db.set_last_assistant_images(conversation_id, _turn_images)
                    # ★ 标记完成 + 立即发 done (不等记忆/摘要, 降低首字节延迟)
                    conversation_status = "completed" if plan_allowed else "stopped"
                    agent_db.update_conversation_status(conversation_id, conversation_status)
                    await ws_manager.send_to_session(session_id, {
                        "type": "done",
                        "conversation_id": conversation_id,
                    })
                    # ★ 记忆/摘要异步化: done 已发出, 这两步在后台跑, 完成后发 memory_updated
                    #   fire-and-forget, 失败仅日志, 不影响已完成的对话
                    asyncio.create_task(_async_memory_finale(
                        conversation_id, req.user_id, list(all_messages), new_start_idx,
                        session_id, ws_manager,
                    ))
                    # ★ 自纠捕捉: 扫描本轮消息, 识别"犯错→自纠"模式, 沉淀全局教训
                    #   fire-and-forget, 不阻塞用户, 失败仅日志
                    asyncio.create_task(_async_self_correction_scan(
                        conversation_id, req.user_id, list(all_messages),
                        session_id, ws_manager,
                    ))
                    return

                # 有工具调用 → 逐个执行
                all_messages.append(ai_message)

                # ★ 工具分类映射 (P3 任务持久化): 用于判断当前工具是否"重工具",
                #   重工具(samseg/rule/report/preprocess) 需开 ai_task 记录, 轻工具(memory/skill/database) 不开
                _tool_cat_map = get_tool_category_map()

                for tool_call in ai_message.tool_calls:
                    # ★ 每个工具调用前检查取消
                    await asyncio.sleep(0)
                    tool_name = tool_call["name"]
                    tool_args = tool_call["args"]
                    tool_call_id = tool_call.get("id", str(uuid.uuid4()))
                    if current_plan is None:
                        current_plan = create_plan_state(conversation_id, req.prompt)
                    active_plan_step = start_tool_step(current_plan, tool_name, tool_args)
                    plan_task_id = persist_plan_state(
                        conversation_id, req.user_id, current_plan, plan_task_id
                    )

                    # 通知前端
                    await ws_manager.send_to_session(session_id, {
                        "type": "tool_call",
                        "tool_calls": [{
                            "id": tool_call_id,
                            "tool_call_id": tool_call_id,
                            "name": tool_name,
                            "args": tool_args,
                        }],
                        "conversation_id": conversation_id,
                    })

                    # ★ P3 任务持久化: 重工具开任务记录
                    #   - 输入参数做脱敏: 路径只保留 basename, 避免 agent-files 绝对路径污染日志
                    _task_id = None
                    _task_started_at = None
                    _is_heavy = _tool_cat_map.get(tool_name) in agent_db.HEAVY_TOOL_CATEGORIES
                    if _is_heavy:
                        import time as _time
                        _task_started_at = _time.time()
                        _safe_input = agent_db.create_task(
                            conversation_id=conversation_id,
                            task_type=tool_name,
                            tool_name=tool_name,
                            user_id=req.user_id,
                            input_data=_sanitize_task_input(tool_args),
                        )
                        if _task_id is None:
                            _task_id = _safe_input
                        agent_db.update_task(_task_id, status="running", progress=10)
                        agent_db.append_task_log(_task_id, f"开始执行工具: {tool_name}")

                    # 执行工具
                    # ★ 工具分两类:
                    #   1. 同步阻塞工具 (DB/GeoServer/torch, 耗时数秒~数十秒) → 线程池, 不阻塞 event loop
                    #   2. 异步工具 (沙盒 docker exec) → 直接在 event loop 上 await ainvoke
                    tool_func = get_tool_by_name(tool_name)
                    if not tool_func:
                        tool_result = {"type": "error", "msg": f"未知工具: {tool_name}"}
                    else:
                        try:
                            await asyncio.sleep(0)
                            # ★ 检测工具底层函数是否为 async coroutine
                            #   LangChain @tool 包装后, 原函数存在 .coroutine (async) 或 .func (sync)
                            import inspect as _inspect
                            _underlying = getattr(tool_func, 'coroutine', None) or getattr(tool_func, 'func', None)
                            invoke_args = dict(tool_args)
                            if tool_name == "lookup_skill":
                                invoke_args["accepted_lessons"] = accepted_lessons
                                invoke_args["current_plan"] = current_plan
                            if _underlying and _inspect.iscoroutinefunction(_underlying):
                                # 异步工具 (沙盒) → event loop 上 await, 支持 CancelledError 响应
                                tool_result = await tool_func.ainvoke(invoke_args)
                            else:
                                # 同步工具 → 线程池, 不阻塞 event loop
                                tool_result = await asyncio.to_thread(tool_func.invoke, invoke_args)
                        except asyncio.CancelledError:
                            # ★ 工具执行中被取消 → 标记任务 cancelled 后向上抛, 走 CancelledError 分支回滚
                            if _task_id is not None:
                                agent_db.update_task(_task_id, status="cancelled", error="用户取消")
                                agent_db.append_task_log(_task_id, "任务被用户取消", level="warning")
                            raise
                        except Exception as e:
                            # ★ str(e) 可能为空 (如 Exception() 无参构造),
                            #   导致前端/AI 看到 {"type":"error","msg":""} 无法定位问题
                            err_msg = str(e) or type(e).__name__
                            # ★ P1-4: 结构化 error 信号, 喂养 self_correction 自纠闭环
                            #   - 把异常类名拼进 msg: PermissionError/FileNotFoundError/TimeoutError 等
                            #     命中 _assess_severity 的 high 关键词 (permission/timeout/file...),
                            #     否则纯报错文本被判 low 而被自纠学习丢弃
                            #   - 去重: 若 err_msg 已以类名开头 (如 "FileNotFoundError: ...") 则不重复拼
                            _err_type = type(e).__name__
                            if not err_msg.startswith(_err_type):
                                err_msg = f"[{_err_type}] {err_msg}"
                            tool_result = {
                                "type": "error",
                                "msg": err_msg,
                                "error_type": _err_type,
                                "tool": tool_name,
                            }

                    # ★ P3: 重工具收尾 — 按成败更新任务状态 + 记录耗时
                    if _task_id is not None:
                        import time as _time
                        _elapsed_ms = int((_time.time() - _task_started_at) * 1000) if _task_started_at else None
                        _is_err = isinstance(tool_result, dict) and tool_result.get("type") == "error"
                        if _is_err:
                            agent_db.update_task(
                                _task_id, status="failed", progress=100,
                                error=(tool_result.get("msg") or "")[:500],
                            )
                            agent_db.append_task_log(
                                _task_id, f"工具执行失败: {tool_result.get('msg', '')}",
                                level="error", elapsed_ms=_elapsed_ms,
                            )
                        else:
                            _out_summary = _summarize_task_output(tool_result)
                            agent_db.update_task(
                                _task_id, status="done", progress=100, output=_out_summary,
                            )
                            agent_db.append_task_log(
                                _task_id, f"工具执行完成", level="info", elapsed_ms=_elapsed_ms,
                            )

                    # ★ 重复操作偏好识别: 工具成功执行后, 同步记录到内存计数器 (O(1), 不阻塞)
                    #   达阈值(3次)会返回偏好条目, 留待 _async_memory_finale 落库
                    if isinstance(tool_result, dict) and tool_result.get("type") not in ("error",):
                        try:
                            pref = pattern_tracker.record(req.user_id, tool_name, tool_args)
                            if pref:
                                # 即时落库 (不等收尾), 用户能更快感知
                                agent_db.save_user_memory(
                                    req.user_id, pref["key"], pref["value"], pref["category"]
                                )
                                # 通知前端弹轻提示
                                asyncio.create_task(_emit_memory_updated(
                                    session_id, conversation_id, ws_manager, [pref], "pattern"
                                ))
                        except Exception as pt_err:
                            logger.warning(f"[PatternTracker] record 失败: {pt_err}")

                    # ★ frontend_action: 推送指令给前端执行 (无论是否阻塞等待)
                    #   旧实现把"推送"和"等待"耦合在 wait_for_result 为真的同一 if 块里,
                    #   导致 samseg 等设了 wait_for_result=False 的工具完全不推送,
                    #   前端永远收不到 render_image 指令 → 面板空白但 LLM 却汇报"已完成"。
                    #   现在拆开: 推送是无条件的, 阻塞等待才是可选的。
                    if tool_result.get("type") == "frontend_action":
                        request_id = tool_call_id
                        instr_type = tool_result["instruction"]["type"]
                        # 发送前端操作指令
                        await ws_manager.send_to_session(session_id, {
                            "type": "frontend_action",
                            "event_type": instr_type,
                            "event_data": tool_result["instruction"]["params"],
                            "description": tool_result.get("description", ""),
                            "request_id": request_id,
                            "conversation_id": conversation_id,
                        })
                        # ★ 聊天图片: 收集本轮 chat_image 产物, 待最终回复持久化时挂到最后一条 assistant 消息
                        if instr_type == "chat_image":
                            _img_params = tool_result["instruction"]["params"] or {}
                            if _img_params.get("image_url"):
                                _turn_images.append({
                                    "url": _img_params["image_url"],
                                    "caption": _img_params.get("caption", ""),
                                })
                        # ★ 仅当显式要求等待时, 才阻塞等前端返回 tool_result
                        if tool_result.get("wait_for_result"):
                            frontend_result = await ws_manager.wait_for_frontend_result(request_id)
                            logger.info(f"[WS] Frontend result: {frontend_result}")
                            tool_result["frontend_result"] = frontend_result

                    # ★ B2 解耦: segment_image 成功后异步派发 report_agent (代码派发, 不走 LLM)。
                    #   report 成为独立 worker 任务卡, 不阻塞 segment 返回, 完成后由回调推 download 事件。
                    if tool_name == "segment_image" and isinstance(tool_result, dict) \
                            and tool_result.get("type") == "frontend_action":
                        asyncio.create_task(_dispatch_segment_report_worker(
                            tool_result=tool_result,
                            parent_task_id=_task_id,
                            conversation_id=conversation_id,
                            user_id=req.user_id,
                            session_id=session_id,
                            ws_manager=ws_manager,
                        ))

                    # ★ Observation 构建层: 工具结果回灌 LLM 前统一完成 verification 兜底、
                    #   verification_agent 验收、repair_plan 注入和 ToolMessage 构造。
                    tool_message, tool_result = build_observation_tool_message(
                        tool_name=tool_name,
                        tool_call_id=tool_call_id,
                        tool_result=tool_result,
                        user_goal=req.prompt,
                    )
                    apply_step_observation(
                        current_plan,
                        active_plan_step["step_id"],
                        tool_name,
                        tool_result if isinstance(tool_result, dict) else {},
                    )
                    if tool_name == "lookup_skill" and isinstance(tool_result, dict):
                        skill_data = tool_result.get("data")
                        skill_data = skill_data if isinstance(skill_data, dict) else {}
                        apply_skill_selection_to_plan(
                            current_plan,
                            skill_data.get("selection") or {},
                            skill_data.get("context_inputs") or {},
                        )
                    plan_task_id = persist_plan_state(
                        conversation_id, req.user_id, current_plan, plan_task_id
                    )
                    all_messages.append(tool_message)
                    all_messages.append(SystemMessage(content=build_plan_context_text(current_plan)))
                    await ws_manager.send_to_session(session_id, {
                        "type": "tool_result",
                        "tool_call_id": tool_call_id,
                        "name": tool_name,
                        "content": tool_message.content or "",
                        "status": (
                            tool_result.get("type")
                            if isinstance(tool_result, dict) and tool_result.get("type") == "error"
                            else "done"
                        ),
                        "conversation_id": conversation_id,
                    })
                    if pending_repair_attempt:
                        repair_success = evaluate_repair_success(
                            pending_repair_attempt,
                            repaired_tool_name=tool_name,
                            repaired_tool_result=tool_result if isinstance(tool_result, dict) else {},
                        )
                        append_jsonl(Path("logs") / "agent-repair-success.jsonl", repair_success)
                        pending_repair_attempt = None
                    repair_followup = build_repair_followup_system_message(tool_result)
                    if repair_followup:
                        all_messages.append(repair_followup)
                    disable_tools_next_turn = should_disable_tools_for_next_repair_turn(tool_result)
                    validation = tool_result.get("agent_validation") if isinstance(tool_result, dict) else None
                    if isinstance(validation, dict) and validation.get("status") in {"failed", "warning"}:
                        pending_repair_audit = {
                            "source_tool": tool_name,
                            "repair_plan": validation.get("repair_plan") or {},
                        }

        except asyncio.CancelledError:
            # ★ v2.1: 被用户停止 → 回滚到 checkpoint, 不向上传播 (避免影响同 session 其他对话)
            logger.info(f"[WS] Task cancelled by user: conv={conversation_id}, checkpoint={checkpoint_id}")
            if checkpoint_id is not None:
                deleted = agent_db.delete_messages_after(conversation_id, checkpoint_id)
                logger.info(f"[WS] Rollback: deleted {deleted} messages after id={checkpoint_id}")
            else:
                # 全新会话且无任何已保存消息 → 删除空的会话记录
                agent_db.delete_conversation(conversation_id)
                logger.info(f"[WS] Rollback: deleted empty conversation {conversation_id}")
                # ★ 会话级沙盒回收: 空会话被删, 异步停止其沙盒容器 (若有)。
                #   全新会话首轮取消一般还没建容器, 但为对称仍挂回收; best-effort, 不阻塞回滚。
                try:
                    from backend.model.tools.sandbox_manager import sandbox_manager
                    asyncio.create_task(
                        sandbox_manager.cleanup_by_conversation(conversation_id, _project_id or "")
                    )
                except Exception as sb_err:
                    logger.warning(f"[WS] 沙盒回收调度失败 conv={conversation_id}: {sb_err}")

            agent_db.update_conversation_status(conversation_id, "stopped")

            # ★ 对话结束时提取一次长期记忆 (全量分析; 取代每轮提取, 降低 LLM 调用成本)
            #   与下面 _async_self_correction_scan 并行两路: 本路偏好/事实, 自纠路错误案例.
            #   enable_preference_extraction 默认 true; 设 false 时本路跳过, 只留错误案例学习.
            if checkpoint_id is not None:
                try:
                    saved = await memory_mod.extract_and_save_long_term_memory(
                        conversation_id, req.user_id, all_messages, 0
                    )
                    if saved:
                        await _emit_memory_updated(
                            session_id, conversation_id, ws_manager,
                            [{"key": "用户画像", "value": f"已学习 {saved} 条偏好", "category": "preference", "source": "long_term"}],
                            "long_term"
                        )
                except Exception as e:
                    logger.error(f"[Memory] 对话结束长期记忆提取失败 conv={conversation_id}: {e}", exc_info=True)
                # ★ 自纠捕捉: 中断停止也要扫描 (用户可能在 Agent 自纠过程中停止)
                try:
                    asyncio.create_task(_async_self_correction_scan(
                        conversation_id, req.user_id, list(all_messages),
                        session_id, ws_manager,
                    ))
                except Exception as sc_err:
                    logger.warning(f"[SelfCorrection] 中断扫描失败 conv={conversation_id}: {sc_err}")
            # ★ v2.1: 不再 raise, 让函数正常返回 (不影响同 session 其他对话)

        except Exception as e:
            # ★ 捕获其他未预期的错误, 通知前端
            logger.error(f"[WS] Unexpected error in tool_chat_ws: conv={conversation_id}, err={e}", exc_info=True)
            try:
                await ws_manager.send_to_session(session_id, {
                    "type": "error",
                    "content": f"对话处理异常: {str(e)}",
                    "conversation_id": conversation_id,
                })
            except Exception:
                pass

        finally:
            # ★ 清理: 从 ws_manager 移除已完成/已取消的任务注册
            if session_id in ws_manager.active_tasks:
                ws_manager.active_tasks[session_id].pop(conversation_id, None)
                if not ws_manager.active_tasks[session_id]:
                    del ws_manager.active_tasks[session_id]
            logger.info(f"[WS] Task cleaned up: session={session_id}, conv={conversation_id}")


