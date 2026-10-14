"""文件下载/上传/元数据路由。

- /api/v1/download/{filepath}: 通用产物下载 (含影像预览转码)
- /api/v1/upload/{filepath}: 读取用户上传的原图
- /api/v1/image/meta: 影像地理元数据
- /api/v1/vector/shp-to-geojson: Shapefile → GeoJSON
- /api/v1/samseg/upload: 上传分割/变化检测影像
- /api/v1/files/upload: 通用资料上传
- /api/v1/files/upload-shapefile: 上传 Shapefile 全套组件
"""
import logging
import mimetypes
import re
import time
import random
import string
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse

from backend.api.deps import FILE_ROOT, SAMSEG_UPLOAD, agent_db, business_db

router = APIRouter()
logger = logging.getLogger(__name__)

# ★ Shapefile 组件后缀: .shp 主文件 + .shx/.dbf 必需 + .prj/.cpg/... 可选
#   upload_shapefile 发布时按 .shp 同目录同 stem 找这些组件。
_SHAPEFILE_EXTS = (
    ".shp", ".shx", ".dbf", ".prj", ".cpg", ".sbn", ".sbx",
    ".fbn", ".fbx", ".ain", ".aih", ".qix", ".sbn",
)

# 影像后缀 (用于通用上传时区分影像/非影像, 影像走 CRS 预检)
_IMAGE_EXTS = (".tif", ".tiff", ".cog", ".png", ".jpg", ".jpeg", ".bmp", ".webp")


# ==================== 文件下载 / 原图读取 ====================

@router.get("/api/v1/download/{filepath:path}")
async def download_file(filepath: str, request: Request):
    """
    下载已生成的文件（GeoTIFF / PNG 等）。
    ★ v2.3: 文件存放在 agent-files/ 下按来源分目录,
      filepath 已包含来源前缀 (如 samseg/generate/... 或 geoserver/generate/...)
    ★ 影像预览: 带 ?preview=1 时, 对浏览器不支持的格式 (tif/geotiff 等) 自动转 PNG
      返回 (供 <img> 直接显示); 浏览器原生格式 (png/jpg/...) 零开销原样返回。
      不带 ?preview=1 时行为完全不变 (renderDownload 等下载场景走原路径)。
    """
    file_path = (FILE_ROOT / filepath).resolve()
    if not str(file_path).startswith(str(FILE_ROOT)):
        raise HTTPException(status_code=403, detail="禁止访问")
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"文件不存在: {filepath}")

    # ★ 影像预览转码: 仅在 ?preview=1 且非浏览器原生格式时触发
    if request.query_params.get("preview") == "1":
        from backend.model.tools import image_preview
        try:
            img_bytes, mime = image_preview.render_image_to_png_bytes(file_path)
            return Response(content=img_bytes, media_type=mime)
        except Exception as e:
            # 转码失败 → 兜底走原文件下载 (不比现状差)
            logger.warning(f"[download] 影像转码失败, 落回原文件: {filepath} ({e})")

    media, _ = mimetypes.guess_type(filepath)
    return FileResponse(
        path=str(file_path),
        filename=file_path.name,
        media_type=media or "application/octet-stream",
    )


@router.get("/api/v1/upload/{filepath:path}")
async def serve_upload_file(filepath: str, request: Request):
    """
    读取用户上传的文件 (SamSeg 输入影像等), 供前端展示原图。
    ★ v2.3: 文件存放在 agent-files/samseg/send/ 下。
    ★ 影像预览: 带 ?preview=1 时, 对浏览器不支持的格式 (tif/geotiff 等) 自动转 PNG
      返回 (供 <img> 直接显示); 浏览器原生格式 (png/jpg/...) 零开销原样返回。
      不带 ?preview=1 时行为完全不变。
    """
    file_path = (SAMSEG_UPLOAD / filepath).resolve()
    if not str(file_path).startswith(str(SAMSEG_UPLOAD)):
        raise HTTPException(status_code=403, detail="禁止访问")
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"文件不存在: {filepath}")

    # ★ 影像预览转码: 仅在 ?preview=1 且非浏览器原生格式时触发
    if request.query_params.get("preview") == "1":
        from backend.model.tools import image_preview
        try:
            img_bytes, mime = image_preview.render_image_to_png_bytes(file_path)
            return Response(content=img_bytes, media_type=mime)
        except Exception as e:
            # 转码失败 → 兜底走原文件下载 (不比现状差)
            logger.warning(f"[upload] 影像转码失败, 落回原文件: {filepath} ({e})")

    media, _ = mimetypes.guess_type(filepath)
    return FileResponse(
        path=str(file_path),
        filename=file_path.name,
        media_type=media or "application/octet-stream",
    )


