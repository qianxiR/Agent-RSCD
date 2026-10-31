"""
模型层 (Model Layer)
- llm_client: LLM 客户端封装 (ChatOpenAI + DashScope/Qwen), 仅负责 LLM 实例构造与工具绑定
- base.py / registry.py: 解译模型统一协议与 capability 注册表
- segmentation/ change_detection/ understanding/: 解译模型适配器
- tools/:    迁移期兼容层（注册表已迁至 agent/tools，工具实现逐项迁移）
- skills/:   技能 Markdown 文档 + 兼容转发（loader/selector 已迁至 agent/skills）

★ System Prompt 已归入 agent/prompt/ (prompt 是智能体设计核心, 属 agent 层)。
"""
