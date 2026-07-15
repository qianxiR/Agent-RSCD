"""
影像预览转码工具 (Model 层 / 工具箱)
- render_image_to_png_bytes: 把任意影像格式 (含遥感 GeoTIFF) 转成浏览器可显示的 PNG bytes。

★ 解决的问题:
  浏览器原生 <img> 不支持 TIFF/GeoTIFF。后端下载/上传端点在收到 ?preview=1 时,
  对不兼容格式调用本模块转码为 PNG 返回, 让前端能直接 <img src> 显示。
  PNG/JPG 等浏览器原生格式不做任何转换 (零开销)。

★ 解码策略 (分层):
  1. 浏览器原生格式 (png/jpg/jpeg/gif/bmp/webp/svg) → 直接 read_bytes 原样返回
  2. rasterio 优先 (遥感 TIFF 多波段/16bit/浮点 GeoTIFF 专用):
     - 多波段 (>=3): 取前 3 个波段做 RGB 组合
     - 单波段: percentile(2~98) 线性拉伸到 0~255, 灰度输出
     - 16bit/浮点自动归一化到 uint8
  3. 退化 PIL.Image.open (普通 tif/bmp 等)
  4. 都失败抛 ValueError

★ 内存缓存 (LRU by mtime): 同一文件未改动时复用上次转码结果, 避免反复解码。

依赖方向: model.tools (本模块) 仅依赖第三方库 (rasterio/PIL/numpy), 不反向依赖 agent 层。
"""
import logging
from collections import OrderedDict
from pathlib import Path
from typing import Tuple

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# ==================== 常量 ====================

# 浏览器 <img> 原生支持的格式 (后缀小写, 不含点): 这些格式前端能直接解码, 无需后端转码
_BROWSER_NATIVE_EXTS = {
    "png", "jpg", "jpeg", "gif", "bmp", "webp", "svg",
}

# 后缀 → MIME (仅浏览器原生格式用, 转码产物一律 image/png)
_NATIVE_MIME = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "bmp": "image/bmp", "webp": "image/webp",
    "svg": "image/svg+xml",
}

# 需要尝试转码的"遥感/非原生"格式后缀 (其他未列出的也走转码, 这里只是文档化常见类型)
_CONVERTIBLE_EXTS = {"tif", "tiff", "geotiff"}

# 转码单文件大小上限 (200MB), 超过拒绝转码 (防止超大 GeoTIFF 打爆内存)
_MAX_CONVERT_BYTES = 200 * 1024 * 1024

# ==================== 内存 LRU 缓存 ====================
# key: (绝对路径字符串, mtime_ns) → value: (png_bytes, mime)
# 用 OrderedDict 实现简易 LRU; 总缓存大小上限 ~50MB (按 bytes 估算)
_PREVIEW_CACHE: "OrderedDict[Tuple[str, int], Tuple[bytes, str]]" = OrderedDict()
_CACHE_MAX_BYTES = 50 * 1024 * 1024
_cache_current_bytes = 0


def _cache_get(key):
    """命中则提到队首并返回, 否则 None。"""
    global _cache_current_bytes
    if key in _PREVIEW_CACHE:
        _PREVIEW_CACHE.move_to_end(key)
        return _PREVIEW_CACHE[key]
    return None


def _cache_put(key, value):
    """写入缓存, 并在超限时按 LRU 逐出最旧条目。"""
    global _cache_current_bytes
    if key in _PREVIEW_CACHE:
        _cache_current_bytes -= len(_PREVIEW_CACHE[key][0])
    _PREVIEW_CACHE[key] = value
    _cache_current_bytes += len(value[0])
    # 逐出最旧条目直到总大小 <= 上限
    while _cache_current_bytes > _CACHE_MAX_BYTES and _PREVIEW_CACHE:
        _, old = _PREVIEW_CACHE.popitem(last=False)
        _cache_current_bytes -= len(old[0])
    if _cache_current_bytes < 0:
        _cache_current_bytes = 0


def clear_preview_cache():
    """清空转码缓存 (调试/显式释放内存用)。"""
    global _cache_current_bytes
    _PREVIEW_CACHE.clear()
    _cache_current_bytes = 0


# ==================== 核心转码 ====================

def _normalize_band(band: np.ndarray) -> np.ndarray:
    """
    单波段归一化到 uint8 [0,255]:
    - 用 percentile(2,98) 做线性拉伸, 抗极端值 (遥感影像常有少量极亮/极暗噪点)
    - NaN/Inf 当作 0
    - 全相同值 (如全 0) → 直接返回 0 数组, 避免除零
    """
    arr = band.astype(np.float64)
    arr = np.where(np.isfinite(arr), arr, 0.0)
    lo, hi = np.percentile(arr, 2), np.percentile(arr, 98)
    if hi - lo < 1e-6:
        return np.zeros(arr.shape, dtype=np.uint8)
    arr = (arr - lo) * (255.0 / (hi - lo))
    arr = np.clip(arr, 0, 255)
    return arr.astype(np.uint8)


def _resolve_rgb_bands(src) -> tuple:
    """
    根据 rasterio colorinterp 元数据定位 R/G/B 波段索引 (1-based)。
    若元数据不含完整 RGB 颜色解释, 返回 None 让调用方兜底。
    """
    try:
        from rasterio.enums import ColorInterp
        ci = src.colorinterp
    except Exception:
        return None
    if ci is None:
        return None
    r_idx = g_idx = b_idx = None
    for i, interp in enumerate(ci, start=1):
        if interp == ColorInterp.red:
            r_idx = i
        elif interp == ColorInterp.green:
            g_idx = i
        elif interp == ColorInterp.blue:
            b_idx = i
    if r_idx is not None and g_idx is not None and b_idx is not None:
        return (r_idx, g_idx, b_idx)
    return None


