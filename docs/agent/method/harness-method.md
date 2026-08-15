# Agent Harness 开发前范式

本文把当前项目采用的 AI Native 开发前控盘方法固化为可复用流程。目标是在写代码前先形成清晰边界、最小任务包、checkpoint 和证据标准, 让 Agent 能自主推进但不失控。

## 1. 核心问题

大模型有两个工程事实:

- 它是概率生成器。任务目标越模糊、自由空间越大, 越容易生成看似合理但不贴合项目的平均方案。
- 上下文很宝贵。长对话会腐烂, 旧方案、新方案、临时假设和当前结论会混在一起。

因此复杂开发任务不能直接进入实现。需要先建立外部真相源:

```text
目标
→ 边界
→ 代码地形
→ 最小任务包
→ checkpoint
→ 证据标准
→ handoff
```

## 2. 水流理论

Agent 不应该被逐行遥控, 也不能被完全放任。正确控制点是:

| 控制点 | 含义 |
|---|---|
| 边界 | 哪些目标必须完成, 哪些文件和行为不能碰 |
| checkpoint | 每个关键节点检查方向、证据和风险 |
| 风险通道 | 高风险动作必须有测试、灰度、回滚或人工确认 |

允许 Agent 在边界内探索路径, 但一旦出现越界、连续验证失败或违反目标, 必须回炉或转向。

## 3. 最小混沌单元

每次交给 Agent 的任务包要满足:

```text
小到可检查
大到可自治
```

任务太大, 失败会暴露得太晚; 任务太小, Agent 退化成执行命令的打字员。

每个任务包必须写清:

```text
任务名
目标
允许修改范围
禁止修改范围
输入
输出
checkpoint
验收命令
停止条件
```

## 4. 开发前流程

### 4.1 复述目标

在实现前必须先复述:

- 用户真正想达成什么。
- 为什么当前任务重要。
- 成功完成意味着什么。
- 哪些行为会被视为失败。

如果目标不清楚, 优先通过读取仓库和已有文档澄清, 不先问可从环境发现的问题。

### 4.2 定义边界

边界必须显式写出:

```text
In scope:
- ...

Out of scope:
- ...

Do not touch:
- ...
```

Agent 架构任务必须声明本次属于哪一层:

```text
prompt
context
runtime
team
memory
tools
skills
observability
```

### 4.3 构建 codemap

codemap 是代码地形索引, 不是全仓库摘要。

格式:

```text
关注点 → 文件 / 模块 → 为什么相关
```

示例:

```text
Tool Observation → backend\agent\runtime\observation_builder.py → 工具结果验证与 ToolMessage 构造
Repair Policy → backend\agent\team\repair_policy.py → failure_type 到 repair_plan 的映射
Prompt Rules → backend\agent\prompt\static_template.py → 主控 Agent 静态行为约束
```

### 4.4 切任务包

每个任务包都应能独立验证。不要把多个架构方向混在一个任务包里。

任务包模板:

```text
Task:
Goal:
Allowed files:
Forbidden files:
Inputs:
Outputs:
Checkpoint:
Validation command:
Stop condition:
```

### 4.5 定义 checkpoint

推荐 checkpoint:

| checkpoint | 检查内容 | 失败动作 |
|---|---|---|
| Goal checkpoint | 是否仍在解决原目标 | 回到目标复述 |
| Boundary checkpoint | 是否碰了无关文件或行为 | 停止并缩小范围 |
| Evidence checkpoint | 是否有测试、日志、产物或 diff 证据 | 不允许收尾 |
| Reflection checkpoint | 失败是否产生修正策略 | 回到 repair_plan |
| Handoff checkpoint | 新 Agent 是否能接手 | 补 handoff |

### 4.6 定义证据标准

不要接受“完成了”, 必须要证据。

证据至少包含一种:

```text
测试命令 + 输出
diff 摘要
运行日志
生成产物
人工验证结果
decision audit trace
```

Agent 行为类改动优先要求:

```text
输入上下文
期望决策
实际工具调用
实际 Observation
是否遵循 repair_plan
```

### 4.7 Handoff

当上下文过长、讨论噪音过多或需要新 Agent 接手时, 用 handoff 重启。

handoff 必须包含:

```text
当前目标
当前有效决策
已完成工作
未完成工作
已知风险
涉及文件
已通过测试
下一任务包
```

## 5. 和当前 Agent 架构的关系

这套范式服务于当前目标:

```text
主控 Agent 规划
→ worker 执行
→ 工具返回
→ verification_agent 验证
→ repair_plan 修正
→ 记忆沉淀
→ skill 固化
```

开发前范式是给人和 Agent 共用的外部真相源。它让后续实现不依赖腐烂聊天历史, 而依赖目标、边界、任务包和证据。

## 6. 可复用技能

本方法已沉淀为本地 Codex skill:

```text
C:\Users\Administrator\.agents\skills\agent-harness-preflight\SKILL.md
```

适用场景:

- 架构重构。
- Agent 记忆 / 反思 / 多 Agent 编排。
- 上下文、prompt、runtime、worker、skill、observability 改造。
- 需要 checkpoint、证据和 handoff 的复杂开发任务。
