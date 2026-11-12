# Agent-RSCD 改造升级文档集

本目录按给定顺序组织 9 个专题。每篇包含：当前基线、差距、目标设计、改造任务、验收标准。

## 文档顺序

| # | 文档 | 当前状态 |
|---:|---|---|
| 1 | [AI Agent 记忆系统](01-agent-memory.md) | 三类记忆已落地；生命周期与 lesson 数据未闭环 |
| 2 | [多 Agent 协作系统](02-multi-agent.md) | 主控 + verification/report 两个受控 worker；无通用协作运行时 |
| 3 | [提示词工程](03-prompt-engineering.md) | 唯一静态模板 + 动态段；已完成基础重构 |
| 4 | [上下文工程](04-context-engineering.md) | 静态/动态双块；预算与可观测性未闭环 |
| 5 | [Agent Skills](05-agent-skills.md) | 3 个技能文档、5 个标准 SkillSpec；执行闭环未完成 |
| 6 | [MCP 协议](06-mcp-protocol.md) | 客户端、动态工具注册、管理 API 可用；协议能力不完整 |
| 7 | [Harness Engineering](07-harness-engineering.md) | 协议与 ChatHarness 适配存在；事件仍由 ChatService 直推 |
| 8 | [AI 工作流](08-ai-workflow.md) | PlanGraph + 3 模板 + DAG Executor；主链路仍以 ReAct 为主 |
| 9 | [Loop Engineering](09-loop-engineering.md) | Plan-and-Execute 外层循环已落地（默认关闭）；预算部分可用，循环检测与评估闭环未完成 |

## 实施阶段

实施顺序与文档顺序一致。每一阶段完成后才进入下一阶段。

| 阶段 | 对应文档 | 目标 |
|---:|---|---|
| 1 | 01 | 记忆生命周期、写入门控、注入预算、lesson 闭环 |
| 2 | 02 | worker 角色注册、契约、任务图、权限与观测 |
| 3 | 03 | 提示词版本、分区预算、评估与回归 |
| 4 | 04 | 上下文预算、来源追踪、缓存失效、快照 |
| 5 | 05 | 技能注册、选择、执行、验收闭环 |
| 6 | 06 | MCP 连接、发现、调用、安全、观测 |
| 7 | 07 | Harness 事件、生命周期、运行记录、回放 |
| 8 | 08 | Workflow 节点契约、DAG 执行、补偿与人工确认 |
| 9 | 09 | Loop 预算、终止条件、循环检测、评估闭环 |

## 当前基线

```text
后端：Python FastAPI 8020 + Java Spring Boot 8081
前端：Vue 3 + Vite 5173
Agent：单主控 ReAct + 可选 Plan-first
记忆：preference / profile / lesson
工具：56 个注册，44 个 LLM 暴露，14 类
技能：3 个 Markdown 文档，5 个标准 SkillSpec
MCP：0 个已配置服务器
工作流：3 个 PlanGraph 模板
```

## 约束

- 主控 Agent 保持唯一决策中心。
- verification_agent 保持确定性，不调用 LLM。
- report_agent 保持受控异步 worker。
- 不新增无契约、无权限、无验收的专业 worker。
- 每阶段必须提供测试或可复现验证，不以文档完成代替实现完成。
