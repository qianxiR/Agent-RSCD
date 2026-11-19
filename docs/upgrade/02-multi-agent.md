# 02 多 Agent 协作系统

## 1. 目标

保留单一主控 Agent，增加受控 worker 运行时。不是自由对话式多 Agent，不是多个 LLM 自主协商。

```text
主控 Agent（唯一决策中心、唯一用户回复者）
  ├─ verification_agent（确定性验收）
  └─ report_agent（受控异步报告）
```

当前不新增专业 worker。本专题只改造这两个 worker 的运行时契约。

## 2. 当前基线

### 2.1 角色

[`backend/agent/team/agent_roles.py`](../../backend/agent/team/agent_roles.py)

```text
verification_agent  工具白名单为空，确定性规则验收
report_agent        工具白名单：generate_monitor_report、visualize_vector
```

未开放 `rs_agent`、`publish_agent` 等角色。

### 2.2 任务运行时

[`backend/agent/team/task_manager.py`](../../backend/agent/team/task_manager.py)

```text
assign_agent_task  -> ai_task 建任务 -> asyncio.create_task
query_agent_task   -> 读取任务详情与日志
cancel_agent_task  -> 取消运行中的 worker
```

当前 worker 路由是 `if agent_role == ...` 硬编码。

### 2.3 结果契约

[`backend/agent/team/result_contract.py`](../../backend/agent/team/result_contract.py)

worker 返回统一字段：

```text
agent_role, task_id, status, summary, evidence,
artifacts, metrics, issues, recommendation, handoff_notes
```

### 2.4 当前任务量

```text
ai_task：16 条（含历史测试与真实任务）
已配置 worker 角色：2 个
```

## 3. 差距

1. 角色路由硬编码，新增角色必须改 `task_manager.py`。
2. 角色元数据只有工具白名单，没有输入契约、输出契约、超时、并发、重试策略。
3. worker 之间不能传递中间结果，只能通过 `ai_task` 和主控转交。
4. 没有 worker 级上下文隔离；所有 worker 共享进程环境。
5. 没有并发上限、队列、优先级、成本统计。
6. 没有 worker 事件流；只能轮询 `ai_task`。
7. 没有 worker 级权限校验；工具白名单未在执行时强制。
8. 没有多 worker 任务图；只能单层派发。

## 4. 目标设计

### 4.1 角色注册表

每个角色声明：

```json
{
  "agent_role": "report_agent",
  "mode": "deterministic|llm",
  "goal": "生成报告",
  "required_inputs": ["segmentation_result"],
  "output_contract": "worker_result_v1",
  "tool_allowlist": ["generate_monitor_report", "visualize_vector"],
  "timeout_seconds": 300,
  "max_concurrency": 2,
  "max_retries": 1,
  "requires_verification": true
}
```

### 4.2 任务图

```text
main_task
  ├─ verification_task
  ├─ report_task
  └─ publish_task
```

每个节点记录：

```text
task_id, parent_task_id, role, status,
inputs, outputs, evidence, error, started_at, finished_at
```

`ai_task` 继续作为唯一任务状态源，不新增第二套任务表。

### 4.3 执行模型

```text
主控创建任务图
  -> 入队
  -> worker runner 取任务
  -> 校验角色/输入/工具白名单
  -> 执行
  -> 写 result_contract
  -> 主控读取结果，继续或收尾
```

禁止：

```text
worker 直接回复用户
worker 自主派发新 worker
worker 调用白名单外工具
worker 修改长期记忆
```

### 4.4 观测

每个任务记录：

```text
queue_wait_ms, run_ms, tokens, tool_calls,
retry_count, error_type, result_status
```

## 5. 改造任务

### P0

- [ ] 新增 `WorkerSpec`，集中声明角色、契约、超时、并发、重试。
- [ ] `task_manager` 改为按 `WorkerSpec` 路由，不再硬编码 `if/elif`。
- [ ] 执行前校验输入契约和工具白名单。
- [ ] 增加并发上限与队列。
- [ ] 增加 worker 超时和取消。
- [ ] 为 `assign_agent_task`、`query_agent_task`、`cancel_agent_task` 增加测试。

### P1

- [ ] 增加任务图 `parent_task_id` 链路查询。
- [ ] 增加 `worker_result` 校验器。
- [ ] 增加 worker 事件：`queued/running/succeeded/failed/cancelled`。
- [ ] 增加任务成本与耗时统计。
- [ ] 增加 report_agent 失败重试策略。

### P2

- [ ] 增加 verification/report 并行案例。
- [ ] 增加 worker 契约版本迁移。
- [ ] 评估 worker 运行时是否需要独立进程；默认不拆分。

## 6. 验收标准

```text
1. 新增 worker 只改注册表，不改 task_manager 主流程。
2. 每个 worker 有输入契约、输出契约、工具白名单、超时、并发上限。
3. worker 不能直接回复用户，不能自主派发 worker。
4. worker 任务可在 ai_task 中查到父子链、状态、耗时、错误。
5. 取消主任务时，其子 worker 可被取消。
6. verification_agent 仍为确定性实现。
```

## 7. 风险

- 过早增加 LLM worker 会放大不可控性和成本。
- worker 共享进程环境会带来资源竞争。
- 任务图过深会降低可观测性。