# ==================== 影像元数据 / Shapefile 转 GeoJSON ====================

@router.get("/api/v1/image/meta")
async def get_image_meta(path: str):
    """
    返回影像的地理元数据 (供前端卡片显示坐标范围 + CRS)。

    ★ 用 rasterio 读; 非 GeoTIFF / 无 CRS 的影像返回 has_crs=false。
    ★ 安全: path 必须在 agent-files/ 或 samseg/send/ 之下, 防路径越权。

    入参: path (str) - 影像宿主机绝对路径 或 相对 agent-files/ 的路径
    出参: {has_crs, crs, bbox: [minx,miny,maxx,maxy], width, height, driver}
    """
    from pathlib import Path as _Path

    # 路径解析: 支持绝对路径 或 相对 FILE_ROOT 的路径
    p = _Path(path).expanduser()
    if not p.is_absolute():
        p = (FILE_ROOT / path).resolve()
    else:
        p = p.resolve()

    # 安全边界: 必须在 FILE_ROOT 之下
    if not str(p).startswith(str(FILE_ROOT)):
        return {"has_crs": False, "msg": "路径不在可访问范围内"}

    if not p.is_file():
        return {"has_crs": False, "msg": f"文件不存在: {path}"}

    try:
        import rasterio
        with rasterio.open(str(p)) as src:
            crs = src.crs
            if crs is None:
                return {"has_crs": False, "width": src.width, "height": src.height,
                        "driver": src.driver, "msg": "影像无 CRS (本地影像)"}
            # 计算 bbox (4 个角点的地理坐标)
            # rasterio bounds: (left, bottom, right, top) = (minx, miny, maxx, maxy)
            bounds = src.bounds
            bbox = [round(bounds.left, 6), round(bounds.bottom, 6),
                    round(bounds.right, 6), round(bounds.top, 6)]
            # CRS 展示名 (优先 EPSG:xxxx, 兜底从 WKT 提取投影名)
            crs_str = str(crs)
            epsg = None
            try:
                epsg = crs.to_epsg()
            except Exception:
                pass
            if epsg:
                crs_str = f"EPSG:{epsg}"
            else:
                # to_epsg 失败 (如 PROJ 数据库冲突) → 从 WKT 提取 PROJCS/GEOGCS 名字简写
                import re as _re
                m = _re.search(r'PROJCS\["([^"]+)"', crs_str) or _re.search(r'GEOGCS\["([^"]+)"', crs_str)
                if m:
                    crs_str = m.group(1)
                elif len(crs_str) > 40:
                    crs_str = crs_str[:37] + "..."
            return {
                "has_crs": True,
                "crs": crs_str,
                "bbox": bbox,
                "width": src.width,
                "height": src.height,
                "driver": src.driver,
            }
    except ImportError:
        return {"has_crs": False, "msg": "rasterio 未安装"}
    except Exception as e:
        # 非影像文件 / rasterio 读不了 → 尝试用 PIL 读尺寸
        try:
            from PIL import Image
            with Image.open(str(p)) as img:
                return {"has_crs": False, "width": img.width, "height": img.height,
                        "msg": f"非地理影像 ({e})"}
        except Exception:
            return {"has_crs": False, "msg": f"读取失败: {e}"}


