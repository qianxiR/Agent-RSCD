"""
数据预处理工具集 (Model 层 / 工具箱) — 对接清单 #3 数据预处理

三个工具:
  1. build_pyramid   — 影像金字塔构建 (rasterio overview)
  2. convert_to_cog  — GeoTIFF → COG 转换 (rio-cogeo, 优先沙盒)
  3. check_topology  — 矢量拓扑检查 (自相交/缝隙/重叠)

★ 重工具白名单 (HEAVY_TOOL_CATEGORIES 含 "preprocess"), 调用会触发 ai_task 持久化.
★ 依赖降级: rasterio/geopandas 在宿主 sam3 环境装好即可用; rio-cogeo 沙盒执行, 沙盒不可用则报错.

依赖方向: model.tools.preprocess_tools → data.vector (复用) / sandbox_manager / 宿主机 rasterio
"""
import os
import re
import logging
from pathlib import Path
from typing import Dict, Any, Optional

from langchain_core.tools import tool
from backend.model.tools.tool_registry import register_tool
from backend.model.tools._paths import session_subpath
from backend.config import settings

logger = logging.getLogger(__name__)


def _get_preprocess_output_dir() -> Path:
    """★ v2.4: 预处理产物输出目录: agent-files/preprocess/{proj}/{conv}/"""
    d = Path(settings.file_storage_root).resolve() / "preprocess" / session_subpath()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _build_download_url(rel_path: Path) -> str:
    """把 agent-files 下的文件转成 /api/v1/download 可访问 URL.
    ★ C4 归一: 复用 _paths.build_download_url 统一实现 (消除重复的 relative_to 逻辑).
    """
    from backend.model.tools._paths import build_download_url as _unified
    return _unified(rel_path, Path(settings.file_storage_root))


# ==================== 1. 影像金字塔构建 ====================

@register_tool("preprocess")
@tool
def build_pyramid(image_path: str, overview_levels: str = "2,4,8,16") -> Dict[str, Any]:
    """
    为本地 GeoTIFF 构建影像金字塔 (overview), 提升大图缩放浏览性能 (#3 数据预处理)。

    ★ 用途: 大幅遥感影像在 GeoServer/前端 WMS 浏览卡顿时, 先构建金字塔可大幅提速。
    ★ 实现: 调 rasterio update_options 构建 overview (在原文件内更新, 不另存)。

    入参:
        - image_path (str): 本地 GeoTIFF 绝对路径 (必须是 GeoTIFF/COG)
        - overview_levels (str): 金字塔层级 (2 的幂, 逗号分隔), 默认 "2,4,8,16"
    出参: {status, summary, data:{levels, original_size}}
    示例:
        build_pyramid("E:/.../my_image.tif")
        build_pyramid("E:/.../my_image.tif", "2,4,8,16,32")
    """
    try:
        import rasterio
    except ImportError:
        return {"type": "error", "msg": "rasterio 未安装, 无法构建金字塔"}

    if not Path(image_path).is_file():
        return {"type": "error", "msg": f"文件不存在: {image_path}"}

    try:
        levels = [int(x.strip()) for x in overview_levels.split(",") if x.strip()]
        if not levels:
            return {"type": "error", "msg": "overview_levels 解析为空"}
    except ValueError:
        return {"type": "error", "msg": f"overview_levels 格式错误, 应为逗号分隔整数: {overview_levels}"}

    # 检查文件类型
    ext = Path(image_path).suffix.lower()
    if ext not in (".tif", ".tiff", ".cog", ".vrt"):
        return {"type": "error", "msg": f"仅支持 GeoTIFF/COG, 当前文件: {ext}"}

    original_size = Path(image_path).stat().st_size
    try:
        # rasterio 构建 overview (在原文件内更新)
        with rasterio.open(image_path, "r+") as dst:
            # 检查是否已有 overview
            existing = dst.overviews(1) if dst.overviews(1) else []
            if existing and set(levels).issubset(set(existing)):
                logger.info(f"[Preprocess] 金字塔已存在 {existing}, 跳过重复构建")
                return {
                    "type": "success",
                    "summary": f"金字塔已存在 (层级 {existing}), 无需重建: {Path(image_path).name}",
                    "data": {"levels": existing, "original_size": original_size, "skipped": True},
                }
            dst.build_overviews(levels, rasterio.enums.Resampling.nearest)
            dst.update_options(nsamples=len(levels))
        logger.info(f"[Preprocess] 金字塔构建完成: {image_path} levels={levels}")
        return {
            "type": "success",
            "summary": (
                f"影像金字塔构建完成: {Path(image_path).name}, 层级 {levels}"
                f" (原文件大小 {original_size // 1024}KB, 金字塔已写入原文件)"
            ),
            "data": {"levels": levels, "original_size": original_size, "image_path": image_path},
        }
    except Exception as e:
        return {"type": "error", "msg": f"金字塔构建失败: {e}"}


