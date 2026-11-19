# 04 上下文工程

## 1. 目标

把上下文拆成可计算、可裁剪、可缓存、可追踪的区块。静态规则、动态状态、工作记忆、工具结果各自有边界。

## 2. 当前基线

### 2.1 组装链路

[`backend/agent/memory/memory_context.py:233`](../../backend/agent/memory/memory_context.py#L233)

```text
长期记忆 -> 用户画像
短期摘要 -> 会话摘要
工作记忆 -> 近期消息
计划状态 -> 显式计划
任务状态 -> 当前任务状态
```

### 2.2 静态/动态双块

[`backend/agent/prompt/system_prompt.py:50`](../../backend/agent/prompt/system_prompt.py#L50)

```text
块 A：静态模板 + 决策表 + 工具目录
块 B：任务状态 + 计划 + 摘要 + 用户画像
```

两块都带 `cache_control: ephemeral`。

### 2.3 裁剪

- `CONTEXT_MAX_TOKENS=16000`
- `strategy="last"` 保留最近消息
- `include_system=True`
- `start_on="human"` 不切断工具调用配对
- 工具消息按 `TOOL_MESSAGE_KEEP_TAIL` 保留尾部

### 2.4 当前问题

- token 计数使用本地估算，不是模型 tokenizer。
- 动态段为空时仍输出占位文本。
- 没有区块级预算。
- 没有记录本次上下文实际包含哪些区块。
- 没有缓存命中/失效统计。
- 旧工具结果折叠后不可逆。

## 3. 目标设计

### 3.1 上下文区块

| 区块 | 来源 | 可裁剪 |
|---|---|---|
| `system.static` | 静态模板 | 否 |
| `system.tools` | 当前工具目录 | 是 |
| `system.dynamic` | 任务/计划/摘要/画像 | 是 |
| `history` | message 表 | 是 |
| `current_turn` | 当前用户消息 | 否 |
| `tool_results` | 本轮工具消息 | 是 |

### 3.2 预算

```text
system.static     2200 tokens
system.tools       800 tokens
system.dynamic    1200 tokens
history          10000 tokens
current_turn      1000 tokens
reserve            800 tokens
```

总计 16000 tokens。

裁剪顺序：

```text
旧工具结果 -> 旧历史 -> 工具目录 -> 动态段
```

永不裁剪：

```text
system.static
current_turn
本轮 verification 结果
```

### 3.3 上下文快照

每次组装记录：

```json
{
  "conversation_id": "...",
  "blocks": {
    "static": {"tokens": 1800, "hash": "..."},
    "tools": {"tokens": 640, "names": ["segment_image"]},
    "dynamic": {"tokens": 420, "sections": ["task", "plan"]},
    "history": {"messages": 18, "tokens": 7200}
  },
  "trimmed": true,
  "cache_hit": true
}
```

写入 `agent_run` 或独立 trace，不进入用户消息。

### 3.4 缓存

- 静态块按 `prompt_version + tool_names` 缓存。
- 动态块按 `summary_hash + plan_hash + profile_hash` 缓存。
- 任何动态输入变化只刷新动态块。
- 缓存命中率进入指标。

## 4. 改造任务

### P0

- [ ] 增加区块级 token 统计。
- [ ] 增加上下文快照结构。
- [ ] 空动态段不输出占位文本。
- [ ] 增加工具消息折叠标记，折叠后仍可追溯原文。
- [ ] 增加 `context_snapshot` 测试。

### P1

- [ ] 增加按区块裁剪策略。
- [ ] 增加缓存命中/失效统计。
- [ ] 增加摘要、计划、画像的 hash。
- [ ] 增加上下文超限告警。

### P2

- [ ] 使用模型 tokenizer 校准计数。
- [ ] 增加上下文回放工具。
- [ ] 建立长对话、多工具、无记忆、旧记忆冲突四类回归样本。

## 5. 验收标准

```text
1. 每次运行可输出各区块 token。
2. 裁剪后不破坏 human/tool 配对。
3. 当前用户消息永不丢失。
4. 静态块与工具目录可缓存，动态块变化只刷新动态块。
5. 折叠后的工具结果仍可追溯。
6. 上下文超限有告警。
```

## 6. 风险

- 区块预算过紧会丢失历史证据。
- 缓存粒度过细会降低命中率。
- 折叠工具结果会降低模型对旧证据的可见性。
