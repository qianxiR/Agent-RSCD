"""
沙盒代码执行工具 (Model 层 / 工具箱)
- 入参: Python 代码字符串 / Shell 命令字符串
- 方法: 在隔离的 Docker 沙盒中执行任意代码
- 出参: {type, summary, data:{stdout, stderr, returncode, host_workspace_path}, msg(失败时)}

★ 核心价值: 让 Agent 拥有"自己写代码解决问题"的能力。
  当 25 个预定义工具都不适用时 (如自定义算法、数据分析、格式转换等),
  Agent 可以调用本工具编写并执行 Python 代码, 获得结果后继续推理。

★ 沙盒隔离模型 (仿 Apix):
  - client_id = 当前对话 ID (conversation_id), 由运行时上下文注入
  - work_dir  = 按 (project_id 分组 + conversation_id 会话) 派生的工作区路径
  → 同一 (分组, 会话) 的多次调用共享同一工作区文件,
    不同会话相互隔离 (与 samseg/geoserver 产物目录结构一致)

设计要点:
  1. 两个工具: run_python_code (执行代码) + run_shell_command (执行命令)
  2. 异步执行: docker exec 走 asyncio subprocess, 不阻塞 event loop
  3. 优雅降级: Docker 不可用时返回明确错误, 不影响其他工具

依赖: 宿主机需安装 Docker 且已构建沙盒镜像 (见 sandbox/Dockerfile)。
"""
import logging
from typing import Dict, Any

from langchain_core.tools import tool

from backend.model.tools.tool_registry import register_tool
from backend.model.tools.sandbox_manager import sandbox_manager
from backend.model.tools._result import build_success, build_error

logger = logging.getLogger(__name__)


def _build_sandbox_tool_response(result: Dict[str, Any], command_name: str,
                                 host_workspace_path: str = "",
                                 path_map: Dict[str, str] = None) -> Dict[str, Any]:
    """
    入参:
        - result: sandbox_manager 返回的执行结果, 必须包含 type/stdout/stderr/returncode。
        - command_name: 当前沙盒动作名称, 仅用于错误消息定位, 不能为空。
        - host_workspace_path: 当前会话的宿主机 work_dir 路径 (容器 /workspace 的宿主机实体)。
        - path_map: 完整路径映射 {容器内路径: 宿主机路径}, 含 /workspace 和 /files/* 产物挂载点。
          注入 data.path_map, 让 AI 精确解析路径 (替代旧版纯文本 PATH HINT)。
    方法:
        - 将 stdout/stderr 统一整理为 summary, 并在失败时生成非空 msg。
        - 优先使用 stderr 作为失败原因, stderr 为空时退回 stdout 或退出码, 避免前端显示空错误。
    出参:
        - 返回可直接给 LLM 和前端消费的结构化结果, 失败时始终包含非空 msg。
        - ★ v2.5: stdout/stderr/returncode 归入 data; path_map 结构化注入 (不再拼纯文本)。
    """
    stdout = result.get("stdout", "").strip()
    stderr = result.get("stderr", "").strip()
    returncode = result.get("returncode", -1)

    output_parts = []
    if stdout:
        # 做什么: 保留标准输出; 为什么: Agent 需要 stdout 判断代码的真实计算结果。
        output_parts.append(f"[STDOUT]\n{stdout}")
    if stderr:
        # 做什么: 保留标准错误; 为什么: stderr 通常是 Docker/Python 的根因信息。
        output_parts.append(f"[STDERR]\n{stderr}")
    if not output_parts:
        # 做什么: 明确空输出状态; 为什么: 防止无 stdout/stderr 被误解为空响应。
        output_parts.append(f"{command_name} 执行完成 (无输出)")

    summary = "\n\n".join(output_parts)
    if returncode != 0:
        summary = f"{command_name} 退出码 {returncode}\n{summary}"

    # ★ 路径透明化: 简短提示指向结构化字段, 不再拼长文本 (详情见 data.path_map)
    if path_map and len(path_map) > 1:
        mounts_hint = "、".join(sorted(k for k in path_map if k != "/workspace"))
        summary += f"\n\n[路径映射见 data.path_map] 可读写: {mounts_hint} 等"

    # ★ v2.5: 统一 schema — stdout/stderr/returncode/path_map 归入 data
    data = {
        "stdout": stdout,
        "stderr": stderr,
        "returncode": returncode,
        "host_workspace_path": host_workspace_path,
    }
    if path_map:
        data["path_map"] = path_map

    # 判定成败: type 为 error 或 returncode != 0 都算失败
    is_error = result.get("type") == "error" or returncode != 0
    if is_error:
        # ★ 多重兜底: stderr → stdout → 退出码 → 类型名, 绝不允许 msg 为空
        detail = stderr or stdout or f"{command_name} 执行失败, 退出码 {returncode}" or "沙盒执行失败 (无详细错误信息)"
        return build_error(msg=detail, data=data, summary=summary)
    return build_success(summary=summary, data=data)


