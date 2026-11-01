"""ProC-SAM3 vendored 代码回归测试（不加载权重）。

运行: D:\\anaconda3\\envs\\sam3\\python.exe tests\\proc_sam3_vendor_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_vendored_proc_sam3() -> None:
    from backend.config import settings
    from backend.model.ProC_SAM3 import engine, proc_sam3_available

    vendor = (ROOT / "backend" / "model" / "ProC_SAM3" / "vendor").resolve()
    assert Path(settings.proc_sam3_root).resolve() == vendor
    assert (vendor / "sam3_segmentor_cached.py").is_file()
    assert (vendor / "configs" / "cfg_vaihingen.py").is_file()

    if not proc_sam3_available():
        print("proc_sam3_vendor: SKIP (weights/torch unavailable)")
        return

    engine._ensure_paths()
    import mmcv  # noqa: F401
    import mmengine  # noqa: F401
    import mmseg  # noqa: F401
    import sam3  # noqa: F401
    from mmengine.config import Config
    from sam3_segmentor_cached import CachedSAM3OpenSegmentor

    for module in (mmcv, mmengine, mmseg, sam3):
        assert str(vendor) in str(Path(module.__file__).resolve()), module.__name__
    assert CachedSAM3OpenSegmentor.__name__ == "CachedSAM3OpenSegmentor"
    cfg = Config.fromfile(str(vendor / "configs" / "cfg_vaihingen.py"))
    assert "model" in cfg


if __name__ == "__main__":
    test_vendored_proc_sam3()
    print("proc_sam3_vendor: PASS")
