> 🗄️ **已归档 · 2026-07-09**：本升级设计方案已完成历史使命，不再作为活跃规划。
> - 第一阶段 `verification_agent` 已落地，§3 目标架构被实际代码超越（额外落地 `repair_policy` / `report_worker` / `validation_stage`）。
> - §9 第一阶段验收标准已全部满足；当前能力、边界与后续建设路线统一见 [`agent.md`](../agent/roadmap.md)。
> - 本文只保留历史方案，不再作为当前实施依据。
> - 本文档仅作历史方案留存。

# 多 Agent 后端升级设计

本文定义遥感 Agent 后端从“单 Agent ReAct”升级为“主控 Agent + 专业 worker”的分阶段方案。目标是增强任务拆解、执行验收和长任务可观测性，同时保留现有 `AgentChatService`、三层记忆、工具注册表和 WebSocket 协议的稳定结构。

## 1. 当前基线

当前后端是单 Agent ReAct 架构：

```text
用户输入
  -> AgentChatService
  -> build_context_messages
  -> LLM tool_calls
  -> 后端执行工具
  -> ToolMessage 回灌
  -> LLM 继续推理或最终回复
```

已有能力：

| 能力 | 当前实现 |
|---|---|
| 推理循环 | `backend/agent/chat_service.py` 手写 ReAct 循环 |
| 上下文 | `backend/agent/memory/memory_context.py` 三层记忆 + trim + 工具结果折叠 |
| 工具 | `backend/model/tools/tool_registry.py` 注册表 + LLM 白名单 |
| 验证 | `backend/model/tools/_verification.py` 产物存在性、格式、页数、要素数校验 |
| 任务 | `backend/agent/memory/agent_db.py::ai_task` 记录重工具状态和日志 |

当前短板：

- 主控 Agent 同时负责理解、执行、验收、汇总，职责过重。
- 工具空间较大，复杂任务容易误选工具或过早收尾。
- 验证机制虽然存在，但仍嵌在工具返回和传输兜底里，没有独立的验收角色。
- 长任务虽然写入 `ai_task`，但没有专业 worker 的生命周期管理。

## 2. 升级目标

升级目标不是做开放式 Agent 群聊，而是做受控分工：

```text
主控 Agent
  -> 专业 worker
  -> 结构化任务结果
  -> 主控 Agent 汇总与最终回复
```

设计原则：

- 保留现有主控 ReAct 主循环。
- worker 不直接面向用户。
- worker 不允许无限递归分配子任务。
- worker 只能访问与角色匹配的工具或能力。
- 任务状态只使用一套数据源，优先复用 `ai_task`。
- 第一阶段先把执行与验收分离，再逐步拆分遥感、发布、报告 worker。

## 3. 目标架构

```text
backend/
├── agent/
│   ├── chat_service.py
│   ├── memory/
│   ├── prompt/
│   ├── runtime/
│   ├── tools/
│   │   ├── memory_tools.py
│   │   └── team_tools.py
│   └── team/
│       ├── agent_roles.py
│       ├── task_manager.py
│       ├── verification_worker.py
│       └── result_contract.py
├── model/
│   └── tools/
└── data/
```

新增层职责：

| 模块 | 职责 |
|---|---|
| `agent/team/agent_roles.py` | 定义 worker 角色、工具白名单和调度边界 |
| `agent/team/task_manager.py` | 创建、查询、取消 worker 任务 |
| `agent/team/verification_worker.py` | 执行验证 Agent 的具体校验 |
| `agent/team/result_contract.py` | 统一 worker 输出结构 |
| `agent/tools/team_tools.py` | 暴露给主控 Agent 的调度工具 |

## 4. 第一阶段：verification_agent

第一阶段只引入验证 worker：

```text
主控 Agent
  -> assign_agent_task(agent_role="verification_agent")
  -> verification_agent 执行客观验收
  -> query_agent_task 查询结果
  -> 主控 Agent 根据 passed / failed / warning 决定继续或收尾
```

### 4.1 verification_agent 输入

```json
{
  "user_goal": "用户目标",
  "tool_name": "segment_image",
  "tool_result": {},
  "artifact_path": "可选产物路径",
  "artifact_paths": ["可选多个产物路径"],
  "expected_type": "可选产物类型",
  "layer_name": "可选图层名"
}
```

### 4.2 verification_agent 输出

```json
{
  "agent_role": "verification_agent",
  "status": "passed | failed | warning",
  "summary": "验收结论",
  "evidence": {},
  "issues": [],
  "recommendation": "continue | retry | switch_tool | report_error",
  "repair_plan": {
    "failure_type": "missing_artifact | empty_vector | invalid_arguments | ...",
    "root_cause_hint": "根因提示",
    "next_action": "下一步修正动作",
    "recommended_tool": "建议调用的工具",
    "parameter_changes": {},
    "fallback_tools": [],
    "stop_condition": "何时停止重试并向用户报错"
  }
}
```

### 4.3 验证规则

