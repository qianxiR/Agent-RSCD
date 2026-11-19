"""双时相影像色调（辐射）一致性：把 T2 拉齐到 T1 的色调/亮度分布。

为什么需要：
  同一地区两期影像的成像时间、太阳高度角、大气与传感器增益不同，DN 值整体偏移。
  实测本数据 T1 峰值概率中位 0.76、T2 只有 0.41 —— 模型概率随亮度整体漂移，
  固定阈值会让暗的一期整幅掉成背景，凭空造出「变化」。

三种口径（都是「以 T1 为参考，改 T2」）：
  lab    CIELAB 均值/方差匹配（Reinhard 色彩迁移）：亮度 L 与色度 a/b 解耦，
         各自对齐一阶（均值）与二阶（标准差）统计量，视觉最自然，推荐默认。
  hist   逐通道直方图匹配（CDF 映射）：非参数，能校正非线性响应；
         但要求两期场景内容分布相近，变化很大时会「把地物分布也搬过去」。
  linear 逐通道线性回归（a*x+b，最小二乘，可只用伪不变像元 PIF 拟合）：
         物理意义最接近相对辐射归一化，只改增益/偏置，不改分布形状。

注意：三者都只做全局统计对齐。若影像内部光照不均（云影、地形阴影），
    需要分块匹配（按瓦片各自求统计量），本模块留了 `blocks` 参数做这件事。
"""

from __future__ import annotations

import numpy as np

__all__ = ["harmonize", "lab_moment_match", "lab_lum_match", "histogram_match", "linear_match",
           "MODE_HELP"]

MODE_HELP = "色调一致性口径：none 不做；lab CIELAB 三通道匹配；lum 只匹配亮度（默认）；" \
            "hist 逐通道直方图匹配；linear 逐通道线性回归"


def _rgb2lab(rgb8: np.ndarray) -> np.ndarray:
    """入参: uint8 RGB (H,W,3)。方法: OpenCV 转 CIE Lab（L 0..255, a/b 0..255 偏移量）。出参: float32。"""
    import cv2

    return cv2.cvtColor(rgb8, cv2.COLOR_RGB2LAB).astype(np.float32)


def _lab2rgb(lab: np.ndarray) -> np.ndarray:
    """入参: float32 Lab。方法: 裁剪到合法域后转回 uint8 RGB。出参: uint8 (H,W,3)。"""
    import cv2

    lab = np.clip(lab, 0, 255).astype(np.uint8)
    return cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)


def lab_moment_match(src8: np.ndarray, ref8: np.ndarray,
                     mask: np.ndarray | None = None) -> np.ndarray:
    """
    入参: 待改的 src(uint8 RGB)、参考 ref(uint8 RGB)、可选 mask（只在 mask 内统计）。
    方法: 转 CIELAB → 逐通道 x' = (x-μs)/σs*σr+μr → 转回 RGB。
    出参: return (uint8 ndarray) 色调对齐后的 src。
    """
    sl, rl = _rgb2lab(src8), _rgb2lab(ref8)
    out = sl.copy()
    for ch in range(3):
        s, r = sl[..., ch], rl[..., ch]
        if mask is not None:
            sv, rv = s[mask], r[mask]
        else:
            sv, rv = s.ravel(), r.ravel()
        ms, ss = float(sv.mean()), float(sv.std()) or 1.0
        mr, sr = float(rv.mean()), float(rv.std()) or 1.0
        out[..., ch] = (s - ms) / ss * sr + mr
    return _lab2rgb(out)


def histogram_match(src8: np.ndarray, ref8: np.ndarray,
                    mask: np.ndarray | None = None) -> np.ndarray:
    """
    入参: 待改 src、参考 ref（uint8 RGB）、可选 mask。
    方法: 逐通道在 256 个分位点上建立 src→ref 的查表（分位点单调化后线性插值），
          等价于把 src 的 CDF 映射到 ref 的 CDF。
    出参: return (uint8 ndarray)。
    """
    q = np.linspace(0.0, 1.0, 256)
    out = np.empty_like(src8)
    for ch in range(3):
        s_vals = (src8[..., ch][mask] if mask is not None else src8[..., ch]).astype(np.float32)
        r_vals = (ref8[..., ch][mask] if mask is not None else ref8[..., ch]).astype(np.float32)
        xs = np.maximum.accumulate(np.quantile(s_vals.ravel(), q))
        xr = np.quantile(r_vals.ravel(), q)
        out[..., ch] = np.interp(src8[..., ch], xs, xr).round().astype(np.uint8)
    return out