def _convert_with_rasterio(path: Path) -> bytes:
    """
    用 rasterio 读遥感 TIFF → PNG bytes。
    - 优先根据 colorinterp 元数据定位 R/G/B 波段索引, 避免盲目取前 3 波段导致偏色
    - 无颜色解释元数据时兜底: 多波段取前 3 个做 RGB
    - 单波段: 灰度拉伸
    - 带 nodata 值时, nodata 像素置 0
    """
    import rasterio  # 延迟导入, 仅真正转码时加载

    with rasterio.open(str(path)) as src:
        count = src.count
        nodata = src.nodata

        # ★ 优先按颜色解释元数据选择 RGB 波段
        rgb_indices = _resolve_rgb_bands(src)

        if rgb_indices is not None:
            r_idx, g_idx, b_idx = rgb_indices
            r_band = src.read(r_idx)
            g_band = src.read(g_idx)
            b_band = src.read(b_idx)
            rgb = np.stack([
                _normalize_band(r_band),
                _normalize_band(g_band),
                _normalize_band(b_band),
            ], axis=-1)  # (H, W, 3)
        elif count >= 3:
            # 兜底: 无颜色解释元数据时取前 3 个波段做 RGB
            bands = [src.read(i + 1) for i in range(3)]
            rgb = np.stack([_normalize_band(b) for b in bands], axis=-1)
        else:
            # 单波段: 灰度 → 转 RGB 三通道相同
            b = src.read(1)
            g = _normalize_band(b)
            rgb = np.stack([g, g, g], axis=-1)

        # 处理 nodata: 把 nodata 像素置为黑色 (避免拉伸时被当作有效极值)
        if nodata is not None and count >= 1:
            mask = src.read(1) == nodata
            if mask.any():
                rgb[mask] = 0

    img = Image.fromarray(rgb, mode="RGB")
    from io import BytesIO
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _convert_with_pil(path: Path) -> bytes:
    """
    用 PIL 读普通 TIFF/其他图像 → PNG bytes。
    - PIL 对单页 8bit TIFF 兼容性最好; 多页/16bit 可能不准, 但作为 rasterio 之后的兜底足够
    - 转 RGB/P 模式以兼容 PNG 编码
    """
    from io import BytesIO
    with Image.open(str(path)) as img:
        if img.mode in ("RGBA", "LA"):
            rgb = img.convert("RGBA")
        elif img.mode == "P":
            rgb = img.convert("RGB")
        elif img.mode == "L":
            rgb = img.convert("RGB")
        else:
            rgb = img.convert("RGB")
        buf = BytesIO()
        rgb.save(buf, format="PNG")
        return buf.getvalue()


def render_image_to_png_bytes(path) -> Tuple[bytes, str]:
    """
    把任意影像文件转成浏览器可显示的 (bytes, mime)。

    入参:
        path: 文件路径 (str / Path)
    出参:
        (image_bytes, mime):
          - 浏览器原生格式 (png/jpg/...): 直接返回原 bytes + 对应 MIME (零开销)
          - TIFF/GeoTIFF 等: 转成 PNG bytes + "image/png"

    异常:
        - 文件不存在 → FileNotFoundError
        - 文件过大 (>200MB) 且需转码 → ValueError (调用方应落回原文件下载)
        - rasterio + PIL 都失败 → ValueError (调用方应落回原文件下载)
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"影像文件不存在: {path}")

    ext = p.suffix.lstrip(".").lower()

    # 1) 浏览器原生格式: 零开销直读
    if ext in _BROWSER_NATIVE_EXTS:
        return p.read_bytes(), _NATIVE_MIME.get(ext, "application/octet-stream")

    # 2) 非原生格式: 走转码
    file_size = p.stat().st_size
    if file_size > _MAX_CONVERT_BYTES:
        raise ValueError(
            f"影像过大 ({file_size // 1024 // 1024}MB > {_MAX_CONVERT_BYTES // 1024 // 1024}MB), 拒绝转码: {p.name}"
        )

    # 2.1) 内存缓存命中检查 (按 path + mtime)
    cache_key = (str(p.resolve()), p.stat().st_mtime_ns)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    # 2.2) rasterio 优先 (遥感 TIFF), 失败退化 PIL
    png_bytes = None
    last_err = None
    if ext in _CONVERTIBLE_EXTS or ext not in _BROWSER_NATIVE_EXTS:
        try:
            png_bytes = _convert_with_rasterio(p)
        except Exception as e:
            last_err = e
            logger.info(f"[image_preview] rasterio 解码失败, 退化 PIL: {p.name} ({e})")

    if png_bytes is None:
        try:
            png_bytes = _convert_with_pil(p)
        except Exception as e:
            last_err = e
            logger.warning(f"[image_preview] PIL 解码也失败: {p.name} ({e})")

    if png_bytes is None:
        raise ValueError(
            f"无法解码影像 {p.name}: rasterio 与 PIL 均失败"
            + (f" (最后错误: {last_err})" if last_err else "")
        )

    result = (png_bytes, "image/png")
    _cache_put(cache_key, result)
    return result


def is_browser_native(path) -> bool:
    """判断文件后缀是否为浏览器 <img> 原生支持的格式 (无需后端转码)。"""
    ext = Path(path).suffix.lstrip(".").lower()
    return ext in _BROWSER_NATIVE_EXTS