@router.get("/api/v1/vector/shp-to-geojson")
async def shp_to_geojson(shp_path: str):
    """
    读 Shapefile → 返回 GeoJSON (供前端 OpenLayers 渲染矢量层)。

    ★ 设计:
      - geopandas.read_file 自动解析同目录的 .dbf/.shx/.prj, 前端只需传 .shp 主文件路径。
      - 兼容两种入参形态:
          ① .shp 主文件绝对路径 (如 .../xxx_shp/xxx.shp)
          ② shp 多文件目录路径 (即工具返回的 shp_zip_path 字段, 实为目录) → 自动取目录下第一个 .shp
      - 返回标准 GeoJSON FeatureCollection, OL 的 ol.format.GeoJSON 可直接消费。
      - 安全边界: 路径必须在 FILE_ROOT (agent-files) 之下, 防路径越权。

    入参:
      - shp_path (str): .shp 主文件路径 或 shp 目录路径 (绝对 或 相对 FILE_ROOT)
    出参:
      - {success, geojson, feature_count, crs} 或 {success: False, msg}
    """
    from pathlib import Path as _Path

    # 路径解析: 绝对路径 或 相对 FILE_ROOT
    p = _Path(shp_path).expanduser()
    if not p.is_absolute():
        p = (FILE_ROOT / shp_path).resolve()
    else:
        p = p.resolve()

    # 安全边界: 必须在 FILE_ROOT 之下
    if not str(p).startswith(str(FILE_ROOT)):
        return {"success": False, "msg": "路径不在可访问范围内"}

    # 兼容目录入参: 若传的是目录 (shp_zip_path 字段), 取目录下第一个 .shp
    if p.is_dir():
        shp_files = sorted(p.glob("*.shp"))
        if not shp_files:
            return {"success": False, "msg": f"目录下无 .shp 文件: {shp_path}"}
        p = shp_files[0].resolve()
    elif not p.is_file():
        return {"success": False, "msg": f"文件不存在: {shp_path}"}
    elif p.suffix.lower() != ".shp":
        return {"success": False, "msg": f"不是 Shapefile (.shp): {shp_path}"}

    try:
        import geopandas as gpd
    except ImportError:
        return {"success": False, "msg": "geopandas 未安装, 无法读取 Shapefile"}

    try:
        gdf = gpd.read_file(str(p))
    except Exception as e:
        return {"success": False, "msg": f"Shapefile 读取失败: {e}"}

    if gdf is None or gdf.empty:
        return {"success": False, "msg": "Shapefile 为空 (无有效要素)"}

    feature_count = len(gdf)

    # ★ 大文件保护: feature 数 > 5000 时采样, 避免前端 OL 渲染卡死
    sampled = False
    if feature_count > 5000:
        gdf = gdf.sample(n=5000, random_state=42)
        sampled = True

    # CRS 信息 (供前端判断投影, OL 默认按 EPSG:4326 解析)
    crs_str = None
    try:
        if gdf.crs is not None:
            epsg = None
            try:
                epsg = gdf.crs.to_epsg()
            except Exception:
                pass
            crs_str = f"EPSG:{epsg}" if epsg else str(gdf.crs)
            # 非 4326 时转 4326 (OL 前端按经纬度渲染)
            if epsg and epsg != 4326:
                gdf = gdf.to_crs(epsg=4326)
                crs_str = "EPSG:4326"
    except Exception as e:
        logger.warning(f"[SHP2GeoJSON] CRS 处理失败, 按原始坐标返回: {e}")

    try:
        geojson = gdf.to_json()
    except Exception as e:
        return {"success": False, "msg": f"GeoJSON 序列化失败: {e}"}

    logger.info(f"[SHP2GeoJSON] {p.name} → {feature_count} 要素 (采样={sampled}), crs={crs_str}")
    return {
        "success": True,
        "geojson": geojson,
        "feature_count": feature_count,
        "crs": crs_str,
        "sampled": sampled,
        "shp_path": str(p),
    }


# ==================== 文件上传 ====================

