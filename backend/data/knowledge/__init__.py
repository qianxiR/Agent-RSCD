"""
知识库子层 (Data 层 / Knowledge)
- knowledge_store: 知识库统一访问接口 (search / ingest), 当前为骨架占位

知识库与业务库职责不同:
  - business_db: 结构化的湖泊/波段业务数据
  - knowledge:   可检索的领域知识片段 (RAG), 形态待定
"""
from backend.data.knowledge import knowledge_store

__all__ = ["knowledge_store"]
