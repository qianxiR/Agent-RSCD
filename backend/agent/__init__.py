"""
Agent 层 (Agent Layer) — 智能体核心
- chat_service: Agent ReAct 推理循环引擎 (运行状态管理流水线)
- ws_manager:   WebSocket 连接管理 + 任务管理 (多对话并行)
- memory/:      记忆子包 (编排 + 记忆数据库 agent_db)
- prompt/:      智能体系统提示词 (工具目录 + CoT + 动态记忆段)
- runtime/:     运行时上下文 (ContextVar, 多对话隔离)
- tools/:       记忆工具 (跟随记忆留在本层; 其余工具见 model.tools)
"""