@router.post("/api/v1/samseg/upload")
async def upload_samseg_images(
    images: list[UploadFile] = File(...),
    conversation_id: str = Form(None),
):
    """
    接收前端上传的图片 (1-2 张), 存到 agent-files/send/{conversation_id}/, 返回绝对路径列表供 agent 工具使用。

    ★ v2.3 规范化:
      - 保存根目录: agent-files/send/{conversation_id}/
      - 命名规则: {conv前缀}_{原文件名} (同会话同文件名重传时加短随机后缀防覆盖)
      - 无会话 id 时: 退回 agent-files/send/_anonymous/{时间戳}_{原文件名}

    - 入参: multipart/form-data
      - images: 1-2 个图片文件 (分割 1 张, 变化检测 2 张)
      - conversation_id (可选): 所属会话 id
    - 出参: {status, paths: [绝对路径...], count, files:[{name,path,preview_url,source}]}
    - 前端拿到 files 后静默保存为聊天附件, 不再把路径写入输入框。
    """
    if not images:
        raise HTTPException(status_code=400, detail="未收到图片文件")

    files = images[:2]  # 最多 2 张

    # ★ v2.3: 按会话隔离上传文件 → agent-files/samseg/send/{conversation_id}/
    conv_prefix = ""
    if conversation_id:
        conv_prefix = re.sub(r"[^a-zA-Z0-9._-]", "", conversation_id)
    conv_dir_name = conv_prefix if conv_prefix else "_anonymous"
    upload_dir = (SAMSEG_UPLOAD / conv_dir_name).resolve()
    upload_dir.mkdir(parents=True, exist_ok=True)

    saved_paths = []
    saved_files = []
    for f in files:
        if not f.filename:
            continue
        # 防路径遍历: 只取文件名
        safe_name = Path(f.filename).name
        if conv_prefix:
            # 带会话 id 命名; 若已存在同名文件, 加 4 位随机后缀防覆盖
            fname = f"{conv_prefix}_{safe_name}"
            dest = upload_dir / fname
            if dest.exists():
                suffix = _rand_suffix(4)
                stem = Path(safe_name).stem
                ext = Path(safe_name).suffix
                dest = upload_dir / f"{conv_prefix}_{stem}_{suffix}{ext}"
        else:
            # 无会话 id: 退回时间戳前缀 (兼容)
            ts = int(time.time() * 1000)
            dest = upload_dir / f"{ts}_{safe_name}"
        content = await f.read()
        dest.write_bytes(content)
        saved_paths.append(str(dest))
        rel = dest.relative_to(SAMSEG_UPLOAD).as_posix()
        saved_files.append({
            "name": dest.name,
            "path": str(dest),
            "preview_url": f"/api/v1/upload/{quote(rel)}?preview=1",
            "source": "upload",
        })
        logger.info(f"[SamSeg] 图片已上传: {dest.name} conv={conv_prefix[:8] or '-'} ({len(content)//1024}KB)")

    if not saved_paths:
        raise HTTPException(status_code=400, detail="无有效图片文件")

    # ★ 系统只接受带坐标影像: 上传后硬拦截无 CRS 影像 (删除已落盘文件 + 400)
    bad = _reject_ungeoreferenced(saved_paths)
    if bad:
        raise HTTPException(
            status_code=400,
            detail=bad[0],  # 取第一条错误信息 (中文提示)
        )

    # ★ Phase A: 上传影像自动登记元数据 (失败不阻塞)
    #   仅对 GeoTIFF/COG 等地理影像生效 (能读出 transform/crs);
    #   普通图片 (PNG/JPG) 无地理信息则跳过.
    for p in saved_paths:
        try:
            _try_register_image_metadata(p, conv_prefix or "_anonymous")
        except Exception as e:
            logger.warning(f"[Metadata] 影像元数据登记失败 ({Path(p).name}): {e}")

    return {
        "status": "success",
        "paths": saved_paths,
        "count": len(saved_paths),
        "files": saved_files,
    }


