# 09 Loop Engineering

## 1. 目标

把 ReAct 循环从“无限执行直到模型不再调用工具”改为有预算、有状态、可检测、可停止、可评估的运行循环。

## 2. 当前基线

### 2.1 主循环

[`backend/agent/chat_service.py:1216`](../../backend/agent/chat_service.py#L1216)

```text
while True:
  iteration += 1
  LLM.astream()
  if no tool_calls: 生成最终回复并结束
  else: 执行工具，回灌 ToolMessage，继续
```

`iteration` 只用于日志，不是终止条件。

### 2.2 重试

LLM 调用失败在同一 iteration 内重试：

```text
LLM_RETRY_MAX=3
rate_limit 退避 5 秒
token 超限先压缩历史
```

### 2.3 停止与恢复

- 用户 `stop_chat`：取消任务，回滚 checkpoint，状态 `stopped`。
- WebSocket 断开：状态 `paused`，保留 `agent_run` 快照。
- `resume_chat`：恢复消息、计划、工具、iteration、tasks、stream_state。

### 2.4 当前问题

1. 没有工具调用轮次上限或 token/cost 预算。
2. 没有循环检测；相同工具+相同参数可重复执行。
3. 终止条件只有“无 tool_calls / 用户停止 / 异常”。
4. 没有运行级预算告警。
5. 没有循环质量评估：无效轮次、重复调用、空转。
6. 没有 human-in-the-loop 暂停点。
7. 没有 loop trace 汇总。
8. 工具批次之间没有显式状态机。

### 2.5 已落地：Plan-and-Execute（`AGENT_PLAN_EXECUTE`，默认关闭）

主链路新增外层循环 `executor/plan_executor.py`，把"自由执行"改为"按计划步骤执行"：

- 计划来源：技能契约 > 模板 > LLM 规划器（`planner/llm_planner.py`）；
- 单步执行：`executor/step_executor.py`，工具绑定收窄到本步，轮次上限 `AGENT_STEP_MAX_TURNS=6`；
- 预算：`AGENT_PLAN_MAX_TOOL_CALLS=20`、`AGENT_PLAN_MAX_VERSIONS=2`，超限标记 `budget_exceeded` / `blocked`；
- 重规划：`create_plan_revision` + `revise_plan`，保留 completed 步骤，`plan_version+1`；
- 轨迹：`logs/agent-plan-execute.jsonl` 记录 step / replan 事件。

仍未实现：`max_iterations` 与显式状态机、计划路径的重复工具+参数检测（目前只有自由 ReAct 的 `retry_guard`）、指标写入 `agent_run`、human-in-the-loop 暂停点。

## 3. 目标设计

### 3.1 循环状态

```text
idle -> thinking -> tool_pending -> tool_running
     -> observing -> thinking
     -> finalizing -> completed
     -> paused / stopped / failed / budget_exceeded
```

### 3.2 预算

```text
max_iterations       默认 12
max_tool_calls       默认 20
max_tokens           默认 60000
max_wall_seconds     默认 900
max_same_failure     默认 2
```

任一预算触发：

```text
保存快照 -> 标记 budget_exceeded -> 返回可读说明
```

### 3.3 循环检测

检测：

```text
同一工具 + 同一参数重复
同一失败签名重复
连续空结果
连续无进展
```

处理：

```text
第一次：允许
第二次：注入修复约束
第三次：阻断并请求用户输入或停止
```

### 3.4 评估

```text
iterations_per_task
tool_calls_per_task
redundant_tool_rate
loop_detected_rate
budget_exceeded_rate
time_to_completion
```

## 4. 改造任务

### P0

- [ ] 增加 `max_iterations` 和 `max_tool_calls`。
- [ ] 增加循环状态机。
- [ ] 增加重复工具+参数检测。
- [ ] 增加预算超限快照与状态。
- [ ] 增加 loop trace 汇总。

### P1

- [ ] 增加 token / wall time 预算。
- [ ] 增加连续无进展检测。
- [ ] 增加 human-in-the-loop 暂停点。
- [ ] 增加循环指标写入 `agent_run`。

### P2

- [ ] 增加循环回放。
- [ ] 增加循环策略 A/B。
- [ ] 增加按任务类型的预算模板。

## 5. 验收标准

```text
1. 超过 max_iterations 停止，不无限循环。
2. 超过 max_tool_calls 停止。
3. 重复工具+参数被检测。
4. 预算超限有快照，可恢复或终止。
5. loop trace 可查询 iterations/tool_calls/redundant/stop_reason。
6. 正常任务不被预算误杀。
```

## 6. 风险

- 预算过紧会截断复杂任务。
- 循环检测误判会阻止合理重试。
- 状态机改造会触及停止、恢复、流式输出三条路径。
