# 03 提示词工程

## 1. 目标

提示词作为可版本化、可评估、可回滚的运行时资产。静态规则与动态上下文分离，工具 schema 按需注入。

## 2. 当前基线

### 2.1 唯一静态模板

[`backend/agent/prompt_store/templates/static_template2.md`](../../backend/agent/prompt_store/templates/static_template2.md)

7 个区块：

```text
成功标准
行为红线
执行协议
工具与数据约定
记忆使用
输出契约
能力边界
```

模板缺失时直接抛出 `RuntimeError`，没有代码兜底。

### 2.2 构建入口

- [`system_prompt.py:24`](../../backend/agent/prompt/system_prompt.py#L24) `build_system_prompt()`
- [`system_prompt.py:50`](../../backend/agent/prompt/system_prompt.py#L50) `build_system_prompt_blocks()`

当前实测：

```text
完整 system prompt：4,241 字符
工具目录：只包含当前选中工具
绑定 schema：compact description，保留参数 schema
```

### 2.3 工具注入

- [`backend/agent/tools/discovery.py`](../../backend/agent/tools/discovery.py)
- [`backend/agent/tools/schema.py`](../../backend/agent/tools/schema.py)

```text
首轮按任务选择工具
模型调用 search_tools 检索候选
模型调用 load_tool 加载完整 schema
下一轮重新 bind_tools
```

当前规模：

```text
注册工具 56
LLM 暴露 44
工具类别 14
动态注入上限 12
```

### 2.4 任务决策表

[`backend/agent/prompt/tool_decision_rules.py`](../../backend/agent/prompt/tool_decision_rules.py)

当前 6 类任务：

```text
语义分割 / 变化检测 / 影像理解 / 长任务编排 / 报告下载 / 图层控制
```

## 3. 差距

1. 提示词没有版本号、变更记录、回滚入口。
2. 没有提示词级回归集；修改模板只能靠人工检查。
3. 没有分区 token 预算；静态段、工具目录、动态段共享总预算。
4. 工具目录分类文案存在历史残留，和当前工具集不完全一致。
5. 没有提示词注入检测；用户输入可进入动态段。
6. 没有提示词效果指标：工具选择准确率、修复率、无效回答率。
7. 静态模板与动态模板的边界没有自动校验。

## 4. 目标设计

### 4.1 版本化

```text
prompt_store/templates/static_template2.md
prompt_store/versions/static_template2@1.0.md
prompt_store/versions/static_template2@1.1.md
```

元数据：

```json
{
  "name": "static_template2",
  "version": "1.1",
  "sections": ["success", "redlines", "protocol", "tools", "memory", "output", "boundary"],
  "owner": "agent",
  "created_at": "2026-11-02"
}
```

### 4.2 分区预算

| 区域 | 预算 |
|---|---:|
| 静态规则 | 2,200 tokens |
| 工具目录 | 800 tokens |
| 动态上下文 | 1,200 tokens |
| 总 system prompt | 4,200 tokens |

超过预算时优先裁剪工具目录，不裁剪行为红线与输出契约。

### 4.3 评估集

必须覆盖：

```text
工具选择：单图分割、双图变化、图层控制、报告下载
修复：缺参数、路径不存在、空结果、验证失败
上下文：长历史、空摘要、无记忆、旧记忆冲突
注入：用户要求忽略规则、伪造系统消息、泄露路径
```

指标：

```text
tool_selection_accuracy
invalid_tool_rate
repair_success_rate
false_success_rate
prompt_tokens
```

### 4.4 注入防护

- 用户输入只进入动态段，不进入静态规则段。
- 动态段中的用户文本加边界标记。
- 工具结果与用户输入分开编码。
- 禁止用户文本覆盖系统规则。

## 5. 改造任务

### P0

- [ ] 为 `static_template2` 增加版本号与变更记录。
- [ ] 增加 `build_system_prompt` 分区 token 统计。
- [ ] 建立 12 条提示词回归样本。
- [ ] 清理工具目录中的过期分类文案。
- [ ] 增加动态段边界标记与注入测试。

### P1

- [ ] 增加提示词 A/B 对比入口。
- [ ] 增加 `prompt_version` 写入 `agent_run` 快照。
- [ ] 增加工具目录预算裁剪。
- [ ] 增加提示词评估脚本。

### P2

- [ ] 建立提示词回滚流程。
- [ ] 建立按任务类型的提示词片段库。

## 6. 验收标准

```text
1. 每次运行可查到 prompt name/version。
2. 静态段、工具目录、动态段分别有 token 统计。
3. 12 条回归样本全部通过。
4. 用户输入不能覆盖行为红线。
5. 模板缺失、版本缺失、分区缺失均能明确报错。
```

## 7. 风险

- 过度压缩静态规则会降低修复与安全约束。
- 工具目录裁剪过度会导致模型无法发现正确工具。
- 版本化后必须同步评估，否则版本号没有意义。