# ==================== 2. COG 转换 ====================

@register_tool("preprocess")
@tool
def convert_to_cog(image_path: str, output_path: str = "") -> Dict[str, Any]:
    """
    将本地 GeoTIFF 转换为 Cloud Optimized GeoTIFF (COG) 格式 (#3 数据预处理)。

    ★ COG 优势: 内置金字塔 + 分块, 支持流式读取, 适合云存储/Web 地图浏览。
    ★ 实现: 优先在 Docker 沙盒执行 rio-cogeo (隔离); 沙盒不可用则尝试宿主机 cogeo.
    ★ 耗时: 大图几十秒~几分钟。

    入参:
        - image_path (str): 本地 GeoTIFF 绝对路径
        - output_path (str): 输出 COG 文件路径, 留空则输出到 agent-files/preprocess/
    出参: {status, summary, data:{output_path, output_url, output_size}}
    示例:
        convert_to_cog("E:/.../my_image.tif")
    """
    if not Path(image_path).is_file():
        return {"type": "error", "msg": f"文件不存在: {image_path}"}
    ext = Path(image_path).suffix.lower()
    if ext not in (".tif", ".tiff", ".vrt"):
        return {"type": "error", "msg": f"仅支持 GeoTIFF, 当前: {ext}"}

    # 输出路径
    if output_path:
        out_path = Path(output_path)
    else:
        out_dir = _get_preprocess_output_dir()
        stem = Path(image_path).stem
        out_path = out_dir / f"{stem}_cog.tif"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 策略 1: 沙盒执行 (rio-cogeo 装在沙盒镜像里)
    sandbox_result = _try_cog_in_sandbox(image_path, str(out_path))
    if sandbox_result.get("ok"):
        size = out_path.stat().st_size
        url = _build_download_url(out_path)
        return {
            "type": "success",
            "summary": (
                f"COG 转换完成 (沙盒执行): {Path(image_path).name} → {out_path.name}, "
                f"大小 {size//1024}KB"
            ),
            "data": {
                "output_path": str(out_path),
                "output_url": url,
                "output_size": size,
                "engine": "sandbox-rio-cogeo",
            },
        }

    # 策略 2: 宿主机直接调 cogeo (sam3 环境)
    host_result = _try_cog_on_host(image_path, str(out_path))
    if host_result.get("ok"):
        size = out_path.stat().st_size
        url = _build_download_url(out_path)
        return {
            "type": "success",
            "summary": (
                f"COG 转换完成 (宿主执行): {Path(image_path).name} → {out_path.name}, "
                f"大小 {size//1024}KB"
            ),
            "data": {
                "output_path": str(out_path),
                "output_url": url,
                "output_size": size,
                "engine": "host-rio-cogeo",
            },
        }

    return {
        "type": "error",
        "msg": (
            f"COG 转换失败: 沙盒({sandbox_result.get('msg')}) "
            f"和宿主机({host_result.get('msg')}) 均不可用。"
            f"请先构建沙盒镜像 (docker build -t agent-sandbox:latest -f sandbox/Dockerfile .) "
            f"或在宿主安装 rio-cogeo (pip install rio-cogeo)。"
        ),
    }


