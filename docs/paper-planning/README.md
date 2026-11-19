# 任务规划与执行稳定性论文

重点主题:agent 任务规划与任务执行稳定性。分三组:基础范式、规划架构、执行稳定性。

## 阅读顺序

1. ReAct → Plan-and-Act → ReWOO → ADaPT:从交错式执行,到计划-执行分离,再到预生成计划与按需分解。
2. LLM-Modulo → Reflexion → CRITIC → TRAIL → MAST:从"LLM 计划不可直接信任"的批判视角,到失败修正机制,再到错误定位与失败归因基准。
3. ToT / LATS / LLM+P / Plan-and-Solve:搜索式规划与外接确定性规划器,按需补充。

## 基础范式与规划架构

| 文件 | 论文 | 出处 | 说明 |
|---|---|---|---|
| 2210.03629_ReAct.pdf | ReAct | ICLR 2023 | 推理与行动交错,边推理边调用工具。当前 agent 执行循环的基础范式,本项目 executor 的直接来源。 |
| 2503.09572_Plan-and-Act.pdf | Plan-and-Act | 2025 | 规划器-执行器分离:规划器生成分步计划,执行器逐步执行,失败触发动态重规划,面向长时程任务。 |
| 2305.04091_PlanAndSolve.pdf | Plan-and-Solve | ACL 2023 | 提示词层面先制定完整计划再逐步执行,改善零样本 CoT。 |
| 2305.10601_TreeOfThoughts.pdf | Tree of Thoughts | NeurIPS 2023 | 把问题求解组织为树搜索:分支、评估、回溯。规划即搜索。 |
| 2305.18323_ReWOO.pdf | ReWOO | 2023 | 预先生成完整计划,执行时只填参数,后续推理不依赖中间观察。降 token,阻断观察错误传播。对应本项目 PlanGraph 预生成 DAG。 |
| 2311.05772_ADaPT.pdf | ADaPT | 2024 | 按需递归分解:先直接执行,失败才分解,深度自适应,避免过度规划。 |
| 2304.11477_LLMPlusP.pdf | LLM+P | 2023 | LLM 把任务翻译成 PDDL,交给经典规划器求最优计划。外接确定性规划器路线。 |
| 2310.04406_LATS.pdf | LATS | ICML 2024 | ReAct + 树搜索 + 环境反馈,统一推理、行动、规划,失败可回溯。 |
| 2402.01817_LLM-Modulo.pdf | LLM-Modulo | 2024 | LLM 生成的计划不可直接信任,须经外部验证与批判回环。对应本项目 verification_agent 与 PlanGraph 校验。 |

## 执行稳定性

| 文件 | 论文 | 出处 | 说明 |
|---|---|---|---|
| 2303.11366_Reflexion.pdf | Reflexion | NeurIPS 2023 | 失败后生成文字反思存入记忆,重试时注入上下文,无需更新权重。对应本项目 lesson / self_correction。 |
| 2303.17651_SelfRefine.pdf | Self-Refine | NeurIPS 2023 | 生成、反馈、修订循环,同一模型充当生成者与批评者。 |
| 2305.11738_CRITIC.pdf | CRITIC | ICLR 2024 | 用外部工具验证输出再修正,不自证。对应 verification_agent。 |
| 2505.08638_TRAIL.pdf | TRAIL | 2025 | 轨迹错误类型学与定位基准:推理、规划、工具调用、参数、工具选择五类错误,386 条人工标注轨迹。失败诊断与归因参考。 |
| 2503.13657_WhyMultiAgentFail_MAST.pdf | MAST | 2025 | 多智能体失败分类学:3 大类 14 种失败模式与标注体系。失败分类对单 agent 长链路同样适用。 |

## 与本项目的对应关系

- 执行循环:ReAct(交错式)与 Plan-and-Act / ReWOO(计划-执行分离)是本项目 PlanGraph + ReAct executor 混合架构的两端。
- 确定性校验:LLM-Modulo / CRITIC 对应 verification_agent:LLM 输出必须过确定性验证才执行或入库。
- 失败修正:Reflexion 对应 lesson 机制:失败 → 文字化经验 → 重试注入。
- 错误归因:TRAIL / MAST 为失败诊断与 lesson 准入提供错误分类参考。
