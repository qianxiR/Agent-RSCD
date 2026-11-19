# 05 Agent Skills

## 1. 目标

技能是可检索、可校验、可执行、可验收的长任务编排单元。技能不是提示词段落，也不是自由文本教程。

## 2. 当前基线

### 2.1 技能文档

[`backend/model/skills/`](../../backend/model/skills)

```text
wf1-segment-visualize-report.md
wf2-change-detection-visualize-report.md
wf4-upload-geoserver-display.md
```

3 个 Markdown，均含 `<!-- skill-contract ... -->`。

### 2.2 加载与检索

- [`backend/agent/skills/loader.py`](../../backend/agent/skills/loader.py)
- [`backend/agent/skills/selector.py`](../../backend/agent/skills/selector.py)
- [`backend/agent/tools/skill_tools.py`](../../backend/agent/tools/skill_tools.py)

```text
lookup_skill(query)
  -> loader.retrieve_skills()
  -> selector.select_skill()
  -> 返回最佳技能全文与契约
```

### 2.3 标准技能目录

[`backend/agent/skills/catalog.py`](../../backend/agent/skills/catalog.py)

5 个 SkillSpec：

```text
segment_extract
change_detect
geoserver_publish
external_spatial_analysis
external_raster_qa
```

### 2.4 当前问题

- Markdown 技能与 `catalog.py` SkillSpec 是两套定义。
- `match_standard_skill()` 当前无调用方。
- `lookup_skill` 只返回文本，未强制按契约逐步执行。
- 工具白名单未在执行时强制校验。
- 技能契约没有版本迁移和兼容检查。
- 技能选择依赖关键词，缺少与计划状态的强绑定。

## 3. 目标设计

### 3.1 单一技能注册表

```text
SkillSpec
  skill_id
  version
  title
  description
  triggers
  required_inputs
  tool_allowlist
  steps[]
  verification_rules
  fallback_rule
  promotion_evidence
```

Markdown 只作为说明文档，契约以结构化注册表为准。

### 3.2 执行闭环

```text
技能选择
  -> 校验必填输入
  -> 校验工具白名单
  -> 写入显式计划
  -> 按步骤执行
  -> 每步 verification
  -> 完成门
```

禁止：

```text
只返回技能文本，不写入计划
跳过必填输入直接执行
调用白名单外工具
未通过验收宣告完成
```

### 3.3 技能与计划

技能步骤映射为 PlanGraph 节点：

```text
skill.steps[i] -> plan.steps[i]
step.tool_name -> node.tool
step.required_inputs -> node.inputs
step.verification_rules -> node.acceptance
```

`lookup_skill` 返回技能后，主控必须调用 `apply_skill_selection_to_plan` 写入计划。

### 3.4 评估

```text
skill_selection_accuracy
contract_valid_rate
required_input_completion_rate
step_completion_rate
skill_success_rate
```

## 4. 改造任务

### P0

- [ ] 合并 Markdown 契约与 `catalog.py` SkillSpec，建立唯一注册表。
- [ ] `lookup_skill` 返回结构化步骤，不只返回全文。
- [ ] 执行时校验工具白名单。
- [ ] 技能选择后强制写入计划。
- [ ] 增加 3 个技能的端到端测试。

### P1

- [ ] 增加技能版本和迁移规则。
- [ ] 增加技能失败回退策略。
- [ ] 增加技能步骤级验收。
- [ ] 增加技能选择审计日志。

### P2

- [ ] 增加技能市场/导入格式。
- [ ] 增加技能 A/B 评估。
- [ ] 增加技能与 lesson 的关联。

## 5. 验收标准

```text
1. 一个技能只有一份契约。
2. 每个技能有必填输入、工具白名单、步骤、验收规则。
3. lookup_skill 后计划中出现对应步骤。
4. 调用白名单外工具被拒绝。
5. 未通过步骤验收不能完成技能。
6. 3 个技能均可端到端执行。
```

## 6. 风险

- 技能契约过严会降低复用。
- 技能与计划状态强绑定会增加状态同步复杂度。
- 合并两套定义时可能破坏现有 `lookup_skill` 调用。
