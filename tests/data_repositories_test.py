"""数据域仓储回归测试（本地文件系统 + 进程内 catalog）。

运行: python tests\\data_repositories_test.py
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data.catalog import AssetCatalog  # noqa: E402
from backend.data.image import ImageRepository  # noqa: E402
from backend.data.other import OtherRepository  # noqa: E402
from backend.data.text import TextRepository  # noqa: E402
from backend.data.vector import VectorRepository  # noqa: E402


def test_repositories_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        catalog = AssetCatalog()
        repos = {
            "vector": VectorRepository(root / "vector", catalog),
            "image": ImageRepository(root / "image", catalog),
            "text": TextRepository(root / "text", catalog),
            "other": OtherRepository(root / "other", catalog),
        }

        refs = {}
        for kind, repo in repos.items():
            ref = repo.save(
                b"payload-" + kind.encode(),
                filename=f"a.{kind}",
                metadata={"kind": kind, "tag": "payload"},
                bbox=[103.0, 30.0, 104.0, 31.0],
                acquired_at="2026-01-01T00:00:00",
            )
            refs[kind] = ref
            assert Path(ref.uri).is_file()
            assert repo.open(ref).read() == b"payload-" + kind.encode()

        assert len(catalog.query(kind="vector")) == 1
        assert len(catalog.query(bbox=[103.2, 30.2, 103.8, 30.8])) == 4
        assert len(catalog.query(text="payload")) == 4

        try:
            repos["vector"].open(refs["image"])
        except ValueError:
            pass
        else:
            raise AssertionError("kind mismatch should raise")


if __name__ == "__main__":
    test_repositories_roundtrip()
    print("data_repositories: PASS")
