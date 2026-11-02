# Prompt 模板目录

当前只保留一个静态模板：

```text
static_template2.md
```

规则：

- 这是系统提示词的**唯一静态模板**；
- `system_prompt.py` 直接读取 `static_template2`，缺失时抛 `RuntimeError`；
- **没有代码兜底**，不要在其他 Python 文件里再维护整段 system prompt；
- 动态上下文仍由 `backend/agent/prompt/dynamic_context.py` 负责，不写进本模板。

占位符：

```text
{tools_catalog}
{decision_table}
{dynamic_section}
```
