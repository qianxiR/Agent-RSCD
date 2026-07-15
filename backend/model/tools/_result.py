# -*- coding: utf-8 -*-
"""
统一工具返回结构 (Model 层 / 工具箱基础设施)

设计动机 (v2.5):
  调研发现 39 个工具的返回 dict 结构五花八门——
    - type 取值基本统一 (frontend_action/success/error), 但 import_file_to_sandbox 用 status
    - instruction 的 type/action 字段对齐性差 (layer_tools/analysis/data 多处 type≠action)
    - 路径类字段命名混乱 (output_path/local_path/geojson_path/vector_url/artifact_path 至少 15 种)
    - verification 覆盖率低 (仅 report×4 + mask 条件)
    - sandbox_tools 把 stdout/stderr/returncode 放顶层而非 data

本模块定义标准 ToolResult schema + 构造 helper, 作为"软约束":
  - 新工具强制用 helper 构造返回值 (规范统一)
  - 旧工具返回的 dict 继续工作 (chat_service 向后兼容, 不强制改造)
  - helper 保证 instruction.type == action (修复历史不一致)
  - helper 规范化路径字段 (artifact_path/artifact_url), 保留工具特有字段

★ 与 chat_service 消费逻辑的关系:
  chat_service 的 _summarize_task_output / verification 兜底 已扩展识别
  artifact_path / output_path / geojson_path / local_path 多种键名,
  新旧工具都能被正确消费。
"""
from typing import Any, Dict, Optional


# ==================== 标准 type 取值 ====================
TYPE_FRONTEND_ACTION = "frontend_action"
TYPE_SUCCESS = "success"
TYPE_ERROR = "error"


# ==================== 标准结构定义 (文档性, 实际用 dict) ====================
# 一个标准的工具返回 dict 包含以下键 (除 type 外都可选):
#
#   {
#     "type": "frontend_action" | "success" | "error",   # 必须字段
#     "summary": str,                # 给 LLM 看的文本摘要 (success/frontend_action 推荐, error 可选)
#     "msg": str,                    # error 时的错误信息 (error 必须字段)
#     "data": dict,                  # 结构化数据 (stats/路径/计数等, 各工具差异大)
#     "instruction": {               # 仅 frontend_action: 前端指令
#         "type": str,               #   == action (★ helper 保证一致)
#         "action": str,             #   render_image / download / render_table / layer_control / ...
#         "params": dict             #   前端执行参数
#     },
#     "description": str,            # 给前端进度条/卡片标题的提示
#     "wait_for_result": bool,       # frontend_action 时: 是否阻塞等前端回执 (默认 False)
#     "verification": dict,          # 成功契约校验 {ok, size_bytes, reason, evidence, ...}
#                                    #   产物型工具推荐带 (由 _verification.py 生成)
#   }
#
# data 里推荐的标准路径字段 (规范化命名, 旧字段保留兼容):
#   - artifact_path:  产物磁盘绝对路径 (替代 output_path/local_path/geojson_path)
#   - artifact_url:   产物前端可访问 URL (替代 output_url/download_url/vector_url/image_url)


# ==================== 构造 helper ====================

def build_success(
    summary: str,
    data: Optional[Dict[str, Any]] = None,
    verification: Optional[Dict[str, Any]] = None,
    description: str = "",
) -> Dict[str, Any]:
    """
    构造标准 success 结果 (纯数据/文本类工具, 不触发前端动作).

    适用: understand_image / upload_raster_layer / insert_data / 元数据检索 /
          build_pyramid / check_topology / memory 工具 等.

    入参:
        - summary: 给 LLM 看的文本摘要 (必填, LLM 据此理解工具执行结果)
        - data: 结构化数据 (路径/统计/计数等, 可选)
        - verification: 成功契约校验 (产物型工具推荐带, 由 _verification.py 生成)
        - description: 给前端进度提示 (可选, 一般 success 不需要)

    出参: {"type": "success", "summary", "data", "verification"(可选), "description"(可选)}
    """
    result: Dict[str, Any] = {"type": TYPE_SUCCESS, "summary": summary}
    if data is not None:
        result["data"] = data
    if verification is not None:
        result["verification"] = verification
    if description:
        result["description"] = description
    return result


