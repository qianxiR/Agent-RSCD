# 工程化设计对照与架构更新分析

> 本文对照 `工程化.md`（面向自然资源行业的 Agent 工程化数据结构设计）与当前后端 Agent 的**实际代码实现**，逐项核验每条建议的合理性，并给出架构更新的优先级建议。
>
> 所有"代码现状"结论均附 `file_path:line_number`，可追溯到实际实现。

---

## 1. 总体判断

`工程化.md` 的**方向正确**——自然资源 Agent 与通用 Agent 的根本区别在于必须有产物、证据、血缘和验证的语义层。但其**落地节奏偏激进**：把 18 张新表当作"第一版建议"（`工程化.md` §13），与当前系统**刻意复用 `ai_task` 表**的克制设计存在张力。

当前架构的真实问题不是"表太少"，而是**三个语义断裂**：

| 断裂点 | 代码现状 | 后果 |
|---|---|---|
| **产物血缘断裂** | 工具返回字符串路径（`artifact_path` / `output_path`），散落在 `data` JSONB（`samseg_tools.py:535`）；`artifacts` 字段仅存于 agent 层 worker 契约（`result_contract.py:45`），model 层工具不带 | 无法追溯"这个变化图斑来自哪两期影像、由哪个工具产出" |
| **证据分散** | 证据有三处来源：① step.evidence 列表（`task_state.py:171`）② `ai_task.verification` JSONB（`agent_db.py:290`）③ 工具自带 `verification.evidence`（`_verification.py:48`） | 完成门判断时需跨处拼装 |
| **任务语义模糊** | 业务意图靠 prompt（`static_template.py`）+ 工具描述，无 `task_spec`；`roadmap.md` 自述"尚未形成统一的结构化任务模型" | 意图解析、技能选择、验证策略无法共享同一结构化输入 |

`工程化.md` 的大多数建议都是在补这三条断裂——**方向合理**。问题只在"一次建 18 张表"。

---

## 2. 工程化建议逐项核验

评估分三类：✅ **命中真实缺口**（应优先做）/ ⚠️ **方向对但需调整** / ❌ **过度或过早**（不建议现在做）。

### 2.1 ✅ 命中真实缺口（应优先做）

| 工程化建议 | 代码现状（file:line） | 评估 |
|---|---|---|
| **`artifact` 统一产物模型**（`工程化.md` §6.1） | 无 `artifact` 表；路径散落 `data` JSONB（`samseg_tools.py:535`）；`artifacts` 字段仅存于 agent 层 worker 契约（`result_contract.py:45`），model 层工具结果不带 | **真实缺口**。产物血缘是自然资源 Agent 的核心差异化，当前完全缺失 |
| **`evidence_record` 独立证据**（`工程化.md` §6.2） | 证据三处分散（见 §1）；step.evidence 只是 `{tool_name, result_type, validation_status, summary}` 摘要（`task_state.py:171`），无 `metric/value/unit` | **真实缺口**。完成门判断与报告生成需要可查询的结构化证据 |
| **`monitoring_task_spec` 任务模型**（`工程化.md` §2.1） | 无；意图靠 `static_template.py` 的 prompt + 工具描述；`roadmap.md` 自认缺失 | **真实缺口**。与路线图阶段 2 一致 |
| **类别转移矩阵**（`工程化.md` §8.2） | `change_mask` 只编码"变化后类别"（`runner.py:773`），丢失"从什么变来"；无 from→to 矩阵 | **真实缺口**。当前只能做二值变化 + 分类变化，业务判定（如耕地转建筑）缺前提 |
| **plan 版本链 / `plan_revision`**（`工程化.md` §4.3） | plan 是单版本内存 dict（`task_state.py:39`），重规划直接覆盖，无 `parent_plan_id` 历史 | **真实缺口**。可追溯性不足 |
| **成本 / GPU 监控**（`工程化.md` §11.3） | token 只 `logger.info` 不落库（`chat_service.py:764`）；`run_change_detection` 连 `performance` 都没埋点（对比 `run_segment` 的 `runner.py:402`）；全库无 GPU 采集 | **真实缺口**。评估体系第 6 阶段无数据源 |

### 2.2 ⚠️ 方向对，但需调整

| 工程化建议 | 问题 | 调整建议 |
|---|---|---|
| **Tool Result 顶层加 `quality`/`execution`**（`工程化.md` §5.1） | schema 合理，但**数据源缺失**：`quality.confidence` 当前只有 `classify_change_type` 产出（`runner.py:577`），且被工具层强制覆盖为 rule 源（`samseg_tools.py:635`）；`execution.gpu_seconds` 全库无采集 | **先补采集再扩 schema**，否则字段空置 |
| **`execution.budget`**（`max_iterations:15` 等，`工程化.md` §4.1） | 与现有设计哲学冲突：`chat_service.py:974` 明确注释"不再设工具调用轮次上限"，靠用户手动停止 | budget 应作**安全护栏**（`max_repair_attempts` 已在 `repair_policy.py` 体现），不应作硬终止，否则回退产品决策 |
| **`temporal_dataset_pair` 一致性检查**（`工程化.md` §3.2） | 当前双时相靠 `detect_change(t1_path, t2_path)` 两字符串（`samseg_tools.py:546`），无 CRS / 分辨率 / 配准校验 | 方向对，但可**先在工具内做校验**（返回 warning evidence），不必先建表 |
| **失败指纹 `failure_fingerprint`**（`工程化.md` §7.3） | 概念已实现，但命名分散：`lesson_policy.py:34` 叫 `failure_signature`；`retry_guard.py:29` 叫 `retry_signature`（且**未被调用**） | 统一命名，并**激活 retry_guard**（当前防机械重试实际靠 plan step status，retry_guard 是死代码） |

