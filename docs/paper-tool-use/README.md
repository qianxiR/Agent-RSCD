# 工具调用论文

覆盖工具学习主线:自监督学会用工具 → 微调加检索缓解 API 幻觉 → 大规模真实 API → 评测基准 → 代码动作 → 评测稳定性。

## 阅读顺序

Toolformer → Gorilla → ToolLLM → API-Bank → HuggingGPT → CodeAct → StableToolBench → τ-bench

| 文件 | 论文 | 出处 | 说明 |
|---|---|---|---|
| 2302.04761_Toolformer.pdf | Toolformer | 2023 | 自监督确定在哪里调用、如何调用,并大规模合成训练数据,模型自学用工具。 |
| 2305.15334_Gorilla.pdf | Gorilla | 2023 | API 调用专用微调 + 检索感知训练,缓解 API 幻觉。 |
| 2307.16789_ToolLLM.pdf | ToolLLM | ICLR 2024 | 16000+ 真实 API 的 ToolBench 数据集,训练 ToolLLaMA,提出 DFSDT 推理策略(深度优先加回溯)。 |
| 2304.08244_API-Bank.pdf | API-Bank | 2023 | 工具增强 LLM 的系统评测:从调用、检索到规划分层评测工具能力。 |
| 2303.17580_HuggingGPT.pdf | HuggingGPT | NeurIPS 2023 | LLM 作控制器,四阶段:任务规划、模型选择、执行、响应汇总;把模型当工具编排。对应本项目模型编排层。 |
| 2402.01030_CodeAct.pdf | CodeAct | ICML 2024 | 以可执行代码为动作空间,可复用已装包、迭代修正,优于 JSON 工具调用。动作空间设计参考。 |
| 2403.07714_StableToolBench.pdf | StableToolBench | 2024 | 修复 ToolBench 评测不稳定:虚拟 API 服务器与稳定指标,保证评测可复现。执行稳定性在评测层面的对应。 |
| 2406.12045_TauBench.pdf | τ-bench | 2024 | 工具-智能体-用户三方交互基准,考察长程任务中规则遵守与数据库状态一致性,显示长程场景下可靠性下降。 |