@router.post("/api/v1/files/upload")
async def upload_general_files(
    files: list[UploadFile] = File(...),
    conversation_id: str = Form(None),
):
    """
    接收前端上传的通用资料文件, 存到 agent-files/samseg/send/{conversation_id}/, 返回绝对路径列表供 Agent 继续使用。

    入参:
        - files: 一个或多个通用文件, 允许图片/GeoJSON/JSON/PDF/Markdown/文本/表格/压缩包等。
        - conversation_id: 可选会话 ID, 用于把上传文件与当前会话目录绑定。
    方法:
        - 复用现有 samseg/send 目录作为用户输入资料区, 保持 /api/v1/upload 路径兼容。
        - 统一做文件名净化与同名防覆盖, 不在这里限制业务类型, 让上层 Agent 决定如何消费。
    出参:
        - {status, paths, count, files:[{name, path, size, content_type}]}
    """
    if not files:
        raise HTTPException(status_code=400, detail="未收到上传文件")

    conv_prefix = ""
    if conversation_id:
        conv_prefix = re.sub(r"[^a-zA-Z0-9._-]", "", conversation_id)
    conv_dir_name = conv_prefix if conv_prefix else "_anonymous"
    upload_dir = (SAMSEG_UPLOAD / conv_dir_name).resolve()
    upload_dir.mkdir(parents=True, exist_ok=True)

    saved_paths = []
    saved_files = []
    for f in files:
        if not f.filename:
            continue

        safe_name = Path(f.filename).name
        if conv_prefix:
            fname = f"{conv_prefix}_{safe_name}"
            dest = upload_dir / fname
            if dest.exists():
                suffix = _rand_suffix(4)
                stem = Path(safe_name).stem
                ext = Path(safe_name).suffix
                dest = upload_dir / f"{conv_prefix}_{stem}_{suffix}{ext}"
        else:
            dest = upload_dir / safe_name
            if dest.exists():
                suffix = _rand_suffix(4)
                stem = Path(safe_name).stem
                ext = Path(safe_name).suffix
                dest = upload_dir / f"{stem}_{suffix}{ext}"

        content = await f.read()
        dest.write_bytes(content)
        saved_paths.append(str(dest))
        saved_files.append({
            "name": safe_name,
            "path": str(dest),
            "size": len(content),
            "content_type": f.content_type or "",
        })
        logger.info(
            f"[Upload] 通用文件已上传: {dest.name} conv={conv_prefix[:8] or '-'} "
            f"({len(content) // 1024}KB, type={f.content_type or '-'})"
        )

    if not saved_paths:
        raise HTTPException(status_code=400, detail="无有效上传文件")

    # ★ 系统只接受带坐标影像: 仅校验影像类后缀 (.tif/.tiff/.cog/.png/.jpg/.jpeg/.bmp/.webp),
    #   非影像资料 (.json/.pdf/.shp 等) 放行不变; 无 CRS 影像删除已落盘文件 + 400。
    image_paths = [p for p in saved_paths if Path(p).suffix.lower() in _IMAGE_EXTS]
    if image_paths:
        bad = _reject_ungeoreferenced(image_paths)
        if bad:
            raise HTTPException(
                status_code=400,
                detail=bad[0],
            )

    return {
        "status": "success",
        "paths": saved_paths,
        "count": len(saved_paths),
        "files": saved_files,
    }


