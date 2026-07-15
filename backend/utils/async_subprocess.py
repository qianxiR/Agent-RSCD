"""
异步子进程兼容工具
- 入参: 命令参数列表、可选超时秒数
- 方法: 优先走 asyncio 原生子进程; Windows 事件循环不支持时回退到线程中的 subprocess.run
- 出参: {returncode, stdout, stderr, timeout}

解决的问题:
- 部分 Windows 环境下, 当前事件循环对 asyncio.create_subprocess_exec 会直接抛
  NotImplementedError, 导致明明 Docker 可用, 但后端无法执行 docker 命令。
- 本模块统一封装兼容层, 让启动检查与沙盒运行时都复用同一套行为。
"""
import asyncio
import subprocess
from typing import Dict, List, Optional


async def run_subprocess(command: List[str], timeout: Optional[int] = None) -> Dict[str, object]:
    """
    入参:
        - command: 子进程命令参数列表, 不能为空, 例如 ["docker", "info"]。
        - timeout: 超时秒数; 为 None 表示不额外限制。
    方法:
        - 先尝试 asyncio.create_subprocess_exec, 保持原有异步非阻塞行为。
        - 若当前事件循环不支持子进程, 则回退到 asyncio.to_thread + subprocess.run。
        - 两条路径统一返回 bytes 形式 stdout/stderr, 让上层解码逻辑保持不变。
    出参:
        - {returncode, stdout, stderr, timeout}
        - timeout=True 表示执行超时且已终止; 其余情况为 False。
    """
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if timeout is None:
            stdout, stderr = await process.communicate()
        else:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        return {
            "returncode": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "timeout": False,
        }
    except NotImplementedError:
        # 做什么: 回退到线程执行同步 subprocess; 为什么: 兼容 Windows 上不支持
        # asyncio 子进程的事件循环实现, 让 Docker 命令仍可被正常调用。
        return await asyncio.to_thread(_run_sync_subprocess, command, timeout)
    except asyncio.TimeoutError:
        return {
            "returncode": -1,
            "stdout": b"",
            "stderr": f"执行超时 (>{timeout}s), 已终止".encode("utf-8"),
            "timeout": True,
        }


def _run_sync_subprocess(command: List[str], timeout: Optional[int] = None) -> Dict[str, object]:
    """
    入参:
        - command: 子进程命令参数列表。
        - timeout: 超时秒数; 直接传给 subprocess.run。
    方法:
        - 在线程里执行同步 subprocess.run, 避免阻塞主事件循环。
        - 统一捕获 TimeoutExpired, 返回与异步路径一致的结构。
    出参:
        - {returncode, stdout, stderr, timeout}
    """
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        return {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "timeout": False,
        }
    except subprocess.TimeoutExpired:
        return {
            "returncode": -1,
            "stdout": b"",
            "stderr": f"执行超时 (>{timeout}s), 已终止".encode("utf-8"),
            "timeout": True,
        }
