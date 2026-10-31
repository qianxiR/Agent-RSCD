# -*- coding: utf-8 -*-
"""
统一资产目录 (Data Catalog)

第一阶段先提供进程内目录 + 稳定接口，后续可替换为 PostGIS 表：
- register/get/query 都只依赖 AssetRef；
- 支持 kind、bbox 相交、时间范围、文本关键字过滤；
- 线程安全，供工具与 Planner 读取数据摘要。
"""
from __future__ import annotations

import threading
from typing import Any, Dict, Iterable, List, Optional

from backend.data.base import ASSET_KINDS, AssetRef, validate_asset_ref


class AssetCatalog:
    """进程内资产目录；接口保持与未来 PostGIS 实现一致。"""

    def __init__(self) -> None:
        self._items: Dict[str, AssetRef] = {}
        self._lock = threading.RLock()

    def register(self, ref: AssetRef) -> AssetRef:
        """
        入参: ref 资产引用。
        方法: 校验后按 asset_id 覆盖写入。
        出参: 入库后的引用；非法时抛 ValueError。
        """
        issues = validate_asset_ref(ref)
        if issues:
            raise ValueError("; ".join(issues))
        with self._lock:
            self._items[ref.asset_id] = ref
        return ref

    def get(self, asset_id: str) -> Optional[AssetRef]:
        """按 ID 取资产，不存在返回 None。"""
        with self._lock:
            return self._items.get(asset_id)

    def all(self) -> List[AssetRef]:
        """返回全部资产快照。"""
        with self._lock:
            return list(self._items.values())

    def query(
        self,
        *,
        kind: Optional[str] = None,
        bbox: Optional[List[float]] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        text: str = "",
        limit: int = 100,
    ) -> List[AssetRef]:
        """
        入参:
          - kind: 资产类型过滤。
          - bbox: [minx,miny,maxx,maxy]；只返回与该范围相交的资产。
          - start/end: acquired_at 的 ISO 时间范围（闭区间）。
          - text: 在 asset_id/uri/metadata 中做不区分大小写的子串匹配。
          - limit: 最大返回条数。
        方法: 逐项过滤；不做数据库查询，O(n)。
        出参: 资产列表。
        """
        if kind is not None and kind not in ASSET_KINDS:
            raise ValueError(f"不支持的 kind: {kind}")
        needle = (text or "").strip().lower()
        results: List[AssetRef] = []
        with self._lock:
            items = list(self._items.values())
        for ref in items:
            if kind and ref.kind != kind:
                continue
            if bbox and ref.bbox and not _bbox_intersects(bbox, ref.bbox):
                continue
            if start and (ref.acquired_at or "") < start:
                continue
            if end and (ref.acquired_at or "") > end:
                continue
            if needle:
                haystack = " ".join([
                    ref.asset_id, ref.uri, str(ref.metadata),
                ]).lower()
                if needle not in haystack:
                    continue
            results.append(ref)
            if len(results) >= max(1, limit):
                break
        return results

    def clear(self) -> None:
        """清空目录（测试/重建索引用）。"""
        with self._lock:
            self._items.clear()


def _bbox_intersects(a: Iterable[float], b: Iterable[float]) -> bool:
    """判断两个 [minx,miny,maxx,maxy] 是否相交。"""
    ax0, ay0, ax1, ay1 = list(a)[:4]
    bx0, by0, bx1, by1 = list(b)[:4]
    return not (ax1 < bx0 or bx1 < ax0 or ay1 < by0 or by1 < ay0)


_CATALOG = AssetCatalog()


def get_catalog() -> AssetCatalog:
    """返回进程级默认目录。"""
    return _CATALOG