def linear_match(src8: np.ndarray, ref8: np.ndarray,
                 mask: np.ndarray | None = None,
                 pif: bool = True) -> np.ndarray:
    """
    入参: 待改 src、参考 ref（uint8 RGB）、可选 mask、pif（是否只用伪不变像元拟合）。
    方法: 逐通道最小二乘拟合 ref ≈ a*src+b；pif=True 时先按 3σ 剔一次外点
          （近似伪不变像元）。若拟合后标准差偏离参考图超过 25%（外点剔除过头），
          退回「矩匹配」的增益 a=σr/σs、偏置 b=μr-a*μs，保证分布宽度不被压扁。
    出参: return (uint8 ndarray)。
    """
    out = np.empty_like(src8)
    for ch in range(3):
        s = src8[..., ch].astype(np.float32)
        r = ref8[..., ch].astype(np.float32)
        keep = np.ones(s.shape, bool) if mask is None else mask.copy()
        a, b = np.polyfit(s[keep], r[keep], 1)
        if pif:
            resid = np.abs(r - (a * s + b))
            tau = 3.0 * (float(resid[keep].std()) or 1e9)
            nk = resid <= tau if mask is None else (resid <= tau) & mask
            if nk.sum() >= max(64, 0.05 * s.size):
                a, b = np.polyfit(s[nk], r[nk], 1)
        pred_std = float(np.std(a * s + b))
        ref_std = float(r[keep].std()) or 1.0
        if abs(pred_std - ref_std) > 0.25 * ref_std:
            sr = float(s[keep].std()) or 1.0
            a = ref_std / sr
            b = float(r[keep].mean()) - a * float(s[keep].mean())
        out[..., ch] = np.clip(a * s + b, 0, 255).astype(np.uint8)
    return out


_MATCHERS = {"lab": lab_moment_match, "hist": histogram_match, "linear": linear_match,
             "lum": None}  # lum 在下方定义后回填


def lab_lum_match(src8: np.ndarray, ref8: np.ndarray,
                  mask: np.ndarray | None = None) -> np.ndarray:
    """
    入参: 待改 src、参考 ref（uint8 RGB）、可选 mask。
    方法: 只匹配 CIELAB 的亮度通道 L（均值+标准差），色度 a/b 原样保留。
          亮度是概率漂移的主因，而色相/饱和度里带着「水=蓝、植被=绿」这类判别线索，
          不动它们可以避免把地的颜色改歪后误分类。
    出参: return (uint8 ndarray)。
    """
    sl, rl = _rgb2lab(src8), _rgb2lab(ref8)
    out = sl.copy()
    s, r = sl[..., 0], rl[..., 0]
    if mask is not None:
        sv, rv = s[mask], r[mask]
    else:
        sv, rv = s.ravel(), r.ravel()
    ms, ss = float(sv.mean()), float(sv.std()) or 1.0
    mr, sr = float(rv.mean()), float(rv.std()) or 1.0
    out[..., 0] = (s - ms) / ss * sr + mr
    return _lab2rgb(out)


_MATCHERS["lum"] = lab_lum_match


def harmonize(t2: np.ndarray, t1: np.ndarray, mode: str = "lab",
              blocks: int = 0) -> np.ndarray:
    """
    入参: t2/t1 uint8 RGB (H,W,3)（t1 为参考）、模式名、blocks>0 时按网络分块各自匹配。
    方法: 按 mode 选匹配器；分块模式对每个瓦片用该瓦片自身的统计量匹配
          参考图的同一瓦片，消除云影/地形造成的空间不均光照。
    出参: return (uint8 ndarray) 与 t2 同尺寸的对齐后影像。
    """
    if mode not in _MATCHERS:
        raise ValueError(f"未知的色调一致性口径: {mode}（可选 {sorted(_MATCHERS)}）")
    fn = _MATCHERS[mode]
    if not blocks or blocks < 2:
        return fn(t2, t1)

    h, w = t2.shape[:2]
    bh, bw = int(np.ceil(h / blocks)), int(np.ceil(w / blocks))
    out = t2.copy()
    for y in range(0, h, bh):
        for x in range(0, w, bw):
            ys, xs = slice(y, min(y + bh, h)), slice(x, min(x + bw, w))
            out[ys, xs] = fn(t2[ys, xs], t1[ys, xs])
    return out