### 2.3 ❌ 过度或过早（不建议现在做）

| 工程化建议 | 为什么过早 |
|---|---|
| **一次新增 18 张表**（`工程化.md` §13） | 当前刻意复用 `ai_task`（plan 挂 `task_type='agent_plan'`，`agent_db.py:1808`）是**优点**。18 表会引入双写一致性风险。应分 3 批（见 §5） |
| **`plan_step` / `repair_plan` 独立表**（`工程化.md` §4.2、§7.2） | 当前 `plan.steps[]` 内存模型 + `repair_policy.py` 规则表已能工作。独立表会与内存 plan 双写，除非先解决持久化版本链 |
| **`worker_assignment` 表 + `permission_scope`**（`工程化.md` §10.1） | 当前仅 2 个**固定** worker（`verification_agent` / `report_agent`，`agent_roles.py:15`），且 verification 是**同步内嵌调用**（`observation_builder.py:75`），非独立派发。复杂权限模型空转 |
| **`segmentation_result` / `change_detection_result` 独立表**（`工程化.md` §8） | 可作为 `artifact` 的 `artifact_type` 子类型，不必独立表 |
| **`tool_execution` 独立表**（`工程化.md` §5.2） | 与 `ai_task` 字段高度重叠（`ai_task` 已有 tool_name / input / output / status / elapsed，`agent_db.py:262`）。重复 |

---

## 3. `工程化.md` 的两个内在矛盾

1. **"不要求一次性重写"（§13 结尾）vs"第一版建议新增 18 张表"（§13 开头）**。建议以结尾的克制原则为准，落地分批（见 §5）。
2. **budget 硬上限 vs 无上限 ReAct**。`工程化.md` §4.1 给了 `max_iterations:15`，但代码明确放弃轮次上限（`chat_service.py:974`）。需明确 budget 的语义是"护栏"还是"终止"——建议为护栏。

---

## 4. 架构更新优先级

基于"先补语义断裂、低破坏性优先"的原则：

| 优先级 | 更新点 | 涉及代码（file:line） | 价值 |
|---|---|---|---|
| **P0** | `_result.py` 的 `build_success` / `build_frontend_action` 增加**可选** `artifacts:[]`、`evidence:[]` 字段 | `_result.py:59`、`_result.py:114` | 补产物血缘断裂；旧工具不强制改，新工具自描述 |
| **P0** | 新增**轻量 `artifact` 表**（`artifact_id, task_id, type, path, crs, lineage_jsonb`） | `agent_db.py:129`（init_schema） | 由 chat_service 在重工具成功后登记；step 级 evidence 暂留 JSONB 不强行迁移 |
| **P1** | `run_change_detection` 补 `performance` 耗时埋点 | `runner.py:417`（对齐 `run_segment` 的 `runner.py:299-409`） | 为评估供数 |
| **P1** | `_summarize_task_output` 提取 `performance` 到 `ai_task.output` | `chat_service.py:1261` | 让细分耗时可查 |
| **P1** | token `usage_metadata` 落 `ai_task` 或独立 metrics 表 | `chat_service.py:764`（当前只打日志） | 成本评估数据源 |
| **P2** | `change_mask` 增加"变化前类别"通道，或单独输出 `transition_matrix` | `runner.py:773` | 支撑 from→to 业务判定（耕地转建筑等） |
| **P3** | `monitoring_task_spec` 先以 JSON 存入 `plan` 或 `ai_task.input` | `task_state.py:39` / `agent_db.py:262` | 作为 skill selector 的结构化输入，验证价值后再决定是否建表 |
| **P4** | `plan` 增加 `version` + `parent_plan_id`，重规划保留历史 | `task_state.py:39` | 可复用多条 `task_type='agent_plan'` 的 ai_task 实现 |

---

## 5. 推荐落地分批

避免一次建 18 表的双写风险，按"补断裂 → 补语义 → 补评估"三批推进：

```
第一批（补语义断裂，低破坏性）
  artifact 表 + _result.py artifacts/evidence 可选字段
  → 解决产物血缘断裂

第二批（补任务语义，中等）
  monitoring_task_spec（先 JSON 不建表）+ plan 版本链
  → 解决任务语义模糊 + 规划可追溯

第三批（补评估数据源，评估体系成熟后）
  evaluation_run / metric_result + token/GPU 采集
  → 支撑成本与效果一体化基线
```

每批独立可验证，前一批稳定后再推进下一批。`plan_step` / `repair_plan` / `worker_assignment` / `tool_execution` 等独立表**暂不建**，等第一批的 artifact 模型验证后，按需以 `artifact_type` 子类型或 JSONB 字段承载。

---

## 6. 与现有系统的兼容性确认

本方案与现有以下设计兼容，不要求重写：

| 现有设计 | 兼容性 |
|---|---|
| ReAct 循环（`chat_service.py:979`，无轮次上限） | ✅ budget 作护栏不作终止，不冲突 |
| WebSocket 双向通信（`ws_manager.py`） | ✅ 不动协议 |
| 三层记忆 + 消息 DAG（`memory_context.py` / `agent_db.py:201`） | ✅ 不动记忆结构 |
| 复用 `ai_task` 持久化（`task_state.py:270`） | ✅ artifact 表与 ai_task 通过 `task_id` 关联，不替代 |
| verification_agent 确定性 worker（`verification_worker.py:102`） | ✅ evidence 模型为其提供结构化输出，不改变其确定性原则 |
| 技能编排（`model/skills/`） | ✅ task_spec 作为 skill selector 输入，不改变技能契约 |
| 47 个工具 + 10 大类（`tool_registry.py`） | ✅ artifacts/evidence 字段可选，旧工具不强制改 |
