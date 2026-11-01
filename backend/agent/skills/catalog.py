# -*- coding: utf-8 -*-
"""标准业务技能目录。

覆盖当前系统的主流程：
- 地物提取（单图分割 → 矢量化 → 报告）
- 变化检测（双时相 → 变化矢量 → 统计/报告）
- GeoServer 发布（上传 → 前端加载）
- 外部 Tools 空间分析
- 外部 Tools 栅格质量/光谱检查
"""

from __future__ import annotations

from typing import Dict, List, Optional

from backend.agent.skills.spec import SkillSpec


STANDARD_SKILLS: List[SkillSpec] = [
    SkillSpec(
        skill_id="segment_extract",
        name="地物提取",
        description="对单期正射影像按类别提取地物，输出矢量/预览/报告。",
        triggers=["地物提取", "提取建筑", "分割", "语义分割", "segment", "extract building"],
        required_inputs=["image_path", "classes"],
        tools=["segment_image", "visualize_vector", "generate_monitor_report"],
        steps=[
            "校验输入影像 CRS 与类别",
            "调用 segment_image 生成掩膜/矢量",
            "调用 visualize_vector 输出叠加预览",
            "按需生成监测报告并推送下载",
        ],
        acceptance=["vector_path 存在", "agent_validation.status=passed", "统计/报告可下载"],
    ),
    SkillSpec(
        skill_id="change_detect",
        name="变化检测",
        description="对两期影像做实例级变化检测，输出变化矢量、转移矩阵与报告。",
        triggers=["变化检测", "双时相", "对比变化", "变化", "change detection", "detect change"],
        required_inputs=["t1_path", "t2_path", "classes"],
        tools=["detect_change", "generate_change_stats_chart", "generate_monitor_report"],
        steps=[
            "校验两期影像 CRS/范围",
            "调用 detect_change 生成变化矢量与统计",
            "生成变化统计图",
            "生成监测报告并推送下载",
        ],
        acceptance=["vector_path 存在", "transition 完整", "图表/报告可下载"],
    ),
    SkillSpec(
        skill_id="geoserver_publish",
        name="GeoServer 发布",
        description="把本地 GeoTIFF/Shapefile 上传到 GeoServer 并加载到前端地图。",
        triggers=["发布图层", "上传 geoserver", "publish layer", "upload raster", "upload shapefile"],
        required_inputs=["file_path"],
        tools=["upload_raster_layer", "upload_shapefile_layer", "load_geoserver_layer"],
        steps=[
            "校验本地文件",
            "按类型调用 upload_raster_layer / upload_shapefile_layer",
            "调用 load_geoserver_layer 在前端显示",
        ],
        acceptance=["layer_name/workspace 返回", "前端收到加载指令"],
    ),
    SkillSpec(
        skill_id="external_spatial_analysis",
        name="外部空间分析",
        description="复用 Tools 的空间分析能力做 buffer / intersection / erase。",
        triggers=["空间分析", "缓冲区", "叠加分析", "buffer", "intersection", "erase"],
        required_inputs=["geojson_a", "operation"],
        tools=["external_spatial_operation"],
        steps=[
            "读取 GeoJSON 文件或 JSON",
            "调用 external_spatial_operation",
            "返回结果 GeoJSON 与要素数",
        ],
        acceptance=["result 为 FeatureCollection", "feature_count 返回"],
    ),
    SkillSpec(
        skill_id="external_raster_qa",
        name="栅格质量与光谱检查",
        description="复用 Tools 读取栅格元信息并计算 NDVI/NDWI 统计。",
        triggers=["栅格信息", "光谱指数", "NDVI", "NDWI", "raster info", "影像质量"],
        required_inputs=["file_path"],
        tools=["external_raster_info", "external_spectral_index"],
        steps=[
            "读取投影/尺寸/波段数",
            "按需计算 NDVI/NDWI 统计",
        ],
        acceptance=["projection/width/height 返回", "光谱统计 min/max/mean 返回"],
    ),
    SkillSpec(
        skill_id="external_knowledge_query",
        name="知识库问答",
        description="检索 KnowledgeRAG 本地知识条目与解决方案案例。",
        triggers=["知识库", "案例", "方案", "knowledge", "RAG", "植被恢复", "土壤恢复"],
        required_inputs=["question"],
        tools=["external_knowledge_query"],
        steps=[
            "抽取问题关键词",
            "检索 knowledge / solution_cases",
            "返回命中条目与摘要",
        ],
        acceptance=["matches 返回", "命中条目含 path/snippet"],
    ),
]


_BY_ID: Dict[str, SkillSpec] = {skill.skill_id: skill for skill in STANDARD_SKILLS}


def list_standard_skills() -> List[SkillSpec]:
    """返回全部标准技能。"""
    return list(STANDARD_SKILLS)


def get_standard_skill(skill_id: str) -> Optional[SkillSpec]:
    """按 ID 取标准技能。"""
    return _BY_ID.get(skill_id)


def match_standard_skill(text: str) -> Optional[SkillSpec]:
    """按触发词做确定性匹配；无命中返回 None。"""
    lowered = (text or "").lower()
    for skill in STANDARD_SKILLS:
        if any(trigger.lower() in lowered for trigger in skill.triggers):
            return skill
    return None
