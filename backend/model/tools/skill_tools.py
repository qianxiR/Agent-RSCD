"""
技能检索工具集 (Model 层 / 工具箱)
- 入参: 用户意图 query (长任务描述)
- 方法: 从 skills 目录检索匹配的长任务编排方案, 返回全文步骤供 agent 执行
- 出参: 标准工具结果 dict, 含匹配 skill 的完整执行步骤

设计说明:
  skills 文档 (model/skills/*.md) 记录"长任务"的多步编排方案
  (如数据入库流水线)。这类方案内容较长, **不注入 system prompt**
  (会持续占用上下文), 而是做成按需查询工具 —— agent 识别到长任务
  意图时, 主动调用本工具检索对应方案, 拿到步骤后逐步执行。

  ★ 与普通工具的区别:
    普通工具 (data/layer/analysis) 是"执行一个动作";
    lookup_skill 是"查一份说明书", 返回的是文档而非数据。

依赖方向: model.tools.skill_tools → model.skills.loader (本层内部), 无跨层依赖。
"""
import logging
from typing import Dict, Any

from langchain_core.tools import tool
from backend.model.tools.tool_registry import register_tool
from backend.model.skills import loader as skills_loader

logger = logging.getLogger(__name__)


@register_tool("skill")
@tool
def lookup_skill(query: str) -> Dict[str, Any]:
    """
    检索长任务技能方案。当用户提出一个需要多步骤、多次工具调用的复杂长任务时
    (如"把影像入库"、"完整接入一份数据"、"批量处理多份数据"), 调用本工具
    获取对应的标准化执行步骤, 然后按步骤逐步执行。

    ★ 何时调用本工具:
      - 用户既要"发布图层"又要"登记元数据"等复合需求 → 对应的入库流水线方案
      - 任务明显需要 3 步以上、跨多个工具的编排
      - 不确定如何拆解复杂任务时, 先查是否有现成方案

    ★ 何时不调用:
      - 单一明确的操作 (如"显示 <图层名> 图层" → 直接 toggle_layer_visibility)
      - 简单查询 (如"列出数据库表" → 直接 list_database_tables)

    入参:
        - query (str): 用户的任务意图描述, 直接用用户原话或概括均可。
          例如 "数据入库流水线"、"完整接入一份新数据"。

    出参:
        - 匹配的技能方案列表, 每项含 name/title/完整执行步骤(content)。
        - agent 应选择 score 最高的方案, 阅读其 content 中的步骤逐步执行。
        - 若无匹配, 返回空列表, agent 需自行拆解任务或向用户澄清。
    """
    try:
        matches = skills_loader.retrieve_skills(query, top_k=3)

        if not matches:
            available = skills_loader.list_skills()
            avail_desc = "、".join(f"{s['title']}({s['name']})" for s in available) if available else "暂无"
            return {
                "type": "success",
                "summary": f"未检索到匹配「{query}」的长任务方案。\n当前可用方案: {avail_desc}\n请尝试更明确的描述, 或自行拆解任务。",
                "data": {"query": query, "matches": [], "available": available},
            }

        # 构造结果: 完整返回最佳匹配的步骤, 其余仅返回元信息
        best = matches[0]
        others = [
            {"name": m["name"], "title": m["title"], "score": m["score"]}
            for m in matches[1:]
        ]

        logger.info(
            f"[Skill] lookup query={query!r} → best={best['name']} (score={best['score']}), "
            f"others={len(others)}"
        )

        return {
            "type": "success",
            "summary": (
                f"📚 检索到长任务方案: 【{best['title']}】\n"
                f"请按下列步骤逐步执行:\n\n"
                f"{best['content']}"
            ),
            "data": {
                "query": query,
                "best_match": {
                    "name": best["name"],
                    "title": best["title"],
                    "score": best["score"],
                    "content": best["content"],
                },
                "other_candidates": others,
            },
        }
    except Exception as e:
        logger.error(f"[Skill] lookup_skill 失败 query={query!r}: {e}", exc_info=True)
        return {"type": "error", "msg": f"技能检索失败: {e}"}


@register_tool("skill")
@tool
def list_available_skills() -> Dict[str, Any]:
    """
    列出所有可用的长任务技能方案 (仅元信息, 不含全文)。
    用于让 agent 了解系统具备哪些标准化长任务能力, 或向用户展示可选项。

    入参: 无
    出参: 技能方案列表 [{name, title, keywords}], agent 可据此判断是否需要
          调用 lookup_skill 获取某个方案的详细步骤。
    触发场景: 用户问"你能处理哪些复杂任务"、"有哪些标准流程"、"你有哪些技能"
    """
    try:
        skills = skills_loader.list_skills()
        if not skills:
            return {
                "type": "success",
                "summary": "当前没有任何已注册的长任务技能方案。",
                "data": {"skills": [], "count": 0},
            }

        lines = [f"📋 系统具备 {len(skills)} 个标准化长任务方案:\n"]
        for s in skills:
            kw = ", ".join(s["keywords"][:4]) if s.get("keywords") else ""
            lines.append(f"  • 【{s['title']}】({s['name']}) — 关键词: {kw}")
        lines.append("\n需要执行某个方案时, 调用 lookup_skill 检索其详细步骤。")

        return {
            "type": "success",
            "summary": "\n".join(lines),
            "data": {"skills": skills, "count": len(skills)},
        }
    except Exception as e:
        return {"type": "error", "msg": f"列出技能失败: {e}"}
