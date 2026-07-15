"""
知识库存储接口 (Data 层 / Knowledge) — 骨架占位

本模块定义知识库的统一访问接口 (search / ingest), 当前为骨架,
具体实现与选型 (pgvector 向量库 / 结构化 PG 表 / 外部检索服务) 待后续确定后填充。

设计意图:
  知识库 (RAG 检索 / 领域知识) 与业务库 (data/business_db) 职责不同 ——
  业务库存放结构化的湖泊/波段数据, 知识库存放可被检索的领域知识片段
  (法规条款、操作手册、遥感方法说明、历史案例等)。
  独立子层便于后续按需接入向量检索, 不影响现有业务库。

后续接入方向 (任选其一, 见 README.md):
  1. pgvector: 复用 PostgreSQL, 用 pgvector 扩展做向量检索 (无需额外服务)
  2. 结构化表: 在业务库建知识表 (条款/FAQ 等), 纯 SQL 检索
  3. 外部服务: 对接专用向量库 (Milvus/Qdrant 等)
"""
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


def knowledge_available() -> bool:
    """
    检查知识库是否可用。
    当前骨架未接入任何后端, 恒返回 False。
    后续接入具体实现后, 改为检测连接 / 向量索引是否就绪。
    """
    return False


def search(
    query: str,
    top_k: int = 5,
    filters: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    知识库检索 (骨架, 未实现)。
    - 入参:
      - query: 查询文本 (自然语言)
      - top_k: 返回的最相关片段数
      - filters: 可选的元数据过滤条件
    - 出参: 知识片段列表 [{id, content, score, metadata}], 当前返回空列表
    - 后续: 向量化 query → 向量相似度检索 (或 SQL 全文检索) → 返回 top_k 片段
    """
    logger.debug(f"[Knowledge] search 未实现, query={query!r}, top_k={top_k}")
    return []


def ingest(
    chunks: List[Dict[str, Any]],
) -> int:
    """
    知识库写入 (骨架, 未实现)。
    - 入参: chunks 知识片段列表 [{content, metadata}]
    - 出参: 成功写入的片段数, 当前返回 0
    - 后续: 向量化 content → 写入向量表 (或结构化知识表)
    """
    logger.debug(f"[Knowledge] ingest 未实现, chunks={len(chunks)}")
    return 0