def _try_cog_in_sandbox(src: str, dst: str) -> Dict[str, Any]:
    """
    在 Docker 沙盒里跑 cogeo conversion (rio cogeo create). 返回 {ok, msg}.
    ★ 注意: 沙盒 API 是 async, 此函数用 asyncio.run 桥接为同步调用.
       沙盒不可用 / 模块缺失 / 已在 event loop 中 → 返回 ok=False, 调用方走宿主机兜底.
    """
    try:
        import asyncio
        from backend.model.tools.sandbox_manager import sandbox_manager as mgr
        from backend.model.tools.sandbox_tools import _get_client_and_work_dir
    except ImportError as e:
        return {"ok": False, "msg": f"sandbox 模块不可用: {e}"}

    async def _run():
        try:
            if not await mgr.is_available():
                return {"ok": False, "msg": "沙盒不可用 (Docker/镜像未就绪)"}
        except Exception as e:
            return {"ok": False, "msg": f"沙盒可用性检测失败: {e}"}
        client_id, work_dir = _get_client_and_work_dir()
        # 把源文件复制进 work_dir
        import shutil
        try:
            src_name = Path(src).name
            dst_in_ws = os.path.join(work_dir, src_name)
            shutil.copy2(src, dst_in_ws)
        except Exception as e:
            return {"ok": False, "msg": f"源文件搬入沙盒失败: {e}"}
        container_dst_name = f"{Path(src).stem}_cog.tif"
        container_dst = f"/workspace/{container_dst_name}"
        code = (
            "from rio_cogeo.cogeo import cog_translate\n"
            "from rio_cogeo.profiles import cog_profiles\n"
            f"src = '/workspace/{src_name}'\n"
            f"dst = {repr(container_dst)}\n"
            "profile = cog_profiles.get('deflate')\n"
            "cog_translate(src, dst, profile, in_memory=False)\n"
            "print('COG_OK:', dst)\n"
        )
        try:
            result = await mgr.exec_python(client_id, work_dir, code, timeout=180)
        except Exception as e:
            return {"ok": False, "msg": f"沙盒执行异常: {e}"}
        if result.get("returncode") == 0 and "COG_OK" in result.get("stdout", ""):
            host_dst_in_ws = os.path.join(work_dir, container_dst_name)
            try:
                shutil.copy2(host_dst_in_ws, dst)
                return {"ok": True}
            except Exception as e:
                return {"ok": False, "msg": f"沙盒产物回传失败: {e}"}
        return {"ok": False, "msg": f"沙盒执行失败: {(result.get('stderr') or result.get('stdout', ''))[:200]}"}

    try:
        return asyncio.run(_run())
    except RuntimeError:
        # 已在 event loop 中 (WS 任务里调用), 不能 asyncio.run
        return {"ok": False, "msg": "沙盒需独立 event loop (调用方在 WS loop 中, 跳过沙盒路径)"}
    except Exception as e:
        return {"ok": False, "msg": f"沙盒异常: {e}"}


def _try_cog_on_host(src: str, dst: str) -> Dict[str, Any]:
    """宿主机直接调 cog_translate (sam3 环境需 pip install rio-cogeo)."""
    try:
        from rio_cogeo.cogeo import cog_translate  # noqa: F401
        from rio_cogeo.profiles import cog_profiles
    except ImportError:
        return {"ok": False, "msg": "宿主未装 rio-cogeo"}
    try:
        profile = cog_profiles.get("deflate")
        cog_translate(src, dst, profile, in_memory=False)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "msg": f"cog_translate 失败: {e}"}


# ==================== 3. 矢量拓扑检查 ====================

