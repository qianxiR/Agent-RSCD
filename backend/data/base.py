# -*- coding: utf-8 -*-
"""
数据层统一契约 (Data Layer)

目标：把"影像 / 矢量 / 图片 / 文本 / other"抽象成统一资产引用，
让 Agent、工具、模型都不直接依赖具体存储路径或服务实现。

- AssetRef: 跨层传递的资产描述（id/kind/uri/crs/bbox/time/metadata）
- DataStore: 打开、保存、查询资产的最小协议
- 具体实现放在 data/imagery、data/vector、data/image、data/text、data/other
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, BinaryIO, Dict, Iterable, List, Literal, Optional, Protocol, Tuple


AssetKind = Literal["imagery", "vector", "image", "text", "other"]
ASSET_KINDS: Tuple[str, ...] = ("imagery", "vector", "image", "text", "other")


@dataclass
class AssetRef:
    """数据资产的稳定引用；不承诺 uri 一定是本地路径。"""

    asset_id: str
    kind: str
    uri: str
    crs: Optional[str] = None
    bbox: Optional[List[float]] = None          # [minx, miny, maxx, maxy]，EPSG:4326
    acquired_at: Optional[str] = None           # ISO 时间
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """转成可 JSON 序列化的 dict。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AssetRef":
        """从 dict 还原；未知字段忽略。"""
        if not isinstance(data, dict):
            raise ValueError("AssetRef 必须是 dict")
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{key: value for key, value in data.items() if key in known})

    def is_file(self) -> bool:
        """是否为本地文件 URI（file:// 或绝对路径）。"""
        return self.uri.startswith("file://") or self.uri.startswith("/") or (
            len(self.uri) > 2 and self.uri[1] == ":" and self.uri[2] in "\\/"
        )


class DataStore(Protocol):
    """最小数据存储协议；按 kind 路由到具体仓储。"""

    kind: str

    def open(self, ref: AssetRef) -> BinaryIO:
        """打开资产为二进制流。"""
        ...

    def save(self, data: bytes, *, kind: str, metadata: Dict[str, Any]) -> AssetRef:
        """保存资产并返回引用。"""
        ...

    def query(self, filters: Dict[str, Any]) -> List[AssetRef]:
        """按元数据/空间/时间条件查询。"""
        ...


def validate_asset_ref(ref: AssetRef) -> List[str]:
    """
    入参: ref 资产引用。
    方法: 检查 kind/uri 合法性。
    出参: issues 列表，空表示通过。
    """
    issues: List[str] = []
    if not ref.asset_id:
        issues.append("asset_id 不能为空")
    if ref.kind not in ASSET_KINDS:
        issues.append(f"不支持的 kind: {ref.kind}")
    if not ref.uri:
        issues.append("uri 不能为空")
    if ref.bbox is not None and len(ref.bbox) != 4:
        issues.append("bbox 必须是 [minx,miny,maxx,maxy]")
    if ref.acquired_at:
        try:
            datetime.fromisoformat(str(ref.acquired_at).replace("Z", "+00:00"))
        except ValueError:
            issues.append("acquired_at 不是合法 ISO 时间")
    return issues
