# Agent 内核设计概览

> 聚焦 **Agent 内核机制**（上下文 / 记忆 / 执行策略 / Token 消耗），讲"这些部件如何协同驱动一次推理"。系统部件详见 [architecture.md](../../architecture.md)。

> **★ 2026-06-24（阶段 14-22）**：46 工具 10 大类（含 delete_geoserver_layer 补齐图层删除 + overlay_edge_on_image 边缘算子叠加★阶段21后仅手动调用）；3 个工作流方案（分割/变化检测/GeoServer 上传）；记忆多级压缩（v2.5）；消息分支 DAG（v2.5）；统一 Tool Result schema（v2.5）；**OpenLayers 地图容器（阶段21，替代卡片墙）**；`agent-files/{来源}/{项目}/{会话}/` 统一目录。
>
> **★ 2026-07（执行闭环与团队）**：ReAct 循环外叠加确定性执行闭环——计划状态机（`task_state.py`，plan/step/completion_gate，复用 ai_task 不新增表）+ 确定性 `verification_agent`（不调 LLM）+ `repair_policy` 13 类 failure_type 修复策略 + 完成门拦截未验证收尾 + 受控 worker（验证/报告）+ 技能编排（3 个 workflow 契约 → plan 步骤）+ 可观测性审计（decision_audit/repair_success）。详见 [architecture-agent.md §3](architecture-agent.md)。

---

## 一、一次用户请求的生命周期

```
用户输入 prompt
   ▼
[1] 上下文构建 (build_context_messages)
    load 长期记忆 → load 短期摘要 → load 工作记忆(消息)
    ★ v2.5: fold_tool_messages 折叠旧工具结果 (Level 0.5, 可逆)
    → trim_messages 裁剪到 16000 token
   ▼
[2] Agent 推理循环 (无轮次上限, 用户停止兜底)
    LLM 决策 → 有 tool_calls?
      是 → 执行工具 → ToolMessage 追加 → 继续循环
      否 → 输出最终回复 → 跳出
   ▼
[3] 收尾 (异步, fire-and-forget)
    persist_turn 持久化本轮消息
    ★ v2.5 maybe_summarize 两级压缩:
      Level 1 fold (可逆) → 仍超阈值 × 1.5?
        是 → Level 2 LLM 摘要 (不可逆, 删旧消息)
    extract_long_term_memory 提取用户偏好
```

---

## 二、上下文管理

**三层消息拼装**：`[SystemMessage(role+规则+工具目录+摘要+画像)] + history + [HumanMessage]`

**关键约束**：
- token 上限 `context_max_tokens`=16000，`trim_messages(strategy="last")` 保留近期
- 自定义 tiktoken 计数器（不能用 `token_counter=llm`，qwen-plus 会 NotImplementedError）
- trim 后强制补回 HumanMessage（防 system prompt 膨胀时丢失当前输入）
- 显式缓存：system prompt 拆「静态块（角色+规则+工具目录）+ 半稳定块（摘要+画像）」，跨轮复用降本

**v2.5 fold_tool_messages**（Level 0.5 可逆压缩）：
- trim 前把旧工具结果折叠为 `[已完成·工具名·type·摘要前80字]`
- 保留 `tool_call_id` 配对，不破坏 LLM 理解
- 保留最近 `tool_message_keep_tail`=3 条不折叠

---

## 三、记忆系统

| 层级 | 表 | 范围 | 触发/注入 |
|---|---|---|---|
| 🔒 **长期** | `user_memory` | 跨会话 | 每轮收尾提取偏好；自纠学习沉淀教训；注入 system prompt |
| 📝 **短期** | `conversation_summary` | 会话内 | **v2.5 两级**：Level 1 fold（可逆）→ Level 2 LLM 摘要（threshold×1.5，不可逆）；注入 system prompt |
| 💾 **工作** | `message` | 近期 N 条 | 每轮 persist_turn；**v2.5 加 fold + DAG 分支**；以消息列表喂 LLM |

**v2.5 消息分支（Git-like DAG）**：
- message 表加 `node_id`/`parent_id`/`is_active`
- `fork_from_node`：隐藏后续兄弟（is_active=FALSE），旧分支保留
- `delete_messages_after` 改软删除（停止回滚不再物理删）
- 支持"回到第 N 轮重新问"，前端 `↻ 重新生成` 按钮

---

## 四、执行策略

- **ReAct 循环**：无限循环工具调度（不设轮次上限），直到 LLM 不再返回 tool_calls 给出最终回复
- **停止机制**：用户点停止 → `asyncio.CancelledError` → 软删除 checkpoint 后消息（v2.5 保留旧分支）
- **工具异常不中断**：捕获为 error ToolMessage 回流给 LLM 重试
- **反伪装成功三层防御**：工具层（verification）/ Prompt 层（规则约束）/ 传输层（chat_service 兜底校验）
- **统一 Tool Result Schema**（v2.5）：`_result.py` 提供 helper，`instruction.type==action` 保证一致
- **执行闭环**（2026-07）：每个工具调用进入 plan step → 确定性 verification_agent 校验 → 失败按 failure_type 生成 repair_plan → completion_gate 拦截未验证收尾。主控 Agent 是唯一决策中心，受控 worker（验证/报告）按白名单分工

---

## 五、Token 消耗策略

| 策略 | 实现 | 效果 |
|---|---|---|
| 显式缓存 | system prompt 拆静态/半稳定块，`cache_control` 标记 | 跨轮复用，命中率约 40-60% |
| 摘要降本 | 摘要用 `summary_model`=deepseek-v4-flash（免费额度） | 摘要不花主模型 token |
| **v2.5 fold** | 旧工具结果折叠为短摘要（Level 0.5） | 长对话工具结果不再累积爆 token |
| **v2.5 两级压缩** | Level 1 可逆 fold 优先，Level 2 不可逆摘要兜底 | 精细化压缩，区分可逆/不可逆 |

---

## 六、核心配置

| 配置 | 默认 | 说明 |
|---|---|---|
| `context_max_tokens` | 16000 | trim 裁剪硬上限 |
| `history_load_limit` | 50 | 加载最近 N 条消息 |
| `summarize_threshold` | 8000 | 累计 token 超此值触发多级压缩 |
| `summary_model` | deepseek-v4-flash | 摘要用的便宜模型 |
| `summary_max_tokens` | 300 | LLM 摘要目标长度（Level 2） |
| `summary_level2_multiplier` | 1.5 | Level 2 触发倍数（threshold × 此值） |
| `tool_message_keep_tail` | 3 | fold 时保留最近 N 条工具消息 |
| `ws_frontend_result_timeout` | 120 | 等待前端 tool_result 超时（秒） |
