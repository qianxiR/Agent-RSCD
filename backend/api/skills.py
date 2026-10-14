"""技能库查询路由 (/api/v1/skills)。

数据源: model/skills 目录下的 Markdown 技能方案 (loader 懒加载 + 缓存),
供前端技能库面板展示。只读端点。
"""
import logging

from fastapi import APIRouter

from backend.model.skills import loader as skills_loader

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/api/v1/skills")
async def list_skills():
    """
    列出全部标准技能的元信息。
    - name: 稳定标识 (文件名)
    - title: 展示名
    - version / contract_valid / required_inputs: 来自技能契约
    - keywords: 触发场景关键词
    """
    try:
        skills = skills_loader.list_skills()
        return {"status": "success", "count": len(skills), "skills": skills}
    except Exception as e:
        logger.error(f"列出技能失败: {e}")
        return {"status": "error", "msg": f"技能加载失败: {e}"}
