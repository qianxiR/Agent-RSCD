# 技能论文

技能 = agent 可沉淀、检索、复用的可执行能力单元。对应本项目 agent/skills/。

## 阅读顺序

Voyager → Agent Workflow Memory → SkillWeaver → Anthropic 技能规范

## 论文

| 文件 | 论文 | 出处 | 说明 |
|---|---|---|---|
| 2305.16291_Voyager.pdf | Voyager | 2023 | 技能库范式来源:自动编写可执行代码作为技能存入向量库,按需检索组合,课程驱动终身学习。 |
| 2409.07429_AgentWorkflowMemory.pdf | Agent Workflow Memory | ICLR 2025 | 从历史轨迹在线归纳可复用 workflow 存入工作流库,跨任务复用。对应本项目技能沉淀与记忆复用。 |
| 2504.07079_SkillWeaver.pdf | SkillWeaver | 2025 | web agent 自主把站点能力合成可执行技能并练习精炼,技能以 API 形式共享。 |

## 工程参考

- Anthropic_Agent_Skills_工程规范_README.md:anthropics/skills 仓库 README 的本地副本。工业界技能格式:文件夹 + SKILL.md(frontmatter 元数据 + 正文),渐进披露。对应本项目 agent/skills/loader + catalog。工程规范,非论文。
