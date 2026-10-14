# Construction Skill 落地设计：静态规则层升级

> 工程决策记录。对标 DataFlow-WebUI 的 Construction Skill 防算子幻觉机制，结合 Agent-RSCD 自研 harness 现状，给出静态规则层的补齐方案。
>
> 状态：P0 已实施（决策表规则源 + 生成器 + prompt 注入 + 评估对照基准）。

## 1. 背景与对标

DataFlow-WebUI（OpenDCAI）的 Construction Skill 通过「结构化 Markdown 文档 + Agent CLI 自动加载」防止 LLM 编造或误用算子。其核心防幻觉武器有五层：

| 机制 | DataFlow 实现 | Agent-RSCD 现状 | 差距 |
|---|---|---|---|
| 算子白名单 | 6 个核心算子固定白名单 | `_LLM_EXPOSED_TOOL_NAMES` 34 个工具白名单 | 已有 |
| 任务→工具决策表 | MANDATORY 决策表（必用/禁用） | 无 | **核心缺失** |
| 算子签名表 | 完整签名 + 边界 + 正反例 | 参数 schema 在 bind_tools，prompt 无边界提示 | 部分缺失 |
| 正反例对照 | good.md / bad.md | 无 | 缺失 |
| 运行时质量门 | 无 | selector 契约校验 + verification_agent + retry_guard + parameter_repair | 远超 DataFlow |

**关键结论**：Agent-RSCD 在运行时校验层已远超 DataFlow，短板在静态规则层——LLM 在 Thought 阶段缺乏「该用哪个、禁用哪个、参数边界」的显式约束。改造焦点 = 补齐静态规则层，而非照搬 DataFlow 的文件加载架构。

## 2. 改造目标与边界

**目标**：让 LLM 在 Thought 阶段就能命中正确工具、规避禁用组合、参数落在边界内——把「事后由 verification 修复」的 case 前移为「事前由 prompt 规避」。

**边界**：
- 范围内：prompt 静态规则层（决策表 / 生成器 / 评估对照基准）
- 范围外：不改 tool_registry 白名单、selector 质量门、verification_agent / retry_guard / parameter_repair、ReAct 主循环

## 3. P0 改造设计

### 3.1 决策表规则源（唯一可信源）

新建 `backend/agent/prompt/tool_decision_rules.py`，集中维护「任务类型 → 工具组合」规则：

```python
DECISION_RULES = [
    {
        "task_type": "semantic_segmentation",
        "task_pattern": "单影像地物提取 / 语义分割",
        "required_tools": ["segment_image"],
        "forbidden_tools": ["detect_change", "generate_monitor_report"],
        "key_constraint": "classes 非空; 接受本地路径, 无需导入沙盒",
    },
    # ... 变化检测 / 影像理解 / 长任务编排 / 报告下载 / 图层控制
]
```

**设计要点**：
- `forbidden_tools` 语义为「不能作为完成此任务的主工具」，前置探索调用不在禁用范围（与 task_planning_eval 的首步判定不冲突）
- 规则源的工具名必须 ⊆ `_LLM_EXPOSED_TOOL_NAMES`，由 `validate_rules()` + 单元测试守护

### 3.2 决策表生成器

`tool_catalog.py` 新增 `build_decision_table() -> str`，从规则源自动生成 Markdown 表注入 system prompt：

```
| 任务类型 | 必用工具 | 禁用主工具 | 关键约束 |
|---|---|---|---|
| 单影像地物提取 / 语义分割 | segment_image | detect_change, generate_monitor_report | classes 非空; ... |
```

工具增减只改规则源，prompt 自动同步。

### 3.3 Prompt 注入

`static_template.py` 在「可用工具目录」段之前插入决策表段，`system_prompt.py` 填充 `{decision_table}` 占位符（含 `build_system_prompt_blocks` 静态缓存块版本同步）。

### 3.4 评估对照基准

