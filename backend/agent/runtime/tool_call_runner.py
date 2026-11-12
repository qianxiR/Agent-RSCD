# -*- coding: utf-8 -*-
"""工具调用执行器 (Agent 层 / Runtime)

把"工具调用"从聊天主循环中抽出，供自由 ReAct 与 Plan-and-Execute 共用：
- 注册表查找、async/sync 分派、异常规范化；
- 重工具 ai_task 记录与耗时/资源采集；
- frontend_action 推送与可选等待；
- 成功后的回调（产物登记、偏好计数、报告派发由调用方注入）。

本模块不负责 tool_call/tool_result 事件，事件顺序仍由调用方控制。
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import time
from typing import Any, Awaitable, Callable, Dict, Optional

from backend.agent.memory import agent_db
from backend.agent.tools.registry import get_tool_by_name, get_tool_category_map

logger = logging.getLogger(__name__)

SendFunc = Callable[[dict], Awaitable[None]]
SuccessHook = Callable[[str, dict, dict, Optional[int]], Any]


def sanitize_task_input(tool_args: dict) -> dict:
    """工具入参脱敏: 路径只保留 basename, 超长文本截断。"""
    if not isinstance(tool_args, dict):
        return {"raw": str(tool_args)[:200]}
    safe = {}
    for key, value in tool_args.items():
        if isinstance(value, str) and ("path" in key.lower() or key.endswith("_path") or "/" in value or "\\" in value):
            safe[key] = value.replace("\\", "/").split("/")[-1] if value else value
        elif isinstance(value, str) and len(value) > 500:
            safe[key] = value[:500] + "...(truncated)"
        else:
            safe[key] = value
    return safe


def summarize_task_output(tool_result: dict) -> dict:
    """工具出参摘要: 只保留 type/summary/description/stats/elapsed/产物路径。"""
    if not isinstance(tool_result, dict):
        return {"type": str(type(tool_result))}
    summary = {}
    for key in ("type", "summary", "description"):
        if key in tool_result:
            value = tool_result[key]
            if isinstance(value, str) and len(value) > 500:
                value = value[:500] + "...(truncated)"
            summary[key] = value
    data = tool_result.get("data")
    if isinstance(data, dict):
        if "stats" in data:
            summary["stats"] = data["stats"]
        if "elapsed" in data:
            summary["elapsed"] = data["elapsed"]
        try:
            from backend.agent.tools._result import extract_artifact_path
            artifact = extract_artifact_path(data)
            if artifact:
                summary["artifact_path"] = artifact
        except ImportError:
            pass
    return summary


def extract_resource_usage(tool_result: dict, elapsed_ms) -> Optional[Dict[str, Any]]:
    """从工具结果提取耗时与模型侧性能埋点。"""
    if not isinstance(tool_result, dict):
        return None
    resource = {"wall_clock_ms": int(elapsed_ms) if elapsed_ms else None}
    performance = tool_result.get("performance")
    if isinstance(performance, dict):
        for key in ("inference_seconds", "total_seconds", "model_ready_seconds", "postprocess_seconds"):
            if key in performance:
                resource[key] = performance[key]
    return resource if resource.get("wall_clock_ms") is not None else None


class ToolCallRunner:
    """单次工具调用的执行与审计。"""

    def __init__(
        self,
        *,
        conversation_id: str,
        user_id: str = "",
        session_id: str = "",
        send: Optional[SendFunc] = None,
        turn_images: Optional[list] = None,
        on_success: Optional[SuccessHook] = None,
    ):
        """
        入参:
          - conversation_id / user_id / session_id: 会话上下文。
          - send: 异步事件发送 (wss.send_to_session 的包装)。
          - turn_images: 本轮聊天图片收集列表 (原地追加)。
          - on_success: 工具成功后的回调 (tool_name, tool_result, task_id)。
        方法: 保存依赖, 不执行任何工具。
        出参: 无。
        """
        self.conversation_id = conversation_id
        self.user_id = user_id
        self.session_id = session_id
        self.send = send
        self.turn_images = turn_images if isinstance(turn_images, list) else []
        self.on_success = on_success
        self._category_map = get_tool_category_map()

    async def _emit(self, payload: dict) -> None:
        """发送事件; 未注入 send 时静默跳过。"""
        if self.send is not None:
            await self.send(payload)

    async def run(
        self,
        tool_name: str,
        tool_args: dict,
        tool_call_id: str = "",
        *,
        announce: bool = False,
    ) -> Dict[str, Any]:
        """
        入参:
          - tool_name / tool_args / tool_call_id: 本次工具调用。
          - announce: 是否发送 tool_call 事件 (由调用方决定, 避免重复)。
        方法:
          - 按工具分类决定是否开 ai_task; 执行工具; 规范化异常;
          - 处理 frontend_action 推送与等待; 调用成功回调。
        出参:
          - 工具原始结果 dict。
        """
        tool_args = tool_args if isinstance(tool_args, dict) else {}
        if announce:
            await self._emit({
                "type": "tool_call",
                "tool_calls": [{
                    "id": tool_call_id,
                    "tool_call_id": tool_call_id,
                    "name": tool_name,
                    "args": tool_args,
                }],
                "conversation_id": self.conversation_id,
            })

        task_id: Optional[int] = None
        started_at = None
        is_heavy = self._category_map.get(tool_name) in agent_db.HEAVY_TOOL_CATEGORIES
        if is_heavy:
            started_at = time.time()
            task_id = agent_db.create_task(
                conversation_id=self.conversation_id,
                task_type=tool_name,
                tool_name=tool_name,
                user_id=self.user_id,
                input_data=sanitize_task_input(tool_args),
            )
            if task_id is not None:
                agent_db.update_task(task_id, status="running", progress=10)
                agent_db.append_task_log(task_id, f"开始执行工具: {tool_name}")

        tool_result: Dict[str, Any]
        tool_func = get_tool_by_name(tool_name)
        if not tool_func:
            tool_result = {"type": "error", "msg": f"未知工具: {tool_name}"}
        else:
            try:
                await asyncio.sleep(0)
                underlying = getattr(tool_func, "coroutine", None) or getattr(tool_func, "func", None)
                if underlying and inspect.iscoroutinefunction(underlying):
                    tool_result = await tool_func.ainvoke(tool_args)
                else:
                    tool_result = await asyncio.to_thread(tool_func.invoke, tool_args)
            except asyncio.CancelledError:
                if task_id is not None:
                    agent_db.update_task(task_id, status="cancelled", error="用户取消")
                    agent_db.append_task_log(task_id, "任务被用户取消", level="warning")
                raise
            except Exception as exc:
                err_msg = str(exc) or type(exc).__name__
                err_type = type(exc).__name__
                if not err_msg.startswith(err_type):
                    err_msg = f"[{err_type}] {err_msg}"
                tool_result = {
                    "type": "error",
                    "msg": err_msg,
                    "error_type": err_type,
                    "tool": tool_name,
                }

        if task_id is not None:
            elapsed_ms = int((time.time() - started_at) * 1000) if started_at else None
            is_error = isinstance(tool_result, dict) and tool_result.get("type") == "error"
            if is_error:
                agent_db.update_task(
                    task_id, status="failed", progress=100,
                    error=(tool_result.get("msg") or "")[:500],
                )
                agent_db.append_task_log(
                    task_id, f"工具执行失败: {tool_result.get('msg', '')}",
                    level="error", elapsed_ms=elapsed_ms,
                )
            else:
                agent_db.update_task(
                    task_id, status="done", progress=100,
                    output=summarize_task_output(tool_result),
                    resource_usage=extract_resource_usage(tool_result, elapsed_ms),
                )
                agent_db.append_task_log(
                    task_id, "工具执行完成", level="info", elapsed_ms=elapsed_ms,
                )

        if isinstance(tool_result, dict) and tool_result.get("type") == "frontend_action":
            instruction = tool_result.get("instruction") or {}
            instr_type = instruction.get("type") or ""
            await self._emit({
                "type": "frontend_action",
                "event_type": instr_type,
                "event_data": instruction.get("params") or {},
                "description": tool_result.get("description", ""),
                "request_id": tool_call_id,
                "conversation_id": self.conversation_id,
            })
            if instr_type == "chat_image":
                params = instruction.get("params") or {}
                if params.get("image_url"):
                    self.turn_images.append({
                        "url": params["image_url"],
                        "caption": params.get("caption", ""),
                    })
            if tool_result.get("wait_for_result") and tool_call_id:
                from backend.agent.ws_manager import ws_manager
                frontend_result = await ws_manager.wait_for_frontend_result(tool_call_id)
                logger.info(f"[WS] Frontend result: {frontend_result}")
                tool_result["frontend_result"] = frontend_result

        if self.on_success is not None:
            try:
                value = self.on_success(tool_name, tool_args, tool_result, task_id)
                if inspect.isawaitable(value):
                    await value
            except Exception as exc:
                logger.warning(f"[ToolRunner] 成功回调失败 (非阻塞) [tool={tool_name}]: {exc}")

        return tool_result
