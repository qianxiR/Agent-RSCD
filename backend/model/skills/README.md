# Skills — 长任务技能文档

本目录存放 **长任务编排方案**（标准化多步流程），agent 通过 `lookup_skill` 工具**按需检索**执行，**不注入 system prompt**。

## 设计理念

| 内容形态 | 注入方式 | 归属 |
|---------|---------|------|
| 工具目录（工具名+参数） | 每轮注入 system prompt | 由 `agent/prompt/system_prompt.py` 从工具 docstring 动态生成 |
| **长任务编排方案（多步流程）** | **按需检索**（agent 识别意图后主动查） | **本目录 `.md` 文档** |

长任务方案内容较长（含前置条件、多步骤、异常处理），**持续注入 prompt 会浪费上下文**。因此做成按需查询：
agent 识别到长任务意图 → 调 `lookup_skill(query)` → 拿到匹配方案全文 → 按步骤逐步执行。

## 运行时链路

```
用户提出长任务需求
   ↓
agent 识别意图 (lookup_skill 工具的 docstring 指导何时调用)
   ↓
调用 lookup_skill("任务描述")
   ↓
model/skills/loader.py  关键词检索 model/skills/*.md
   ↓
返回匹配方案全文 (触发场景 + 标准步骤 + 异常处理)
   ↓
agent 阅读步骤, 逐步调用相应工具
```

## 已有方案

> ★ v2.5 已注册 3 个工作流方案（`lookup_skill` 可检索）：

| 方案 | 文件 | 触发场景 |
|---|---|---|
| **WF1: 一键分割+可视化+报告** | `wf1-segment-visualize-report.md` | 用户上传单图要求"分割""识别地物" |
| **WF2: 一键变化检测+可视化+报告** | `wf2-change-detection-visualize-report.md` | 用户上传双图要求"变化检测""违建监测" |
| **WF4: 上传影像到 GeoServer+显示** | `wf4-upload-geoserver-display.md` | 用户要求"发布图层""上传到地图服务" |

每个方案含：触发场景 + 前置条件 + 标准步骤（含工具调用名）+ 完成判断 + 异常处理。agent 通过 `lookup_skill(query)` 检索匹配方案后逐步执行。

## 新增方案

往本目录加一个 `.md` 文件即可，**无需改代码**：
1. 文件首行写 `# 标题`
2. 包含 `## 触发场景` 段（写清什么需求会用到，含典型用户话术）
3. 写清标准步骤（含具体工具调用名）、前置条件、异常处理、完成判断
4. `loader.py` 启动时自动扫描加载，`lookup_skill` 自动能检索到

> 撰写方案时请使用**通用占位**（`<图层名>`、`<表名>` 等）描述参数，不要写死具体业务名称、工作空间或业务表名，
> 以保证方案与具体数据源解耦、可复用。

> 技能检索当前用关键词重叠计数（`loader.retrieve_skills`），无向量库依赖。
> 后续方案变多、需要语义检索时，可接入 `data/knowledge/` 的向量库升级检索。

## 相关代码

- `model/skills/loader.py` — 文档扫描 + 关键词检索（`retrieve_skills` / `list_skills` / `get_skill_by_name`）
- `model/tools/skill_tools.py` — `lookup_skill` / `list_available_skills` 工具（注册到工具注册表）