| 输入信号 | 验证方式 |
|---|---|
| `tool_result.type == error` | 直接判定 failed |
| `tool_result.verification.ok == false` | 判定 failed |
| 产物路径存在 | 调用 `verify_by_path()` |
| 多产物路径 | 逐个校验，任一失败则 failed |
| 无路径且无 verification | warning，要求主控 Agent 补充证据或谨慎收尾 |

第一阶段 verification_agent 是确定性 worker，不额外调用 LLM。这样做是为了把验收标准稳定落地，避免“另一个模型也误判成功”。

### 4.4 修复策略

验证 Agent 的核心作用不是只指出失败，而是把失败转换为可执行修复策略：

```text
工具结果
  -> verification_agent 验收
  -> repair_policy 归因
  -> repair_plan 回灌 ToolMessage
  -> 主控 Agent 按 repair_plan 继续 ReAct
```

第一阶段内置规则：

| failure_type | next_action | 说明 |
|---|---|---|
| `tool_error` | `diagnose_error_then_retry` | 根据 `msg/error_type` 修正参数或切换工具 |
| `missing_artifact` | `retry_same_tool_with_checked_output_path` | 检查输出目录后重跑原工具 |
| `empty_or_tiny_artifact` | `rerun_and_check_upstream_data` | 检查上游数据是否为空, 必要时换格式 |
| `empty_vector` | `return_to_segmentation_with_same_target_and_synonyms` | 回到分割阶段，保持原目标类别并增加同义词/别名表达 |
| `incomplete_shapefile_zip` | `reexport_shapefile_or_fallback_geojson` | 重导 Shapefile 或改用 GeoJSON |
| `unreadable_artifact` | `regenerate_with_format_check` | 明确格式后重新生成 |
| `permission_or_path_error` | `move_to_allowed_workspace` | 改用会话目录或 sandbox `/workspace` |
| `invalid_arguments` | `repair_arguments_before_retry` | 按 schema 和上下文补齐参数 |
| `insufficient_evidence` | `collect_evidence_before_final_answer` | 补充客观证据, 不允许直接收尾 |

`chat_service` 会把验证摘要和 `repair_plan.next_action / recommended_tool / stop_condition`
拼入工具结果 `summary`，让主控 Agent 在下一轮 Observation 中直接看到修正策略。

## 5. 后续阶段

### 5.1 遥感推理 worker

角色：`rs_agent`

职责：

- 选择 `segment_image`、`detect_change`、`understand_image`。
- 执行掩膜分析、矢量化前置检查。
- 输出遥感推理产物和统计摘要。

### 5.2 发布 worker

角色：`publish_agent`

职责：

- 发布 GeoServer 图层。
- 触发前端加载、定位、显隐控制。
- 验证图层服务可访问。

### 5.3 报告 worker

角色：`report_agent`

职责：

- 生成统计图、表格、报告。
- 验证 PDF / DOCX / XLSX 可读性。
- 输出可下载产物清单。

### 5.4 主控 Agent 变化

主控 Agent 从“亲自执行所有细节”升级为：

```text
理解目标 -> 拆任务 -> 派发 worker -> 查询结果 -> 调用验证 -> 汇总回复
```

## 6. 任务状态设计

复用 `ai_task`，补充多 Agent 字段：

| 字段 | 含义 |
|---|---|
| `parent_task_id` | 父任务 ID |
| `agent_role` | worker 角色 |
| `goal` | 子任务目标 |
| `verification` | 验证结果 |

原字段继续使用：

| 字段 | 含义 |
|---|---|
| `input` | worker 输入 |
| `output` | worker 输出 |
| `status` | pending / running / done / failed / cancelled |
| `progress` | 进度 |
| `error` | 失败原因 |

## 7. 调度工具

主控 Agent 可用三个工具：

| 工具 | 作用 |
|---|---|
| `assign_agent_task` | 派发 worker 任务 |
| `query_agent_task` | 查询 worker 状态、输出和日志 |
| `cancel_agent_task` | 取消 worker 任务 |

工具放在 `backend/agent/tools/`，因为它们操作 Agent 自身的任务系统，不属于业务工具箱。

## 8. 风险控制

- 第一阶段只有 `verification_agent`，不开放 worker 递归派发。
- worker 状态写入 `ai_task`，不引入第二套任务表。
- 主控 Agent 仍是唯一用户可见回复者。
- worker 输出必须是结构化结果，不能直接替代最终回复。
- 后续每新增一个 worker，都必须绑定工具白名单和输出契约。

## 9. 验收标准

第一阶段完成标准：

1. 后端启动时 `ai_task` 自动补齐多 Agent 字段。
2. `assign_agent_task(agent_role="verification_agent", ...)` 能创建并执行验证任务。
3. `query_agent_task(task_id)` 能返回状态、输出、verification 和日志。
4. `cancel_agent_task(task_id)` 能取消未完成任务或返回已完成状态。
5. 不影响现有 ReAct 工具执行、任务进度条和记忆链路。