@router.post("/api/v1/files/upload-shapefile")
async def upload_shapefile_components(
    files: list[UploadFile] = File(...),
    conversation_id: str = Form(None),
):
    """
    上传 Shapefile 全部组件 (.shp/.shx/.dbf/.prj/.cpg...) 并落盘到同一目录,
    返回 .shp 在服务器的绝对路径, 供前端调 /api/v1/geoserver/layers 发布。

    ★ 为什么需要这个端点: Shapefile 是多文件组合, 浏览器单文件上传只传 .shp,
      服务器无同目录 .shx/.dbf → upload_shapefile 报 "缺少必要文件: xxx.shx"。
      本端点让前端一次性上传全部组件, 保证同 stem 同目录落盘。

    入参:
        - files: shapefile 全部组件 (必须含 .shp, 建议含 .shx/.dbf/.prj)
        - conversation_id: 可选会话 ID, 落盘到 samseg/send/{conv_id}/
    方法:
        - 按 .shp 的 stem 校验组件集合; 同一 stem 的全部组件用同一 stem 落盘
          (不各自加随机后缀, 否则 upload_shapefile 找不到同 stem 的 .shx/.dbf)
        - 同目录已存在同名 .shp 时, 整组 stem 加同一个随机后缀
    出参:
        - {status, shp_path, stem, components:[{name, path, size}], warnings:[]}
        - status != success 时前端据 msg 提示用户补齐组件
    """
    if not files:
        raise HTTPException(status_code=400, detail="未收到上传文件")

    # 1) 净化文件名 + 校验至少一个 .shp
    received = []  # [(stem_lower, ext_lower, original_name, UploadFile)]
    shp_stems = set()
    for f in files:
        if not f.filename:
            continue
        safe_name = Path(f.filename).name
        stem = Path(safe_name).stem
        ext = Path(safe_name).suffix.lower()
        if ext not in _SHAPEFILE_EXTS:
            # 忽略非 shapefile 组件 (容错)
            continue
        received.append((stem.lower(), stem, ext, safe_name, f))
        if ext == ".shp":
            shp_stems.add(stem.lower())

    if not received:
        raise HTTPException(status_code=400, detail="未收到有效的 shapefile 组件 (.shp/.shx/.dbf/...)")
    if not shp_stems:
        raise HTTPException(status_code=400, detail="缺少 .shp 主文件, 请选中完整的 shapefile 组件集")

    # 2) 校验每个 .shp 都有 .shx + .dbf (upload_shapefile 必需)
    #    按 stem 分组
    by_stem: dict[str, dict[str, tuple]] = {}
    for stem_lower, stem, ext, name, f in received:
        by_stem.setdefault(stem_lower, {"_stem": stem})[ext] = (name, f)

    warnings = []
    for stem_lower, group in by_stem.items():
        if ".shp" not in group:
            warnings.append(f"{group['_stem']}: 缺少 .shp 主文件")
            continue
        missing = [e for e in (".shx", ".dbf") if e not in group]
        if missing:
            warnings.append(f"{group['_stem']}: 缺少必需组件 {'/'.join(missing)}")

    # 3) 落盘到 samseg/send/{conv_id}/, 同 stem 组件用同一 stem
    conv_prefix = ""
    if conversation_id:
        conv_prefix = re.sub(r"[^a-zA-Z0-9._-]", "", conversation_id)
    conv_dir_name = conv_prefix if conv_prefix else "_anonymous"
    upload_dir = (SAMSEG_UPLOAD / conv_dir_name).resolve()
    upload_dir.mkdir(parents=True, exist_ok=True)

    components_out = []
    shp_path = None

    for stem_lower, group in by_stem.items():
        if ".shp" not in group:
            continue  # 缺 .shp 的组跳过 (已记 warning)
        base_stem = group["_stem"]

        # 同目录已有同名 .shp → 整组 stem 加同一随机后缀, 保持组件同 stem
        dest_shp = upload_dir / f"{base_stem}.shp"
        final_stem = base_stem
        if dest_shp.exists():
            suffix = _rand_suffix(4)
            final_stem = f"{base_stem}_{suffix}"

        for ext, (name, f) in group.items():
            if ext == "_stem":
                continue
            dest = upload_dir / f"{final_stem}{ext}"
            content = await f.read()
            dest.write_bytes(content)
            logger.info(
                f"[Upload] Shapefile 组件已上传: {dest.name} "
                f"conv={conv_prefix[:8] or '-'} ({len(content) // 1024}KB)"
            )
            components_out.append({"name": dest.name, "path": str(dest), "size": len(content)})
            if ext == ".shp":
                shp_path = str(dest)

    if not shp_path:
        raise HTTPException(
            status_code=400,
            detail="未能落盘任何 .shp 组件: " + ("; ".join(warnings) or "未知原因"),
        )

    return {
        "status": "success",
        "shp_path": shp_path,
        "stem": Path(shp_path).stem,
        "components": components_out,
        "warnings": warnings,
    }


