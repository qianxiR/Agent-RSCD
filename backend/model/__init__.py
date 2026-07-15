"""
模型层 (Model Layer)
- llm_client: LLM 客户端封装 (ChatOpenAI + DashScope/Qwen), 仅负责 LLM 实例构造与工具绑定
- tools/:    工具箱 (工具注册表 + GeoServer/数据库/图层/遥感工具实现)
- skills/:   技能文档 (Markdown) — 含长任务编排方案; agent 通过 lookup_skill 工具按需检索

★ System Prompt 已归入 agent/prompt/ (prompt 是智能体设计核心, 属 agent 层)。
"""
