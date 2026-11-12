# Agent-RSCD 改造计划

## 1. 实施原则

```text
1. 主控 Agent 保持唯一决策中心。
2. 每阶段先补契约与测试，再改主链路。
3. 每阶段可独立回滚。
4. 不以新增组件数量衡量进度。
5. 未通过验收不进入下一阶段。
```

## 2. 阶段总表

| 阶段 | 文档 | 主题 | 当前状态 | 退出条件 |
|---:|---|---|---|---|
| 1 | 01 | 记忆系统 | 基础完成 | 生命周期、写入门控、lesson 闭环、审计 |
| 2 | 02 | 多 Agent 协作 | 受控双 worker | 角色注册、任务图、权限、观测 |
| 3 | 03 | 提示词工程 | 基础完成 | 版本、分区预算、回归集、注入防护 |
| 4 | 04 | 上下文工程 | 基础完成 | 区块预算、快照、缓存统计、折叠追溯 |
| 5 | 05 | Agent Skills | 部分完成 | 单一注册表、执行闭环、白名单校验 |
| 6 | 06 | MCP 协议 | 部分完成 | schema 校验、连接复用、审计、测试 server |
| 7 | 07 | Harness Engineering | 部分完成 | 事件统一、run_id、生命周期、回放 |
| 8 | 08 | AI 工作流 | 部分完成 | DAG 主路径、节点重试、人工确认、trace |
| 9 | 09 | Loop Engineering | 部分完成（Plan-and-Execute） | 预算、循环检测、状态机、指标 |

## 3. 阶段 1：记忆系统

**范围**

```text
user_memory 生命周期
preference/profile/lesson 写入门控
注入预算与来源追踪
lesson 数据闭环
```

**交付**

- `touch_user_memory` / `supersede_user_memory`
- memory audit
- legacy profile 清理
- memory API
- 测试：写入、去重、注入、清理、lesson

**退出条件**

```text
7 条 legacy profile 有明确归属
lesson 至少 1 条真实数据完成端到端
注入可输出 memory_ids
```

## 4. 阶段 2：多 Agent 协作

**范围**

```text
WorkerSpec 注册表
任务图
工具白名单执行时校验
并发/超时/取消
worker 事件
```

**交付**

- `WorkerSpec` 与角色注册表
- `task_manager` 去硬编码
- worker 事件
- 任务图查询
- 测试：派发、并发、取消、失败、白名单

**退出条件**

```text
新增角色只改注册表
worker 不能直接回复用户
父子任务链可查询
```

## 5. 阶段 3：提示词工程

**范围**

```text
版本化
分区 token 预算
评估集
注入防护
```

**交付**

- prompt version
- 分区统计
- 12 条回归样本
- 注入测试
- 工具目录文案清理

**退出条件**

```text
每次运行可查 prompt version
静态/工具/动态分区 token 可见
12 条回归全部通过
```

## 6. 阶段 4：上下文工程

**范围**

```text
区块预算
上下文快照
缓存统计
工具消息折叠追溯
```

**交付**

- `context_snapshot`
- 区块裁剪
- 缓存命中统计
- 超限告警
- 测试：长历史、多工具、空记忆

**退出条件**

```text
当前用户消息永不丢失
裁剪不破坏 human/tool 配对
区块 token 可查
```

## 7. 阶段 5：Agent Skills

**范围**

```text
单一注册表
技能执行
白名单
步骤验收
```

**交付**

- SkillSpec v2
- `lookup_skill` 结构化返回
- 计划写入
- 3 个技能端到端测试
- 失败回退

**退出条件**

```text
一个技能一份契约
技能步骤进入计划
白名单外工具被拒绝
```

## 8. 阶段 6：MCP 协议

**范围**

```text
schema 校验
连接管理
审计
安全
测试 server
```

**交付**

- MCP schema validator
- session 复用
- 审计日志
- 测试 MCP server
- 健康检查

**退出条件**

```text
非法 schema 不进入 bind_tools
不可达 server 不阻塞聊天
list/call 有回归测试
```

## 9. 阶段 7：Harness Engineering

**范围**

```text
事件统一
run_id
生命周期
回放
```

**交付**

- ChatService 改走 `emit`
- `before_tool` / `after_tool`
- run record
- 停止原因枚举
- harness 测试

**退出条件**

```text
所有下行事件由 Harness emit
每个事件有 run_id + seq
停止原因与状态一致
```

## 10. 阶段 8：AI 工作流

**范围**

```text
DAG 主路径
节点契约
重试/回退
人工确认
```

**交付**

- DAG 接入第二个任务
- 节点超时/重试/fallback
- 工作流 trace
- 人工确认
- 版本化

**退出条件**

```text
节点失败阻断下游
工作流有 trace 与预算
高风险节点可确认
```

## 11. 阶段 9：Loop Engineering

**范围**

```text
预算
状态机
循环检测
指标
```

**交付**

- `max_iterations`
- `max_tool_calls`
- 重复调用检测
- budget_exceeded 快照
- loop trace

**退出条件**

```text
不出现无限循环
预算超限可恢复/终止
循环指标可查
```

## 12. 验证矩阵

| 阶段 | 单元测试 | 集成测试 | 运行时验证 |
|---|---|---|---|
| 1 | memory | 对话收尾 | 注入日志 |
| 2 | worker | 派发/取消 | ai_task 父子链 |
| 3 | prompt | 工具选择 | prompt version |
| 4 | context | 长对话 | token 快照 |
| 5 | skills | 3 技能 | 计划步骤 |
| 6 | MCP | list/call | server 健康 |
| 7 | harness | 事件流 | run_id |
| 8 | workflow | DAG | 节点 trace |
| 9 | loop | 停止/恢复 | 预算指标 |

## 13. 当前阻塞

```text
后端服务已停止，运行时验证未执行。
lesson 当前 0 条。
MCP 当前 0 个服务器。
Plan-first 默认关闭。
```

## 14. 完成定义

一个阶段完成必须同时满足：

```text
代码已合并
测试通过
运行时验证通过
文档更新
回滚路径明确
```
