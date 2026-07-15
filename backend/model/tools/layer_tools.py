"""
图层控制工具集 (Model 层 / 工具箱)
- 入参: layer_name (图层名称)、action (show/hide/toggle)
- 方法: 构造前端操作指令, 通过 frontend_action 让前端执行图层操作
- 出参: 标准工具结果 dict, 含 instruction + wait_for_result=True

这类工具的核心特点:
  后端不直接操作地图, 而是发送指令给前端, 等待前端执行完毕后返回结果
  这是因为地图状态 (OpenLayers) 完全在前端维护, 后端无法直接操控
"""
from typing import Dict, Any, Optional, List
from langchain_core.tools import tool
from backend.model.tools.tool_registry import register_tool
from backend.model.tools._result import build_frontend_action
from backend.config import settings
from backend.data import geoserver_client


def clean_layer_name(name: str) -> str:
    """清理图层名称, 去除用户输入的 @ 符号"""
    return name.lstrip('@') if name else name


@register_tool("layer_control")
@tool
def toggle_layer_visibility(layer_name: str, action: str, workspace: str = "") -> Dict[str, Any]:
    """
    控制单个图层的显示/隐藏状态

    ★ 工作空间自动识别:
      1. 优先使用传入的 workspace 参数查找图层
      2. 若该 workspace 下未找到，自动遍历所有 GeoServer 工作空间搜索
      3. 找到后使用正确的工作空间加载 WMS 影像预览
      4. 这意味着你不需要精确知道图层在哪个 workspace —— 传裸名即可自动匹配

    功能说明:
    - show: 显示图层并自动定位到图层范围
    - hide: 隐藏图层但不改变地图视角
    - toggle: 切换图层显示状态，如果变为显示会自动定位

    Args:
        layer_name: 图层名称（GeoServer 中的实际图层名，裸名或带 workspace 前缀均可）
        action: 操作类型，可选值："show"、"hide"、"toggle"
        workspace: 提示的工作空间名称，留空则自动遍历所有工作空间搜索；
                   系统会自动验证并修正，无需人工精确指定。

    Returns:
        图层控制指令，需要发送到前端执行

    示例:
        用户: "显示 my_layer 图层"
        调用: toggle_layer_visibility("my_layer", "show")
        效果: 自动识别工作空间 → 获取正确 bbox → 显示并定位
    """
    cleaned_name = clean_layer_name(layer_name)

    # 图层组名称转发到图层组工具
    layer_group_names = [
        "行政区划", "矢量图层", "矢量图层（湖泊）", "湖泊", "湖泊矢量",
        "栅格图层", "栅格图层（哨兵影像）", "哨兵影像", "遥感影像"
    ]
    if cleaned_name in layer_group_names:
        return toggle_layer_group_visibility(cleaned_name, action)

    action_desc_map = {"show": "显示并定位", "hide": "隐藏", "toggle": "切换"}
    action_desc = action_desc_map.get(action, action)
    should_locate = action in ["show", "toggle"]

    # ★ 第一步: 推断初始 workspace (基于名称后缀的快速猜测)
    inferred_ws = _infer_workspace(cleaned_name, workspace)

    # ★ 第二步: 验证并解析真实 workspace (REST API 验证 → 全 workspace 搜索 → 兜底)
    resolved_ws, bare_name = _resolve_layer_workspace(cleaned_name, inferred_ws)

    # ★ 第三步: 用正确 workspace 获取 bbox
    resolved_bbox = None
    if should_locate:
        resolved_bbox = _resolve_bbox(None, resolved_ws, bare_name)

    return build_frontend_action(
        action="layer_control",
        params={
            "layer_name": bare_name,
            "action": action,
            "locate": should_locate,
            "workspace": resolved_ws,
            "wms_url": settings.geoserver_wms_url,
            "bbox": resolved_bbox,
        },
        summary=f"{action_desc}图层: {bare_name} (工作空间: {resolved_ws})",
        description=f"正在{action_desc}图层: {bare_name} (工作空间: {resolved_ws})",
        wait_for_result=True,
    )


def _infer_workspace(layer_name: str, fallback: str = "") -> str:
    """推断 GeoServer 工作空间 (当前不绑定具体工作空间, 由上层搜索解析)。"""
    return fallback