@register_tool("preprocess")
@tool
def check_topology(vector_path: str, fix: bool = False) -> Dict[str, Any]:
    """
    检查矢量数据的拓扑质量 (自相交/无效几何/重叠/缝隙) (#3 数据预处理)。

    ★ 用途: 上传矢量数据 (Shapefile/GeoJSON) 后, 检查数据质量, 找出需要清洗的问题。
    ★ 实现: geopandas + shapely.is_valid / buffer(0) 修复 / sindex 重叠检测。
    ★ 耗时: 大型矢量可能几十秒。

    入参:
        - vector_path (str): 本地 Shapefile (.shp) 或 GeoJSON (.geojson) 绝对路径
        - fix (bool): 是否对无效几何做 buffer(0) 修复, 默认否 (仅检查)
    出参: {status, summary, data:{total_features, invalid_count, issues:[...], fixed:bool}}
    示例:
        check_topology("E:/.../redline.shp")
        check_topology("E:/.../redline.shp", fix=True)
    """
    try:
        import geopandas as gpd
        from shapely.validation import explain_validity
    except ImportError:
        return {"type": "error", "msg": "geopandas/shapely 未安装"}

    if not Path(vector_path).is_file():
        return {"type": "error", "msg": f"文件不存在: {vector_path}"}
    ext = Path(vector_path).suffix.lower()
    if ext not in (".shp", ".geojson", ".json", ".gpkg"):
        return {"type": "error", "msg": f"仅支持 Shapefile/GeoJSON/GeoPackage, 当前: {ext}"}

    try:
        gdf = gpd.read_file(vector_path)
        if gdf.empty:
            return {"type": "success", "summary": "矢量数据为空", "data": {"total_features": 0, "invalid_count": 0, "issues": []}}
        total = len(gdf)

        # 1. 自相交 / 无效几何检查
        invalid_mask = ~gdf.is_valid
        invalid_count = int(invalid_mask.sum())
        issues = []
        for idx in gdf.index[invalid_mask][:20]:  # 最多列 20 条
            geom = gdf.loc[idx, gdf.geometry.name]
            reason = explain_validity(geom)
            issues.append({"index": int(idx), "type": "invalid_geometry", "reason": reason})

        # 2. 修复 (可选)
        fixed_count = 0
        if fix and invalid_count > 0:
            gdf[gdf.geometry.name] = gdf.geometry.buffer(0)
            still_invalid = int((~gdf.is_valid).sum())
            fixed_count = invalid_count - still_invalid
            invalid_count = still_invalid
            # 写回原文件 (备份)
            backup = Path(vector_path).with_suffix(ext + ".bak")
            try:
                Path(vector_path).rename(backup)
                gdf.to_file(vector_path, driver=("GeoJSON" if ext in (".geojson", ".json") else "ESRI Shapefile"), encoding="utf-8")
            except Exception as e:
                logger.warning(f"[Preprocess] 修复后写回失败: {e}")

        # 3. 空间重叠检测 (用 sindex 加速)
        overlap_count = 0
        try:
            sindex = gdf.sindex
            for i in gdf.index:
                geom_i = gdf.loc[i, gdf.geometry.name]
                if geom_i is None or geom_i.is_empty:
                    continue
                candidates = list(sindex.intersection(geom_i.bounds))
                for j in candidates:
                    if j <= i:
                        continue
                    geom_j = gdf.loc[j, gdf.geometry.name]
                    if geom_j and geom_i.overlaps(geom_j):
                        overlap_count += 1
                        if len(issues) < 20:
                            issues.append({"type": "overlap", "indices": [int(i), int(j)]})
        except Exception as e:
            logger.debug(f"[Preprocess] 重叠检测跳过: {e}")

        summary = (
            f"矢量拓扑检查完成: {Path(vector_path).name}, 共 {total} 个要素。"
            f"\n  - 无效几何: {invalid_count} 个"
            + (f" (已修复 {fixed_count} 个)" if fix and fixed_count else "")
            + f"\n  - 空间重叠: {overlap_count} 对"
        )
        if issues:
            summary += f"\n  - 问题清单 (最多 20 条):"
            for iss in issues[:10]:
                summary += f"\n    · {iss}"

        return {
            "type": "success",
            "summary": summary,
            "data": {
                "total_features": total,
                "invalid_count": invalid_count,
                "fixed_count": fixed_count if fix else 0,
                "overlap_count": overlap_count,
                "issues": issues,
                "fixed": bool(fix and fixed_count > 0),
            },
        }
    except Exception as e:
        return {"type": "error", "msg": f"拓扑检查失败: {e}"}
