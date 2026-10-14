# Harness Preflight Spec: Claude Code 架构借鉴执行计划

> 基于对 Anthropic 官方 [Agent Loop 文档](https://code.claude.com/docs/en/agent-sdk/agent-loop) 与 [Effective Context Engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) 的架构分析，结合 Agent-RSCD 现有代码的真实状态（已通过代码地图核实），产出的可执行规划。
>
> 状态：规划完成，待评审 → 评审通过后进入实施。

---

## Phase 1: 目标重述

借鉴 Claude Code Agent 架构中 Agent-RSCD 尚未覆盖或可增强的机制，提升长任务（分割→发布→报告→修正，跨 10+ 轮 ReAct）的上下文鲁棒性和 agent 行为质量。

**成功标准**：完成 P0+P1 两项改造，通过全评估回归（任务规划 3/3、行为评估 false_success 0/10、思维链质量 PASS），且长任务模拟验证 token 占用下降或注意力衰减改善。

**不可发生**：不得破坏现有多级压缩体系（Level 0.5/1/2）、不得让 verification_agent/retry_guard/parameter_repair 的现有行为回归、不得改变 ToolMessage 的 DB 持久化结构。

---

## Phase 2: 边界定义

### 现状核实（改变借鉴方向的关键发现）

代码地图核实后，Agent-RSCD **已有成熟的多级上下文管理体系**，与 Claude Code 的差距远小于最初预估：

| Claude Code 机制 | Agent-RSCD 现状（代码核实） | 差距重新评估 |
|---|---|---|
| 自动 Compaction | Level 0.5 折叠 + Level 1/2 摘要 + 强制压缩 + trim_messages（`memory_context.py:160-634`） | **已超越 Claude Code**（多级 vs 单级） |
| Tool result clearing | `fold_tool_messages(keep_tail=3)` 已折叠旧 ToolMessage content（`memory_context.py:160-229`） | **已实现**，但折叠粒度可优化 |
| Subagent context 隔离 | `assign_agent_task` + verification_agent（白名单为空，确定性 worker） | 部分实现，回传结构可优化 |
| Token 预算监控 | `_count_message_tokens` + `context_max_tokens=16000` + `summarize_threshold=8000` | 已实现，但缺主动预警 |
| Hooks | retry_guard + parameter_repair（硬编码在循环） | 不借鉴（场景固定） |

### 改造范围

```
In scope（本次做）:
- P0: Tool result 折叠增强（fold 粒度从"全部旧消息"细化到"保留更多近期待验证结果"）
- P1: Context budget 主动预警（在接近阈值前触发压缩，而非等 token_exceed 被动触发）
- P2: verification_agent 回传结构优化（确认主控不摄入完整 verification detail）

Out of scope（本次不做）:
- Hooks 框架抽取（场景固定，过度工程）
- Subagent 并行化（当前只有 verification_agent，无并行需求）
- Effort level（遥感无轻任务）
- Session fork（无多路径探索需求）

Do not touch（禁止改动）:
- tool_registry.py 白名单结构
- agent_db.py message 表 schema（node_id/parent_id DAG 结构）
- Level 2 物理删除逻辑（delete_messages_upto 不可逆删除保持现状）
- selector.py skill 质量门
- verification_agent 判定逻辑本身
- ReAct 主循环结构（while True + 内层重试）
```

### 改动层分类

```
context（上下文管理）：P0 + P1 主战场
memory（记忆持久化）：P0 可能微调 fold 配置
observability（可观测）：P1 新增 budget 预警日志
prompt（提示词）：不改动
runtime（运行时）：P2 回传结构微调
team（多 agent）：P2 verification 回传
tools（工具）：不改动
skills（技能）：不改动
```

---

## Phase 3: 代码地图

仅列出本次改造涉及的文件：

```
工具消息折叠（P0 核心）→ backend\agent\memory\memory_context.py:160-229
  fold_tool_messages / _fold_tool_content —— 当前 keep_tail=3 硬编码，折叠为"工具名·type·summary前80字"

压缩阈值与配置（P0+P1 共用）→ backend\config.py:119-152
  context_max_tokens=16000 / summarize_threshold=8000 / tool_message_keep_tail=3 / summary_max_tokens=300

多级压缩核心 → backend\agent\memory\memory_context.py:513-634
  maybe_summarize() —— Level 1 记录 / Level 2 LLM 摘要 + 物理删除

强制压缩（被动触发）→ backend\agent\chat_service.py:926-991
  _force_compress_and_retrim —— token_exceed 时触发，压缩后原地替换 all_messages

ReAct 循环 token 检查点 → backend\agent\chat_service.py:1130-1221
  内层重试循环，_classify_llm_error 分流 token_exceed

token 计数 → backend\agent\memory\memory_context.py:88-145
  _get_encoder / _count_tokens / _count_message_tokens —— cl100k_base 近似

trim_messages 裁剪 → backend\agent\memory\memory_context.py:303-331
  硬裁剪兜底，strategy="last"

verification 回传 → backend\agent\team\validation_stage.py:159-194
  apply_validation_to_tool_result —— agent_validation 写回 tool_result

ToolMessage 构造 → backend\agent\runtime\observation_builder.py:52-89
  build_observation_tool_message —— content = JSON.dumps(enhanced_result)

工具结果 schema → backend\model\tools\_result.py:59-129
  build_success / build_error —— summary/data/verification/artifacts/evidence 结构
```

---

## Phase 4: 最小混沌任务包

### 任务包 P0：Tool Result 折叠粒度增强

```
Task name: fold-granularity-enhancement
Goal: 让 fold_tool_messages 在折叠旧工具结果时，对"含 agent_validation 且状态为 failed/warning"的
      ToolMessage 保留更完整的 repair_plan 信息（而非统一压到 80 字），避免压缩后丢失修复线索。
Allowed files:
  - backend\agent\memory\memory_context.py（fold_tool_messages / _fold_tool_content）
Forbidden files:
  - backend\config.py（配置项如需调整另行评估，本次不改）
  - backend\agent\memory\agent_db.py（不动 DB 结构）
  - backend\agent\chat_service.py（不动主循环）
Inputs:
  - 现有 fold_tool_messages(keep_tail=3)：旧 ToolMessage content 统一折叠为
    "[已完成·工具名·type] summary前80字"
Outputs:
  - fold_tool_messages 新增逻辑：解析 content JSON，若含 agent_validation.status
    in {failed, warning}，折叠时保留 failure_type + repair_plan.next_action
    + recommended_tool（而非只保留 summary 前 80 字）
  - 成功状态的旧消息维持原折叠（80 字 summary 足够）
Checkpoint: 折叠后 messages 仍通过 trim_messages 正常裁剪，无 tool_call_id 配对断裂
Validation command:
  D:/anaconda3/envs/sam3/python.exe -m pytest tests/test_memory_context.py -v
  （若无此测试，新建最小回归测试验证 fold 不破坏配对 + failed 消息保留 repair_plan）
Stop condition: 折叠逻辑通过单元测试 + 不破坏现有 ToolMessage 配对结构
```

### 任务包 P1：Context Budget 主动预警

```
Task name: context-budget-proactive-warn
Goal: 在 ReAct 循环每轮工具执行后，主动检查 all_messages token 占用；
      当达到 summarize_threshold 的 80% 时，触发预防性 maybe_summarize(force_level2=True)，
      而非等到 token_exceed 被动触发（被动触发时已有 LLM 调用失败开销）。
Allowed files:
  - backend\agent\chat_service.py（工具执行循环内新增预算检查）
  - backend\agent\memory\memory_context.py（可能新增预算检查 helper）
Forbidden files:
  - backend\config.py（不改阈值，复用现有 summarize_threshold）
  - backend\agent\memory\agent_db.py
Inputs:
  - 现有被动触发链：_classify_llm_error → token_exceed → _force_compress_and_retrim
  - 现有 _count_message_tokens（memory_context.py:118-145）
  - 现有 maybe_summarize(force_level2=True)
Outputs:
  - 新增 _check_context_budget(all_messages, conversation_id, new_start_idx) 函数：
    计算 token 占用，达阈值 80% 时调 maybe_summarize + build_context_messages 重载
  - 在 chat_service.py 工具执行循环末尾（~line 1506 后）调用该检查
  - 预警时记录 INFO 日志（可观测性）
Checkpoint: 预警触发后 all_messages 原地替换，主循环继续正常执行
Validation command:
  D:/anaconda3/envs/sam3/python.exe tests\agent_behavior_harness_eval.py
  （验证被动压缩仍可用，主动预警不破坏行为）
Stop condition: 主动预警逻辑通过 + 行为评估 false_success 保持 0/10
```

### 任务包 P2：verification 回传结构确认

```
Task name: verification-return-audit
Goal: 审计主控 ReAct 循环是否将 verification_agent 的完整输出摄入 context；
      若有完整 detail 泄漏到主控 messages，优化为只回传结论性摘要。
Allowed files:
  - backend\agent\chat_service.py（assign_agent_task 结果处理段）
  - backend\agent\team\validation_stage.py（可能调整回传结构）
Forbidden files:
  - verification_agent 判定逻辑本身
Inputs:
  - assign_agent_task / query_agent_task 工具
  - validation_stage.py 的 agent_validation 结构
Outputs:
  - 审计报告：主控摄入的是完整 verification detail 还是已摘要的 agent_validation
  - 若需优化：调整回传为 failure_type + repair_plan + 一句话根因
Checkpoint: verification 结果不膨胀主控 context
Validation command:
  人工审计 + D:/anaconda3/envs/sam3/python.exe tests\agent_behavior_harness_eval.py
Stop condition: 审计确认回传结构合理 OR 完成优化并通过回归
```

---

## Phase 5: 检查点定义

| 检查点 | 时机 | 判定标准 | 失败动作 |
|---|---|---|---|
| **目标检查点** | 每个任务包完成后 | 改造是否仍服务于"长任务上下文鲁棒性"目标 | 偏离则回滚任务包 |
| **边界检查点** | 每个任务包完成后 | Do not touch 清单中的文件未被修改 | `git diff --name-only` 核对 |
| **证据检查点** | 每个任务包完成后 | 单元测试 + 评估回归通过 | 修正后重验，不达标不进下一包 |
| **回归检查点** | P0+P1 全完成后 | 任务规划 3/3、行为 0/10、思维链 PASS | 定位回归源，在边界内修正 |
| **交接检查点** | 若 context 膨胀 | 更新本文档 Handoff Notes 段 | 提供干净续接点 |

---

## Phase 6: 证据标准

每个任务包必须提供以下证据之一，否则视为未完成：

### P0 证据
```
输入上下文：构造含 failed agent_validation 的旧 ToolMessage 序列
期望行为：折叠后保留 failure_type + repair_plan.next_action
实际折叠结果：_（单元测试输出）_
配对完整性：tool_call_id 与 tool_calls 配对无断裂（单元测试断言）
回归：行为评估 false_success_rate=0/10（输出 JSON）
```

### P1 证据
```
模拟场景：构造接近 summarize_threshold 80% 的消息历史
期望行为：主动触发 maybe_summarize，而非等 token_exceed
实际行为：_（日志输出主动压缩记录）_
token 对比：主动压缩后 token 占用 vs 被动触发路径（日志对比）
回归：行为评估 false_success_rate=0/10 + 任务规划 3/3（输出 JSON）
```

### P2 证据
```
审计结论：主控摄入的 verification 结果结构是（完整 detail / 已摘要 agent_validation）
context 占用：单次 verification 回传的 token 数（如可测）
优化状态：无需优化 / 已优化为结构化摘要
```

---

## Phase 7: 交接说明

```
Current goal: 借鉴 Claude Code 架构增强 Agent-RSCD 上下文鲁棒性
Current decision: P0+P1+P2 三个任务包，P0 优先（成本最低收益最明确）
Completed work: 代码地图核实完成，确认现有压缩体系已较成熟，借鉴方向从"建压缩"调整为"增强细节"
Open work:
  - P0: fold_tool_messages 折叠粒度增强（failed 消息保留 repair_plan）
  - P1: context budget 主动预警（80% 阈值预防性压缩）
  - P2: verification 回传结构审计
Known risks:
  - P0 风险：折叠逻辑解析 content JSON 可能遇到非标准结构（需 try/except 降级到原 80 字折叠）
  - P1 风险：主动压缩可能触发不必要的 Level 2 物理删除（需确认 force_level2 的删除边界）
  - P2 风险：审计可能发现当前回传已合理（则 P2 无需改动，标记为确认项）
Files touched: 待实施时更新
Tests passed: 待实施时更新
Next task package: P0 fold-granularity-enhancement
```

---

## 附录：借鉴价值重评估

基于代码地图核实，原分析的借鉴优先级需调整：

| 原优先级 | 借鉴点 | 核实后重新评估 | 调整理由 |
|---|---|---|---|
| ~~P0~~ | Tool result clearing | **已实现**（fold_tool_messages） | 降级为"折叠粒度增强" |
| ~~P1~~ | Token 预算检查 + 自动 compaction | **已有被动 compaction**，升级为主动 | 保留 P1，调整为"被动→主动" |
| P2 | Subagent 回传分级摘要 | 维持 P2 | 审计性质，可能无需改动 |
| ~~P3~~ | Hooks 框架抽取 | **不借鉴** | 场景固定，过度工程 |

**核心结论**：Agent-RSCD 的上下文管理体系在架构层面已不弱于 Claude Code，本次借鉴的本质是**细节增强**而非架构补齐。实施前应评估投入产出比——如果现有体系的实际表现（长任务不爆 context）已可接受，P0+P1 可作为可选优化而非必须项。