def _resolve_layer_workspace(layer_name: str, hint_workspace: str = None) -> tuple:
    """
    ★ 解析图层所属的真实工作空间（两步验证）。
    
    流程:
      1. 若 layer_name 已含 ':' → 直接拆分返回
      2. 裸名 + 有提示 workspace → 用 REST API 快速验证图层是否存在
      3. 快速验证失败 → 遍历所有 workspace 搜索
      4. 全找不到 → 用提示 workspace 兜底（后续由 GeoServer WMS 自行报错）
    
    返回: (resolved_workspace: str, bare_layer_name: str)
    """
    # 已含冒号: 直接拆分 (如 "ws:layer" → ("ws", "layer"))
    if ':' in layer_name:
        parts = layer_name.split(':', 1)
        return parts[0], parts[1]
    
    # 裸名: 先快速验证提示 workspace
    ws_hint = hint_workspace or settings.geoserver_workspace or ""
    try:
        if ws_hint and geoserver_client.layer_exists(ws_hint, layer_name):
            return ws_hint, layer_name
    except Exception:
        pass  # GeoServer 不可用，不阻塞，继续尝试全局搜索
    
    # 提示 workspace 失败或为空 → 遍历所有 workspace 搜索
    try:
        full_name = geoserver_client.resolve_layer_name(layer_name)
        if full_name and ':' in full_name:
            parts = full_name.split(':', 1)
            return parts[0], parts[1]
    except Exception:
        pass  # 遍历也失败，兜底
    
    # 兜底: 用提示 workspace（即使不一定正确，后续 WMS 请求会报错提示用户）
    return ws_hint, layer_name


@register_tool("layer_control")
@tool
def hide_layer(layer_name: str) -> Dict[str, Any]:
    """
    关闭（隐藏）指定图层

    功能说明:
    - 仅用于关闭图层，不会触发定位
    - 如需打开图层，请使用 toggle_layer_visibility 工具（会自动定位）

    Args:
        layer_name: 图层名称（GeoServer 中的实际图层名，裸名或带 workspace 前缀均可）

    Returns:
        图层关闭指令，需要发送到前端执行
    """
    cleaned_name = clean_layer_name(layer_name)

    layer_group_names = [
        "行政区划", "矢量图层", "矢量图层（湖泊）", "湖泊", "湖泊矢量",
        "栅格图层", "栅格图层（哨兵影像）", "哨兵影像", "遥感影像"
    ]
    if cleaned_name in layer_group_names:
        return toggle_layer_group_visibility(cleaned_name, "hide")

    return build_frontend_action(
        action="layer_control",
        params={"layer_name": cleaned_name, "action": "hide"},
        summary=f"关闭图层: {cleaned_name}",
        description=f"正在关闭图层: {cleaned_name}",
        wait_for_result=True,
    )


@register_tool("layer_control")
@tool
def toggle_layer_group_visibility(group_name: str, action: str) -> Dict[str, Any]:
    """
    批量控制图层组的可见性

    Args:
        group_name: 图层组名称，可选值：
            - "行政区划": 行政区划图层组
            - "矢量图层" 或 "矢量图层（湖泊）": 湖泊矢量图层组
            - "栅格图层" 或 "栅格图层（哨兵影像）": 哨兵影像栅格图层组
        action: 操作类型，可选值："show"、"hide"、"toggle"
    """
    cleaned_name = clean_layer_name(group_name)

    group_name_map = {
        "行政区划": "行政区划",
        "矢量图层": "矢量图层", "矢量图层（湖泊）": "矢量图层",
        "湖泊": "矢量图层", "湖泊矢量": "矢量图层",
        "栅格图层": "栅格图层", "栅格图层（哨兵影像）": "栅格图层",
        "哨兵影像": "栅格图层", "遥感影像": "栅格图层",
    }
    standard_group_name = group_name_map.get(cleaned_name, cleaned_name)

    action_desc_map = {"show": "显示", "hide": "隐藏", "toggle": "切换"}
    action_desc = action_desc_map.get(action, action)

    return build_frontend_action(
        action="layer_group_control",
        params={"group_name": standard_group_name, "action": action},
        summary=f"{action_desc}图层组: {standard_group_name}",
        description=f"正在{action_desc}图层组: {standard_group_name}",
        wait_for_result=True,
    )


