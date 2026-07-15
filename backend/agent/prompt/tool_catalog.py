"""
工具目录提示词片段 (Agent 层 / Prompt)

本模块只负责把当前已注册工具转成给 LLM 阅读的能力目录。
"""


# 工具分类的中文标签 (category → 展示名 + 说明)
# ★ 与 tool_registry.register_tool("xxx") 的 category 一一对应
_TOOL_CATEGORY_LABELS = {
    # —— 遥感解译主线 (核心, 目录顺序决定 Agent 优先关注的工具) ——
    "samseg": ("遥感解译分析", "SamSeg 语义分割 / 多时相变化检测 (指定地物类别进行解译, 含业务类型判读)"),
    "vlm": ("遥感影像理解", "对单张遥感影像进行视觉理解、地物描述与空间分布分析"),
    "remote_sensing": ("遥感影像获取", "从 GeoServer 下载栅格影像为 GeoTIFF"),
    "geoserver": ("图层服务操作", "栅格/矢量图层多格式上传下载 (GeoTIFF/GeoJSON/Shapefile) + 服务查询"),
    "layer_control": ("地图图层控制", "前端地图图层的显示/隐藏/定位 (frontend_action 驱动)"),
    # —— 辅助分析能力 ——
    "preprocess": ("数据预处理", "影像金字塔构建 / COG 转换 / 矢量拓扑检查"),
    "analysis": ("掩膜分析", "连通域分析 + 几何特征提取 (OBB/多边形/方位, 可导出带坐标系 GeoJSON)"),
    "report": ("报表生成", "变化统计图表 / PDF Word 监测报告 / 矢量与表格导出"),
    "database": ("数据库查询", "PostgreSQL 表的查询、统计、增删改 + 影像元数据时空检索"),
    "sandbox": ("沙盒代码与文件", "沙盒内执行 Python/Shell 代码；导入本地文件 (import_file_to_sandbox)；沙盒图片渲染到前端 (render_sandbox_image)；下载沙盒文件到宿主机目录 (download_file_from_sandbox)"),
    "skill": ("长任务技能", "按需检索标准化长任务编排方案, 拿到步骤后逐步执行"),
    "memory": ("记忆管理", "查看/清空长期记忆与会话摘要 (AI 自身的记忆系统)"),
    "agent_team": ("专业 Worker 调度", "派发 verification_agent 做客观验收; report_agent 由系统在分割完成后自动派发, 主控用 query_agent_task 查阅报告产物; 查询或取消 worker 任务"),
    "demo": ("演示工具", "本地测试工具 (时间/数学/UI面板等, 不依赖外部服务)"),
}


def build_tools_catalog() -> str:
    """
    入参:
      - 无显式入参, 从全局工具注册表读取当前已暴露给 LLM 的工具。
    方法:
      - 按工具 category 分组。
      - 每个工具只输出名称和 docstring 首行。
      - 参数 schema 由 llm.bind_tools() 注入, 此处不重复展开。
    出参:
      - str, 可直接拼入 system prompt 的工具能力目录。
    """
    from backend.model.tools.tool_registry import get_tools_catalog_for_llm, get_tool_category_map

    all_tools = get_tools_catalog_for_llm()
    cat_map = get_tool_category_map()

    grouped: dict = {}
    for name, tool_obj in all_tools.items():
        cat = cat_map.get(name, "other")
        grouped.setdefault(cat, []).append((name, tool_obj))

    ordered_cats = list(_TOOL_CATEGORY_LABELS.keys()) + [
        cat for cat in grouped if cat not in _TOOL_CATEGORY_LABELS
    ]

    blocks = []
    for cat in ordered_cats:
        if cat not in grouped:
            continue
        label, desc = _TOOL_CATEGORY_LABELS.get(cat, (cat, ""))
        lines = [f"### {label} — {desc}"]
        for name, tool_obj in grouped[cat]:
            desc_text = (tool_obj.description or "").strip().split("\n")[0]
            lines.append(f"- **{name}**: {desc_text}")
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)
