# 01 AI Agent 记忆系统

## 1. 目标

长期记忆只保留三类：

```text
preference  用户行为偏好
profile     稳定用户画像
lesson      已验证改错教训（scope=global）
```

要求：可写入、可去重、可过期、可审计、可注入、可清理。

## 2. 当前基线

### 2.1 表结构

[`backend/agent/memory/agent_db.py`](../../backend/agent/memory/agent_db.py)

`user_memory` 现有字段：

```text
id, user_id, key, value, category, created_at
scope, kind, confidence, source, evidence, status
supersedes_id, last_used_at, use_count, updated_at
```

唯一约束：

```sql
UNIQUE (user_id, scope, category, key)
WHERE status = 'active'
```

### 2.2 读写

- 写入：[`agent_db.py:1300`](../../backend/agent/memory/agent_db.py#L1300)
- 读取：[`agent_db.py:1365`](../../backend/agent/memory/agent_db.py#L1365)
- 清空：[`agent_db.py:1458`](../../backend/agent/memory/agent_db.py#L1458)
- Store 封装：[`stores.py:57`](../../backend/agent/memory/stores.py#L57)

写入时旧类别自动归一化：

```text
fact / entity / envfact -> profile
workflow -> lesson
```

### 2.3 提取与注入

- 提取：[`memory_context.py:730`](../../backend/agent/memory/memory_context.py#L730)
- 注入：[`memory_context.py:855`](../../backend/agent/memory/memory_context.py#L855)
- 注入顺序：`lesson -> preference -> profile`
- 注入条件：`status=active`、`confidence >= 0.6`
- 注入上限：20 条
- 每轮提取上限：3 条

### 2.4 当前数据

```text
preference   0
profile      7
lesson       0
global       0
```

`profile` 7 条全部为 `source=legacy`、`confidence=0.6`。

## 3. 差距

1. `last_used_at`、`use_count`、`supersedes_id` 已建字段，没有读写逻辑。
2. `lesson` 为 0，改错沉淀链路没有真实数据。
3. 旧 `profile` 内容是行为描述，未按 `preference/profile` 重新分类。
4. 没有过期、衰减、冲突合并。
5. 没有记忆质量评估。
6. 没有记录注入轨迹，无法判断某条答案使用了哪条记忆。
7. `preference` 写入仍依赖每轮 LLM 提取，存在重复和噪声。

## 4. 目标设计

### 4.1 生命周期

```text
candidate -> active -> superseded
                   -> stale
                   -> deleted
```

- `active`：可注入。
- `superseded`：被新版本替代，不注入。
- `stale`：长期未使用或置信度下降，不注入。
- `deleted`：软删除，保留审计。

### 4.2 写入门控

| 类别 | 写入条件 | 单轮上限 |
|---|---|---:|
| `preference` | 用户明确表达，或跨 2 次会话重复 | 2 |
| `profile` | 用户明确陈述的稳定事实 | 1 |
| `lesson` | 修复成功且 verification=passed | 1 |

拒绝写入：

```text
具体图层名、表名、文件路径
一次性任务参数
模型推断出的临时结论
没有 evidence 的条目
```

### 4.3 去重与替代

- 同一 `(user_id, scope, category, key)` 更新 active 行。
- 语义相同的不同 key 合并到 canonical key。
- 旧版本写 `status=superseded`，新行 `supersedes_id` 指向旧行。
- 同一 key 的冲突按 `confidence + evidence 数量 + updated_at` 选择。

### 4.4 注入预算

```text
先按类别排序：lesson -> preference -> profile
再按 confidence、last_used_at、use_count 排序
最多 20 条
最多占用上下文预算的 10%
```

注入时记录：

```json
{
  "memory_ids": [1, 2],
  "categories": ["preference", "profile"],
  "tokens": 320
}
```

### 4.5 lesson 闭环

```text
工具失败
  -> 自纠检测
  -> 修复成功
  -> verification=passed
  -> LLM 提炼五段式
  -> lesson_policy 准入
  -> scope=global 写入
  -> 下轮注入
  -> 记录是否避免重复错误
```

## 5. 改造任务

### P0

- [ ] 实现 `touch_user_memory(ids)`，更新 `last_used_at`、`use_count`。
- [ ] 实现 `supersede_user_memory(old_id, new_id)`。
- [ ] 写入时校验 `evidence` 非空；`lesson` 必须含 verification。
- [ ] 清理 7 条 legacy profile：保留画像，行为类改 `preference`，低价值删除。
- [ ] 增加 `memory_audit` 日志：写入、更新、注入、删除。
- [ ] 增加空值、重复、旧类别归一化测试。

### P1

- [ ] 增加 90 天未使用转 `stale`。
- [ ] 增加 `canonical_key` 和同义 key 合并。
- [ ] 增加 `GET /api/v1/memory` 管理接口。
- [ ] 增加 `DELETE /api/v1/memory/{id}` 软删除。
- [ ] 增加 lesson 有效性复验字段。

### P2

- [ ] 建立记忆评估集：写入准确率、重复率、注入相关性。
- [ ] 建立记忆冲突案例集。
- [ ] 评估是否需要按项目隔离 `project_id`。

## 6. 验收标准

```text
1. preference/profile/lesson 三类写入均有 evidence。
2. 同一 key 不产生重复 active 行。
3. 注入不超过 20 条，且能输出 memory_ids。
4. lesson 从失败到注入全链路可复现。
5. legacy profile 清理后，7 条数据均有明确归属。
6. 记忆写入、注入、删除均有审计记录。
```

## 7. 风险

- 降低提取频率会减少偏好覆盖，需要接受短期记忆变少。
- `lesson` 准入门槛不能降低，否则会污染全局记忆。
- 注入预算过小会丢失画像，过大则挤占工作记忆。
