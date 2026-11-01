# Prompt 模板目录

- 文件命名：`<name>.md` 或 `<name>.<version>.md`
- 占位符：`{tools_catalog}`、`{decision_table}`、`{dynamic_section}`（与旧 system prompt 对齐）
- `PromptStore.get("system")` 在文件不存在时回退 `prompt/static_template.py`
- 运行时可调用 `PromptStore.put()` 写入新版本，便于管理端灰度