def _get_client_and_work_dir():
    """
    从运行时上下文获取 client_id 和 work_dir。
    - client_id = 当前 conversation_id (完整 UUID, 供沙盒 hash 隔离 key)
    - work_dir  = 按 (project_id 分组, conversation_id 会话) 派生的工作区路径 (目录名已短化)
    - project_id = 当前 project_id (原始值, 透传给 manager 用于产物挂载)
    延迟导入避免循环依赖 (context_vars 在 agent 层, 本模块在 model 层)。
    出参: (client_id, work_dir, project_id, conversation_id)
    """
    from backend.agent.runtime.context_vars import (
        get_current_conversation_id, get_current_project_id,
    )
    conversation_id = get_current_conversation_id() or "_anonymous"
    project_id = get_current_project_id() or ""
    client_id = conversation_id
    # ★ work_dir 含会话层级: agent-files/sandbox/workspace/{proj短}/{conv短}/
    work_dir = sandbox_manager.resolve_work_dir(project_id, conversation_id)
    return client_id, work_dir, project_id, conversation_id


@register_tool("sandbox")
@tool
async def run_python_code(code: str) -> Dict[str, Any]:
    """
    在隔离的 Docker 沙盒中立即执行 Python 代码并返回输出。

    适用场景 (当其他工具无法完成时):
    - 自定义算法实现 (NDVI 计算、指数运算、统计分析等)
    - 数据处理与转换 (CSV/JSON 解析、格式转换、批量处理)
    - 数学计算、科学计算 (numpy/pandas/scipy)
    - 遥感栅格分析 (rasterio 读 GeoTIFF、计算统计、重分类等)
    - 矢量空间分析 (geopandas/shapely 读 Shapefile/GeoJSON)
    - 文件读写与文本处理
    - 临时验证性代码 (检查某个值、测试某个逻辑)

    ★ 沙盒环境已预装 (直接 import 即可, 无需 pip install):
      - 数据科学: numpy, pandas, scipy, scikit-learn, sympy
      - 绘图: matplotlib (中文字体已配置), seaborn
      - 遥感/GIS: rasterio (读 GeoTIFF), geopandas + fiona (矢量读写 Shapefile/GeoJSON), shapely (空间分析), rio-cogeo (COG 转换)
      - 其他: Pillow, requests, openpyxl
      - 系统工具: gdal-bin, git, curl, wget, unzip
    ★ 读写本地 GeoTIFF/Shapefile: 先用 import_file_to_sandbox 把文件搬进沙盒,
      再在代码里用 /workspace/文件名 读取。
    ★ 网络可用 (容器与宿主机共享网络), 缺包时可用 run_shell_command 执行
      pip install <包名> (不要加 --quiet, 否则空输出无法判断成败)。

    同一(分组, 会话)的对话共享同一工作区 /workspace, 文件在该会话内可复用。
    沙盒生成的图片可用 render_sandbox_image 工具展示到前端工作区面板。

    Args:
        code: 完整的 Python 代码字符串。代码会被原样写入临时文件并执行,
              不会保存为持久文件。通过 print() 输出结果。
    """
    if not code or not code.strip():
        return {"type": "error", "msg": "代码不能为空"}

    client_id, work_dir, project_id, conversation_id = _get_client_and_work_dir()
    logger.info(f"[Sandbox] run_python_code 被调用: client={client_id[:12]}... work_dir={work_dir}")
    result = await sandbox_manager.exec_python(
        client_id, work_dir, code, project_id=project_id, conversation_id=conversation_id
    )
    logger.info(
        f"[Sandbox] run_python_code 完成: type={result.get('type')} "
        f"rc={result.get('returncode')} stdout_len={len(result.get('stdout', ''))}"
    )

    path_map = sandbox_manager.resolve_path_map(work_dir, project_id, conversation_id)
    return _build_sandbox_tool_response(
        result, "Python", host_workspace_path=str(work_dir), path_map=path_map
    )


@register_tool("sandbox")
@tool
async def run_shell_command(command: str) -> Dict[str, Any]:
    """
    在隔离的 Docker 沙盒中执行 Shell 命令并返回输出。

    适用场景:
    - 安装额外 Python 包 (pip install xxx) 或系统工具 (apt-get install)
    - 文件/目录操作 (ls, find, mv, cp, mkdir 等)
    - 运行脚本、构建项目、调用 CLI 工具 (如 gdalinfo 查看 GeoTIFF 元数据)
    - 检查环境信息 (python --version, pip list 等)
    - Git 操作、网络请求 (curl/wget)

    ★ 沙盒网络可用 (与宿主机共享网络)。判断 pip install 是否成功:
      - 看 returncode (0=成功), 不要只看 stdout 是否为空
      - ★ 不要加 --quiet, 否则输出为空难以判断; 如需精简用 --no-input
    ★ 常用包 (numpy/rasterio/geopandas/matplotlib 等) 已预装, 通常无需 pip install。

    命令在沙盒容器的 /workspace 目录下执行, 该目录与宿主机对应分组的工作区挂载同步。
    可用 timeout 配置项控制最大执行时间 (默认 60 秒)。

    Args:
        command: Shell 命令字符串。支持管道、重定向、多命令组合。
                 示例: "pip install scipy", "ls -la /workspace", "echo hello"
    """
    if not command or not command.strip():
        return {"type": "error", "msg": "命令不能为空"}

    client_id, work_dir, project_id, conversation_id = _get_client_and_work_dir()
    result = await sandbox_manager.exec_shell(
        client_id, work_dir, command, project_id=project_id, conversation_id=conversation_id
    )

    path_map = sandbox_manager.resolve_path_map(work_dir, project_id, conversation_id)
    return _build_sandbox_tool_response(
        result, "Shell 命令", host_workspace_path=str(work_dir), path_map=path_map
    )
