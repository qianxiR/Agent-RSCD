# 07 Harness Engineering

## 1. 目标

把一次 Agent 运行拆成标准生命周期：输入、准备、执行、停止、结果、事件、错误、恢复。上层不依赖 ChatService 内部实现。

## 2. 当前基线

### 2.1 协议

[`backend/agent/harness/base.py`](../../backend/agent/harness/base.py)

```text
HarnessContext
HarnessEvent
AgentHarness
```

`HarnessEvent` 定义：

```text
thinking / tool_call / tool_result / content / done / error
```

### 2.2 适配器

[`backend/agent/harness/chat_harness.py`](../../backend/agent/harness/chat_harness.py)

```text
ChatHarness.run()
  -> ensure_mcp_tools_loaded()
  -> 构造 ToolChatRequest
  -> 调用 ChatService.tool_chat_ws()
```

`ChatHarness.stop()` 调用 `ws_manager.cancel_task()`。

### 2.3 当前接线

[`backend/api/ws.py:150`](../../backend/api/ws.py#L150)

```text
ChatHarness(_chat_service, ws_manager).run(context, _noop_emit)
```

`emit` 当前是空实现，事件仍由 `ChatService` 直接通过 `ws_manager` 推送。

### 2.4 运行状态

[`backend/agent/runtime/run_state.py`](../../backend/agent/runtime/run_state.py)

已有：

```text
agent_run
status: running/paused/completed/stopped/failed
messages, plan, tasks, stream_state
```

## 3. 差距

1. `HarnessEvent` 已定义，但主链路没有使用；事件路径分裂。
2. 没有统一 `prepare`、`before_tool`、`after_tool`、`finalize` 钩子。
3. 没有 harness 级运行记录；`agent_run` 只存快照。
4. 错误分类、重试、停止原因分散在 ChatService。
5. 没有 harness 注册表；只有 `chat`。
6. 没有事件回放。
7. 没有运行级 trace id。
8. 没有测试覆盖 `HarnessContext` 到事件流的完整路径。

## 4. 目标设计

### 4.1 生命周期

```text
prepare
  -> run
      -> before_iteration
      -> before_tool
      -> after_tool
      -> after_iteration
  -> finalize
  -> stop
```

### 4.2 事件

```json
{
  "type": "tool_result",
  "conversation_id": "...",
  "run_id": "...",
  "seq": 12,
  "payload": {}
}
```

要求：

```text
所有事件经 emit 输出
事件带 run_id + seq
事件可落库
前端只消费事件，不直接依赖 ChatService
```

### 4.3 运行记录

```text
agent_run
  run_id, conversation_id, session_id
  harness_name, status, stop_reason
  started_at, finished_at
  iterations, tool_calls, tokens
  trace_id
```

### 4.4 错误与停止

```text
error_type: llm/network/tool/validation/cancelled/internal
stop_reason: completed/user_stop/disconnect/budget/error
```

停止原因必须与状态一致：

```text
user_stop -> stopped
disconnect -> paused
budget -> stopped
error -> failed
```

## 5. 改造任务

### P0

- [ ] 让 ChatService 通过 `emit` 输出事件，移除 `_noop_emit`。
- [ ] 增加 `run_id`、`seq`、`trace_id`。
- [ ] 增加 `before_tool` / `after_tool` 钩子。
- [ ] 增加 harness 级测试，覆盖 thinking/tool/content/done/error。
- [ ] 统一停止原因枚举。

### P1

- [ ] 增加 harness 注册表，支持 `chat`、`plan` 等实现。
- [ ] 增加事件落库与回放。
- [ ] 增加运行级指标。
- [ ] 增加 `prepare/finalize` 钩子。

### P2

- [ ] 增加多 harness 并存。
- [ ] 增加事件重放调试工具。
- [ ] 增加运行对比。

## 6. 验收标准

```text
1. WebSocket 所有下行事件均由 Harness emit 产生。
2. 每个事件有 run_id 和 seq。
3. 停止原因与 agent_run.status 一致。
4. 运行结束可查询 iterations/tool_calls/tokens/trace_id。
5. 事件可回放。
6. ChatHarness 现有行为不回归。
```

## 7. 风险

- 事件改造会触及 ChatService 大量推送点。
- 事件落库会增加写入量。
- 多 harness 并存会增加路由复杂度。