# ==================== 上传 helper (CRS 预检 + 元数据登记 + 随机后缀) ====================

def _reject_ungeoreferenced(image_paths: list[str]) -> list[str]:
    """
    上传端点 CRS 预检: 检测影像列表中无 CRS (无地理坐标) 的项。

    ★ 无 CRS 影像 → 删除已落盘文件 (防污染 send 目录) 并返回中文错误信息列表。
    ★ 有 CRS 或读取异常 (保守放行, 避免误伤) → 不计入返回。
    ★ 返回空列表表示全部通过; 非空表示存在无坐标影像 (调用方据此抛 400)。

    出参: [error_msg, ...] (每条含文件名 + 原因); 空列表 = 全部通过
    """
    errors = []
    try:
        from backend.model.SamSeg.geoio import assert_image_has_crs
    except ImportError as e:
        logger.warning(f"[Upload] CRS 校验模块不可用, 跳过预检: {e}")
        return errors  # 模块缺失不阻断 (降级为旧行为)

    for idx, p in enumerate(image_paths, 1):
        ok, reason = assert_image_has_crs(p)
        if not ok:
            # 删除已落盘的无 CRS 影像, 防止 send 目录堆积无效文件
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass
            name = Path(p).name
            errors.append(
                f"第 {idx} 张影像 ({name}) 无地理坐标(CRS)，系统仅接受带坐标的遥感影像(GeoTIFF/COG)。原因: {reason}"
            )
            logger.warning(f"[Upload] 拒绝无 CRS 影像: {name} ({reason})")
    return errors


def _try_register_image_metadata(file_path: str, owner_hint: str = "study_user") -> None:
    """
    尝试把上传的影像文件登记到 image_metadata 表 (#4).
    - 仅 GeoTIFF/COG 等地理影像 (能读 transform/crs) 才登记
    - 普通图片 (PNG/JPG) 无地理信息 → 跳过, 不报错
    - PostGIS 可用时 bbox 转 SRID=4490, 否则存 JSONB
    """
    try:
        import rasterio
    except ImportError:
        return
    p = Path(file_path)
    ext = p.suffix.lower()
    if ext not in (".tif", ".tiff", ".cog", ".vrt"):
        return  # 非地理影像, 跳过
    try:
        with rasterio.open(file_path) as src:
            b = src.bounds
            bbox = (b.left, b.bottom, b.right, b.top)
            try:
                srid = int(src.crs.to_epsg()) if src.crs else None
            except Exception:
                srid = None
            # ★ to_epsg() 对 Web Mercator Auxiliary Sphere 等复合 CRS 常返回 None
            #   用 bounds 数值范围兜底判断: >180 必定是投影坐标 (Web Mercator=3857 最常见)
            if srid is None:
                if abs(b.left) > 180 or abs(b.bottom) > 90:
                    srid = 3857  # Web Mercator 投影
                else:
                    srid = 4490  # 经纬度
            res = float(src.res[0]) if src.res else None
            w, h, bands = src.width, src.height, src.count
        layer_name = f"upload_{p.stem}_{owner_hint[:8]}"
        business_db.register_image_metadata(
            layer_name=layer_name,
            file_path=file_path,
            bbox=bbox,
            srid=srid,
            resolution=res,
            width=w,
            height=h,
            band_count=bands,
            uploaded_by=owner_hint,
        )
    except Exception as e:
        logger.warning(f"[Metadata] 读取影像地理信息失败 ({p.name}): {e}")


def _rand_suffix(n: int = 4) -> str:
    """生成长度 n 的小写字母数字随机后缀 (防同会话同名文件覆盖)"""
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))
