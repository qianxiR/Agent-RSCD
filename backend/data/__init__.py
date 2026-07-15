"""
数据层 (Data Layer)
- business_db:     业务库连接池与 CRUD (表名作为参数, 不绑定具体业务表/工作空间)
- geoserver_client:GeoServer REST + WMS 客户端 (空间数据发布)
- knowledge/:      知识库子层 (领域知识检索, 当前骨架占位)

★ 记忆库 agent_db 已上移至 agent 层 (backend.agent.memory.agent_db) ——
  记忆是智能体自身状态, 归 agent 层; data 层只承载外部业务/空间/知识数据。
"""
