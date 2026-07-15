"""
工具注册与调度系统 (Model 层 / 工具箱)
- 入参: 工具函数 (带 @tool + @register_tool 装饰器)
- 方法: 注册、查询、执行工具
- 出参: 工具注册表 dict[name → Callable]

设计要点:
  装饰器顺序: @register_tool("category") 在上, @tool 在下
  因为 Python 装饰器从下往上执行: 先 @tool 包装为 StructuredTool,
  再 @register_tool 注册到全局表, 此时 func 已是 StructuredTool 对象,
  需要用 func.name (而非 func.__name__) 获取工具名
"""
import logging
from typing import Dict, Any, Callable, Optional

logger = logging.getLogger(__name__)

# 全局注册表: category → {tool_name → tool_func}
_tool_registry: Dict[str, Dict[str, Callable]] = {}

# 工具名 → 所属分类的反向映射
_tool_category_map: Dict[str, str] = {}

# ★ LLM 暴露白名单:
#   注册表仍保留全部工具, 便于内部调用/测试/兼容旧链路。
#   只有这里列出的工具会进入 bind_tools 与 system prompt 工具目录, 从源头收窄 Agent 决策空间。
_LLM_EXPOSED_TOOL_NAMES = {
    # 遥感解译主线
    "segment_image",
    "detect_change",
    "understand_image",
    # GeoServer 图层增删查改与结果发布
    "list_geoserver_services",
    "download_layer",
    "upload_raster_layer",
    "upload_shapefile_layer",
    "publish_geojson_layer",
    "delete_geoserver_layer",
    # 前端地图加载/定位/显隐
    "load_geoserver_layer",
    "locate_to_layer_bounds",
    "toggle_layer_visibility",
    # 分析与报告
    "analyze_connected_components",
    "export_mask_to_shapefile",
    "visualize_vector",
    "generate_change_stats_chart",
    "generate_monitor_report",
    "export_change_vector",
    "export_stats_table",
    # 影像/矢量目录检索
    "list_image_catalog",
    "list_vector_catalog",
    "search_images_by_time",
    "search_images_by_region",
    # 沙盒兜底执行能力
    "run_python_code",
    "run_shell_command",
    "render_sandbox_image",
    "import_file_to_sandbox",
    "download_file_from_sandbox",
    # 长任务编排与主动记忆管理
    "lookup_skill",
    "view_user_memory",
    "save_user_memory_item",
    "delete_user_memory_item",
    "view_conversation_summary",
    "clear_user_memory",
    "clear_conversation_summary",
    # 主控 Agent + 专业 worker 调度
    "assign_agent_task",
    "query_agent_task",
    "cancel_agent_task",
}

_AGENT_ROLE_EXPOSED_TOOL_NAMES = {
    "verification_agent": set(),
}


def register_tool(category: str):
    """
    工具注册装饰器
    - category: 工具分类 (layer_control / remote_sensing 等)
    - 必须放在 @tool 之上 (先被调用, 接收 @tool 包装后的 StructuredTool)
    """
    def decorator(func):
        # @tool 包装后是 StructuredTool, 用 .name 属性获取工具名
        tool_name = getattr(func, 'name', None) or func.__name__
        if category not in _tool_registry:
            _tool_registry[category] = {}
        _tool_registry[category][tool_name] = func
        _tool_category_map[tool_name] = category
        return func
    return decorator


def get_all_tools() -> Dict[str, Callable]:
    """获取所有已注册工具的扁平 dict"""
    result = {}
    for category_tools in _tool_registry.values():
        result.update(category_tools)
    return result


def get_tool_category_map() -> Dict[str, str]:
    """获取工具名 → 所属分类的反向映射 (供 prompt 生成等场景按分类分组)"""
    return dict(_tool_category_map)


def get_tools_for_llm() -> list:
    """
    获取供 LLM bind_tools 使用的工具列表 (LangChain @tool 对象)。
    入参: 无。
    方法: 从完整注册表中按 _LLM_EXPOSED_TOOL_NAMES 白名单筛选, 保留注册顺序, 隐藏非核心/高噪声工具。
    出参: 已收窄的 StructuredTool 列表; 未注册的白名单项会被自然跳过, 支持可选依赖降级。
    """
    all_tools = get_all_tools()
    return [tool for name, tool in all_tools.items() if name in _LLM_EXPOSED_TOOL_NAMES]


def get_tools_catalog_for_llm() -> Dict[str, Callable]:
    """
    获取用于 system prompt 工具目录的白名单工具 dict。
    入参: 无。
    方法: 与 get_tools_for_llm 使用同一白名单, 确保 prompt 目录和 bind_tools 真实能力一致。
    出参: {tool_name: StructuredTool} 映射, 仅包含当前允许 LLM 主动选择的工具。
    """
    all_tools = get_all_tools()
    return {name: tool for name, tool in all_tools.items() if name in _LLM_EXPOSED_TOOL_NAMES}


def get_llm_exposed_tool_names() -> set:
    """
    获取 LLM 暴露工具名集合。
    入参: 无。
    方法: 返回白名单副本, 供诊断脚本或测试核对工具收窄范围。
    出参: set[str], 修改返回值不会影响注册表全局配置。
    """
    return set(_LLM_EXPOSED_TOOL_NAMES)


def get_tools_for_agent_role(agent_role: str) -> list:
    """
    获取指定专业 worker 可绑定的工具列表。
    入参:
        - agent_role: worker 角色名, 如 verification_agent。
    方法:
        - 从完整注册表按角色白名单筛选。
        - 第一阶段 verification_agent 是确定性 worker, 白名单为空。
    出参:
        - StructuredTool 列表; 未知角色返回空列表。
    """
    names = _AGENT_ROLE_EXPOSED_TOOL_NAMES.get(agent_role, set())
    all_tools = get_all_tools()
    return [tool for name, tool in all_tools.items() if name in names]


def get_tool_by_name(name: str) -> Optional[Callable]:
    """按名称获取工具函数"""
    return get_all_tools().get(name)
