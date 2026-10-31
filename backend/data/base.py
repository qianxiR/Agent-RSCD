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
from pathlib import Path
from typing import Any, BinaryIO, Dict, Iterable, List, Literal, Optional, Protocol, Tuple
from uuid import uuid4


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


class LocalFileRepository:
    """本地文件系统资产仓储；五类数据域可复用同一实现。

    - save: 写文件 + 生成 AssetRef + 可选登记到 catalog；
    - open: 按 AssetRef.uri 打开；
    - query: 委托 catalog 做 kind/bbox/time/text 查询。
    """

    def __init__(self, kind: str, root: Path | str, catalog: Any = None) -> None:
        if kind not in ASSET_KINDS:
            raise ValueError(f"不支持的 kind: {kind}")
        self.kind = kind
        self.root = Path(root)
        self.catalog = catalog

    def save(
        self,
        data: bytes,
        *,
        filename: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        crs: Optional[str] = None,
        bbox: Optional[List[float]] = None,
        acquired_at: Optional[str] = None,
    ) -> AssetRef:
        """
        入参: 二进制内容 + 可选文件名/元数据/CRS/范围/时间。
        方法: 创建目录 → 生成唯一文件名 → 写入 → 构造 AssetRef → 登记 catalog。
        出参: AssetRef。
        """
        self.root.mkdir(parents=True, exist_ok=True)
        safe_name = Path(filename).name if filename else f"{uuid4().hex}.bin"
        target = self.root / safe_name
        if target.exists():
            target = self.root / f"{target.stem}_{uuid4().hex[:6]}{target.suffix}"
        target.write_bytes(data)
        ref = AssetRef(
            asset_id=f"{self.kind}_{uuid4().hex[:12]}",
            kind=self.kind,
            uri=str(target),
            crs=crs,
            bbox=bbox,
            acquired_at=acquired_at,
            metadata=dict(metadata or {}),
        )
        if self.catalog is not None:
            self.catalog.register(ref)
        return ref

    def open(self, ref: AssetRef) -> BinaryIO:
        """打开资产；kind 不匹配时拒绝，避免跨域误用。"""
        if ref.kind != self.kind:
            raise ValueError(f"仓储 kind={self.kind} 不能打开 kind={ref.kind}")
        return Path(ref.uri).open("rb")

    def query(self, **filters: Any) -> List[AssetRef]:
        """委托 catalog 查询；未配置 catalog 时返回空。"""
        if self.catalog is None:
            return []
        filters.setdefault("kind", self.kind)
        return self.catalog.query(**filters)