def build_error(
    msg: str,
    data: Optional[Dict[str, Any]] = None,
    summary: str = "",
    verification: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    构造标准 error 结果 (所有工具的错误路径统一用此).

    入参:
        - msg: 错误信息 (必填, 给 LLM 看的失败原因)
        - data: 附加数据 (可选, 如部分产出/已完成的步骤)
        - summary: 错误摘要 (可选, 默认等于 msg)
        - verification: 校验详情 (可选, 校验失败时带, 含 reason 字段)

    出参: {"type": "error", "msg", "summary"(=msg if 空), "data"(可选), "verification"(可选)}
    """
    result: Dict[str, Any] = {"type": TYPE_ERROR, "msg": msg, "summary": summary or msg}
    if data is not None:
        result["data"] = data
    if verification is not None:
        result["verification"] = verification
    return result


def build_frontend_action(
    action: str,
    params: Dict[str, Any],
    summary: str,
    description: str = "",
    wait_for_result: bool = False,
    data: Optional[Dict[str, Any]] = None,
    verification: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    构造标准 frontend_action 结果 (触发前端渲染/下载/图层控制等动作).

    适用: segment_image / detect_change / render_sandbox_image / download_geoserver_layer /
          generate_change_stats_chart / generate_monitor_report / export_change_vector /
          list_geoserver_services / toggle_layer_visibility 等.

    ★ instruction 固定三键 {type, action, params}, 且 type == action (修复历史不一致).

    入参:
        - action: 前端动作类型 (render_image / download / render_table /
                  layer_control / layer_group_control / layer_display / layer_locate)
        - params: 前端执行参数 (各 action 不同: image_url/caption/legend/url/filename/...)
        - summary: 给 LLM 看的文本摘要
        - description: 给前端进度条/卡片标题的提示
        - wait_for_result: 是否阻塞等前端回执 (图层类 True, 渲染/下载类 False)
        - data: 结构化数据 (路径/统计等, 给 LLM 看)
        - verification: 成功契约校验 (产物型工具推荐带)

    出参: 完整的 frontend_action dict
    """
    result: Dict[str, Any] = {
        "type": TYPE_FRONTEND_ACTION,
        "action": action,
        "instruction": {
            "type": action,  # ★ 保证 type == action (修复历史 type≠action 不一致)
            "action": action,
            "params": params,
        },
        "wait_for_result": wait_for_result,
        "description": description,
        "summary": summary,
    }
    if data is not None:
        result["data"] = data
    if verification is not None:
        result["verification"] = verification
    return result


def build_download_action(
    params: Dict[str, Any],
    summary: str,
    data: Optional[Dict[str, Any]] = None,
    verification: Optional[Dict[str, Any]] = None,
    description: str = "",
) -> Dict[str, Any]:
    """
    构造下载类 frontend_action (action=download 的便捷封装).

    这是 report/analysis/data 下载工具的通用形态:
      download_geoserver_layer / download_raster_layer / download_shapefile_layer /
      generate_monitor_report / export_change_vector / export_stats_table

    params 推荐字段: {url, filename, caption, file_size, ...}
    data 推荐字段: {artifact_path, artifact_url, format, ...}
    """
    return build_frontend_action(
        action="download",
        params=params,
        summary=summary,
        description=description,
        wait_for_result=False,  # 下载类不阻塞
        data=data,
        verification=verification,
    )


def build_render_image_action(
    params: Dict[str, Any],
    summary: str,
    data: Optional[Dict[str, Any]] = None,
    verification: Optional[Dict[str, Any]] = None,
    description: str = "",
) -> Dict[str, Any]:
    """
    构造图片渲染类 frontend_action (action=render_image 的便捷封装).

    这是 samseg/sandbox/report 图表工具的通用形态:
      segment_image / detect_change / render_sandbox_image /
      generate_change_stats_chart / analyze_connected_components

    params 推荐字段: {image_url, caption, legend, extra_images, input_image_url, ...}
    data 推荐字段: {artifact_path, stats, vector_url, ...}
    """
    return build_frontend_action(
        action="render_image",
        params=params,
        summary=summary,
        description=description,
        wait_for_result=False,  # 渲染类不阻塞 (samseg 几十秒, 阻塞会超时)
        data=data,
        verification=verification,
    )


def build_chat_image_action(
    image_url: str,
    caption: str = "",
    summary: str = "",
    data: Optional[Dict[str, Any]] = None,
    description: str = "",
) -> Dict[str, Any]:
    """
    构造聊天图片类 frontend_action (action=chat_image 的便捷封装)。

    与 render_image 的区别: render_image 走右侧地图/GeoServer 面板 (要 base_layer);
    chat_image 把静态 PNG 直接渲染进聊天窗口消息流, 用户点击可放大预览。
    当前形态: render_sandbox_image (沙盒 matplotlib/Pillow 产出的无坐标图)。

    入参:
        - image_url: 前端可访问的图片 URL (/api/v1/download/...)
        - caption: 图片标题/说明, 留空由前端用文件名
        - summary: 给 LLM 看的文本摘要
        - data: 结构化数据 (local_path/filename 等, 给 LLM/任务表)
        - description: 给前端进度提示

    出参: 完整的 frontend_action dict (instruction.type/action = chat_image, 不阻塞)
    """
    return build_frontend_action(
        action="chat_image",
        params={"image_url": image_url, "caption": caption},
        summary=summary,
        description=description,
        wait_for_result=False,  # 聊天图片不阻塞, 前端立即渲染
        data=data,
    )


# ==================== 路径字段规范化 helper ====================

# chat_service 兜底识别的路径键名集合 (新旧兼容)
# 新工具用 artifact_path, 旧工具用 output_path/local_path/geojson_path 等
ARTIFACT_PATH_KEYS = (
    "artifact_path",   # 新规范 (v2.5+)
    "output_path",     # report/preprocess 旧命名
    "geojson_path",    # mask/data 矢量旧命名
    "local_path",      # sandbox_render 旧命名
    "shp_path",        # export 特有
)

ARTIFACT_URL_KEYS = (
    "artifact_url",    # 新规范
    "output_url",      # report 旧命名
    "download_url",    # analysis/data 旧命名
    "image_url",       # 渲染图 URL (在 instruction.params 里)
    "vector_url",      # samseg 矢量 URL
)


def extract_artifact_path(data: Optional[Dict[str, Any]]) -> Optional[str]:
    """
    从工具 data dict 里抽取产物路径 (兼容新旧命名).
    chat_service 的 verification 兜底用此函数识别路径.

    返回第一个非空的路径值, 或 None。
    """
    if not data:
        return None
    for key in ARTIFACT_PATH_KEYS:
        val = data.get(key)
        if val:
            return val
    return None


def extract_artifact_url(data: Optional[Dict[str, Any]]) -> Optional[str]:
    """从工具 data dict 里抽取产物 URL (兼容新旧命名)."""
    if not data:
        return None
    for key in ARTIFACT_URL_KEYS:
        val = data.get(key)
        if val:
            return val
    return None