`task_planning_eval.py` 导入 `DECISION_RULES`，判定时合并 `case.forbidden_tools` 与决策表对应 `task_type` 的 `forbidden_tools`——决策表更新时评估自动跟进，且不破坏现有 3/3 基线（已核对决策表 forbidden 与 case expected_tools 无交叉）。

### 3.5 单元测试守护

`tests/test_decision_rules.py` 断言规则源所有工具名 ⊆ LLM 暴露白名单，CI 拦截笔误。

## 4. 文件清单

| 操作 | 文件 | 说明 |
|---|---|---|
| 新建 | `backend/agent/prompt/tool_decision_rules.py` | 决策表规则源 + validate_rules |
| 新建 | `tests/test_decision_rules.py` | 规则源工具名白名单守护 |
| 修改 | `backend/agent/prompt/tool_catalog.py` | 新增 build_decision_table() |
| 修改 | `backend/agent/prompt/static_template.py` | 插入 {decision_table} 占位符段 |
| 修改 | `backend/agent/prompt/system_prompt.py` | 填充占位符（两个 build 函数同步） |
| 修改 | `tests/task_planning_eval.py` | 合并决策表 forbidden 做对照基准 |

**不改**：tool_registry.py / selector.py / verification_agent / retry_guard / parameter_repair / chat_service.py

## 5. 验证方式

```powershell
# 1. 单元测试: 规则源工具名 ⊆ 白名单
python -m pytest tests/test_decision_rules.py -v

# 2. 任务规划评估: 决策表注入后不破坏基线 (期望 3/3 PASS)
python tests/task_planning_eval.py

# 3. 行为评估不回归 (期望 false_success_rate=0/10)
python tests/agent_behavior_harness_eval.py

# 4. 思维链质量不降分
python tests/thinking_quality_eval.py

# 5. 启动服务确认决策表段渲染正常
python backend\main.py
```

## 6. 实施中发现的回归与修正（重要）

**发现**：初版用「MANDATORY，命中第一条即用」强查表措辞，导致思维链评估 practicality 从 4.5→4.1（pass_rate 80%→60% FAIL）、first_principles 4.2→3.9，综合分 93.0→89.5。

**根因**：对照实验（临时关闭决策表跑基线）确认是决策表的真实回归，非裁判波动。机制分析——「MANDATORY 查表」认知风格与第一性原理推导冲突：LLM 在 Reflection 阶段也倾向于「套规则」而非「从证据推导根因」，典型降分模式如把"GeoTIFF 不可读"套成"路径写错"（A10）、把"ZIP 不完整"套成".dbf 缺失"（A5）。

**修正**：把标题改为「任务→工具决策表（工具选择参考，不影响根因推导）」，并显式加边界声明——
> ★ 本表只约束工具选择：决定用哪个工具看本表；决定工具为何失败、下一步如何修复，仍按第一层「Repair Plan 执行协议」从客观证据推导根因，不得用本表的查表风格替代根因推导。

**修正后效果**：

| 维度 | 决策表关（基线） | 决策表开（初版 MANDATORY） | 决策表开（修正后） |
|---|---|---|---|
| practicality | 4.5 | 4.1 ❌ | **4.7** ✅ |
| first_principles | 4.2 | 3.9 | **4.2** ✅ |
| 综合分 | 93.0 | 89.5 | **93.5** ✅ |
| practicality_pass | 80% | 60% ❌ | **90%** ✅ |

修正后所有维度不低于基线，决策表的工具选择增益不再以牺牲根因推导为代价。

## 7. 风险与缓解

| 风险 | 缓解 |
|---|---|
| prompt token 增量（约 200-400 token） | 决策表比等价段落描述更省 token；放在静态缓存块内，跨轮命中缓存 |
| 规则源维护负担 | validate_rules + 单元测试断言工具名 ⊆ 白名单，CI 拦截遗漏 |
| 决策表与真实工具能力漂移 | build_decision_table 从规则源生成，task_planning_eval 做一致性校验 |

## 8. 后续阶段（本次不做）

- **P1**：skill 文档（wf1/wf2/wf4）增加正反例段
- **P2**：独立工具签名表文档 `docs/agent/core/tool-signatures.md`
