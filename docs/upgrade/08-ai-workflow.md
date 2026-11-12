# 08 AI 工作流

## 1. 目标

把确定性长任务从 ReAct 自由编排中抽出来，转为可验证的 DAG 工作流。工作流负责节点依赖、输入输出、验收和失败恢复；LLM 负责意图识别与参数补齐。

## 2. 当前基线

### 2.1 PlanGraph

[`backend/agent/planner/`](../../backend/agent/planner)

```text
schema.py      PlanGraph / PlanNode
templates.py   3 个模板
validator.py   结构校验
runtime.py     Shadow / Plan-first 接入
plan_adapter.py 转 plan_state
```

3 个模板：

```text
semantic_segmentation
change_detection
geoserver_publish
```

### 2.2 执行器

[`backend/agent/executor/dag_executor.py`](../../backend/agent/executor/dag_executor.py)

支持：

```text
节点依赖
最大工具调用预算
节点成功/失败/阻塞
```

### 2.3 当前接入

```text
AGENT_PLAN_SHADOW=true
AGENT_PLAN_FIRST=false
AGENT_PLAN_EXECUTE=false
```

- Shadow：只生成计划、校验、写日志，不改变 ReAct。
- Plan-first：仅变化检测请求，在 ReAct 前生成显式计划。
- Plan-and-Execute：开启后按计划逐步执行，规划来源为技能契约 > 模板 > LLM 规划器。
- 三者都关闭时，其他任务仍走自由 ReAct。

### 2.4 当前问题

- DAG Executor 不是主执行路径，Plan-and-Execute 使用自己的步骤调度。
- 自由 ReAct 路径的计划仍只由模板覆盖。
- 节点失败没有补偿事务。
- 没有人工确认节点。
- 没有工作流版本与迁移。
- 没有节点级重试策略。
- 没有工作流级 trace（Plan-and-Execute 仅写 JSONL 轨迹）。

## 3. 目标设计

### 3.1 工作流定义

```json
{
  "workflow_id": "change_detection",
  "version": "2.0",
  "nodes": [
    {
      "id": "n1_validate",
      "tool": "validate_inputs",
      "inputs": {},
      "acceptance": ["status=passed"],
      "required": true
    }
  ],
  "budget": {"max_tool_calls": 8, "max_seconds": 600}
}
```

### 3.2 执行模型

```text
意图识别
  -> 选择工作流
  -> 补齐必填输入
  -> 校验 DAG
  -> 拓扑执行
  -> 节点验收
  -> 失败重试/回退
  -> 完成门
```

### 3.3 节点契约

每个节点：

```text
id, tool, inputs, outputs,
required, acceptance, retry, timeout,
fallback, human_approval
```

### 3.4 人工确认

高风险节点前置确认：

```text
删除图层
覆盖已有产物
发布到生产工作区
批量处理
```

确认结果写入节点状态，不写入用户消息。

## 4. 改造任务

### P0

- [ ] 将 DAG Executor 接入至少一个非变化检测任务。
- [ ] 节点增加超时、重试、fallback。
- [ ] 节点验收失败阻断下游。
- [ ] 增加工作流执行 trace。
- [ ] 增加 Plan-first 灰度开关按任务类型配置。

### P1

- [ ] 增加通用任务到工作流的映射。
- [ ] 增加人工确认节点。
- [ ] 增加工作流版本与迁移。
- [ ] 增加节点级成本统计。

### P2

- [ ] 评估 LLM 生成工作流草案，但执行前必须校验。
- [ ] 增加工作流回放与对比。
- [ ] 增加跨工作流依赖。

## 5. 验收标准

```text
1. 工作流可按拓扑顺序执行。
2. 节点失败不会静默跳过。
3. 节点有超时、重试、fallback。
4. 工作流执行有 trace 和预算。
5. 高风险节点可人工确认。
6. Shadow 与 Plan-first 结果可对比。
```

## 6. 风险

- 过早替换 ReAct 会降低开放任务适应能力。
- 工作流模板过细会增加维护成本。
- 人工确认会改变现有前端交互协议。