@register_tool("layer_control")
@tool
def load_geoserver_layer(
    layer_id: str,
    layer_name: str,
    workspace: str = "",
    layer_type: str = "raster",
    bbox: list = None,
    statistics: dict = None,
) -> Dict[str, Any]:
    """
    从GeoServer加载图层到前端地图。

    ★ 工作空间自动识别:
      1. 优先使用传入的 workspace 参数查找图层
      2. 若该 workspace 下未找到，自动遍历所有 GeoServer 工作空间搜索
      3. 找到后使用正确的工作空间加载 WMS 影像预览

    Args:
        layer_id: 图层ID（GeoServer 中的实际图层名）
        layer_name: 图层显示名称
        workspace: 提示的工作空间名称，留空则自动搜索；系统会自动验证并修正
        layer_type: 图层类型，"raster" 或 "vector"，默认 "raster"
        bbox: 图层边界框 [minLng, minLat, maxLng, maxLat]；未传时自动从 GeoServer 获取
        statistics: 统计信息字典
    """
    # ★ 解析工作空间: 用 layer_id 作为 GeoServer 查找键
    inferred_ws = _infer_workspace(layer_id or layer_name, workspace)
    resolved_ws, bare_id = _resolve_layer_workspace(layer_id or layer_name, inferred_ws)

    # ★ bbox 兜底: 未传时从 GeoServer GetCapabilities 自动获取 (使用已解析的正确 workspace)
    resolved_bbox = _resolve_bbox(bbox, resolved_ws, bare_id)

    return build_frontend_action(
        action="layer_display",
        params={
            "layer_name": bare_id,
            "type": layer_type,
            "bbox": resolved_bbox,
            "statistics": statistics,
            "workspace": resolved_ws,
            "wms_url": settings.geoserver_wms_url,
        },
        summary=f"从GeoServer加载图层: {layer_name} (工作空间: {resolved_ws})",
        description=f"正在从GeoServer加载图层: {layer_name} (工作空间: {resolved_ws})",
        wait_for_result=True,
    )


def _resolve_bbox(bbox: Optional[List[float]], workspace_hint: str, layer_name: str) -> Optional[List[float]]:
    """
    ★ 解析图层 bbox（含工作空间自动识别）。
    
    流程:
      1. 若调用方已传入 bbox → 原样返回
      2. 否则: 先通过 _resolve_layer_workspace 确定正确的工作空间
      3. 再用确定后的 workspace:layername 从 GeoServer GetCapabilities 取 bbox
    
    - 入参: bbox ([minLng,minLat,maxLng,maxLat] 或 None) / workspace_hint / layer_name
    - 出参: [minLng,minLat,maxLng,maxLat] 或 None (GeoServer 不可用时)
    """
    if bbox:
        return bbox
    try:
        resolved_ws, bare_name = _resolve_layer_workspace(layer_name, workspace_hint)
        full_name = f"{resolved_ws}:{bare_name}"
        raw = geoserver_client.get_layer_bbox(full_name)
        if raw:
            # get_layer_bbox 返回 {minx,miny,maxx,maxy} (WGS84: minx=经度, miny=纬度)
            return [raw["minx"], raw["miny"], raw["maxx"], raw["maxy"]]
    except Exception:
        pass  # GeoServer 不可用 → 返回 None, 前端用全局 bbox 兜底
    return None


@register_tool("layer_control")
@tool
def locate_to_layer_bounds(
    layer_name: str,
    layer_id: str = "",
    bbox: list = None,
) -> Dict[str, Any]:
    """
    定位到图层边界范围（仅定位，不显示图层）

    ★ 工作空间自动识别: 系统会自动搜索图层所属的正确工作空间

    功能说明:
    - 调整地图视角，缩放到指定图层的边界范围
    - 不会改变图层的显示状态

    Args:
        layer_name: 图层名称（GeoServer 中的实际图层名，裸名或带 workspace 前缀均可）
        layer_id: 图层ID，不传时自动使用 layer_name
        bbox: 图层边界框 [minLng, minLat, maxLng, maxLat]；未传时自动从 GeoServer 获取
    """
    resolved_layer_id = layer_id or layer_name
    resolved_ws, bare_name = _resolve_layer_workspace(resolved_layer_id, settings.geoserver_workspace)
    resolved_bbox = _resolve_bbox(bbox, resolved_ws, bare_name)

    return build_frontend_action(
        action="layer_locate",
        params={
            "layer_id": bare_name,
            "layer_name": layer_name,
            "bbox": resolved_bbox,
        },
        summary=f"定位到图层: {layer_name} (工作空间: {resolved_ws})",
        description=f"正在定位到图层: {layer_name} (工作空间: {resolved_ws})",
        wait_for_result=True,
    )
