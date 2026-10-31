"""
数据层 (Data Layer)

统一资产契约与域目录：
- base:      AssetRef / DataStore 协议（影像/矢量/图片/文本/other）
- catalog:   进程内统一资产目录（后续可替换为 PostGIS）
- imagery/:  影像检索、抓取、配准、掩膜
- vector/:   矢量登记与格式转换
- image/:    普通图片、预览、缩略图
- text/:     知识库、报告模板、文本语料
- other/:    表格、产物、下载文件
- connectors/: PostGIS / GeoServer / 对象存储连接器

★ 记忆库 agent_db 归 agent 层 (backend.agent.memory.agent_db)：
  记忆是智能体自身状态，data 层只承载外部业务/空间/知识数据。
"""

from backend.data.base import (
    ASSET_KINDS,
    AssetKind,
    AssetRef,
    DataStore,
    validate_asset_ref,
)
from backend.data.catalog import AssetCatalog, get_catalog

__all__ = [
    "ASSET_KINDS",
    "AssetKind",
    "AssetRef",
    "DataStore",
    "validate_asset_ref",
    "AssetCatalog",
    "get_catalog",
]
