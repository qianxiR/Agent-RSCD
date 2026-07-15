"""
沙盒功能端到端测试 (独立脚本, 直接调 sandbox_manager + sandbox_tools)

5 个测试:
  1. is_available 检测 (Docker + 镜像)
  2. matplotlib 画图 (验证 bind mount 产物回传)
  3. rasterio 读 GeoTIFF + NDVI 计算 (验证遥感数据科学能力)
  4. rio-cogeo COG 转换 (验证新加的 P5 包)
  5. 容器复用 + 会话隔离 (验证 (client_id, work_dir) 哈希隔离)

运行:
  conda activate sam3
  python tests/sandbox_tests.py
"""
import sys
import os
import asyncio
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

T1 = str(ROOT / "tests" / "data" / "test_t1.tif")


def section(name):
    print("\n" + "=" * 60)
    print(f"  {name}")
    print("=" * 60)


def check(cond, msg):
    sym = "✓" if cond else "✗"
    print(f"  [{sym}] {msg}")
    return cond


async def main():
    overall_ok = True
    from backend.model.tools.sandbox_manager import sandbox_manager
    from backend.model.tools.sandbox_tools import _get_client_and_work_dir

    # ==================== 测试 1: 可用性检测 ====================
    section("测试 1: 沙盒可用性检测 (is_available)")
    t0 = time.time()
    avail = await sandbox_manager.is_available()
    elapsed = time.time() - t0
    overall_ok &= check(avail, f"is_available() = {avail} ({elapsed:.2f}s)")
    if avail:
        check(True, f"沙盒镜像: agent-sandbox:latest")
        check(True, f"工作区根: {sandbox_manager.resolve_work_dir('test_proj', 'test_conv')}")
    else:
        print(f"  失败原因: {sandbox_manager._last_unavailable_reason}")
        print("  ⚠ 后续测试将全部跳过")
        return 1

    # ==================== 测试 2: matplotlib 画图 ====================
    section("测试 2: matplotlib 画图 (验证 bind mount 产物回传)")
    client_id, work_dir = "test_conv_chart", sandbox_manager.resolve_work_dir("test_proj", "test_conv_chart")
    code = """
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
plt.rcParams['font.sans-serif'] = ['Noto Sans CJK SC', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False
x = np.linspace(0, 10, 100)
fig, ax = plt.subplots(figsize=(8, 5))
ax.plot(x, np.sin(x), label='正弦', linewidth=2)
ax.plot(x, np.cos(x), label='余弦', linewidth=2)
ax.set_title('沙盒测试 - 中文标题')
ax.set_xlabel('X 轴')
ax.set_ylabel('Y 轴')
ax.legend()
ax.grid(True, alpha=0.3)
plt.savefig('sandbox_chart_test.png', dpi=100, bbox_inches='tight')
print('CHART_SAVED:', 'sandbox_chart_test.png')
print('numpy version:', np.__version__)
"""
    t0 = time.time()
    result = await sandbox_manager.exec_python(client_id, work_dir, code)
    elapsed = time.time() - t0
    rc = result.get("returncode")
    stdout = result.get("stdout", "")
    chart_path = Path(work_dir) / "sandbox_chart_test.png"
    has_chart = chart_path.is_file()
    has_signal = "CHART_SAVED" in stdout
    overall_ok &= check(rc == 0, f"exec returncode={rc} ({elapsed:.1f}s)")
    overall_ok &= check(has_signal, f"stdout 含 CHART_SAVED 信号")
    overall_ok &= check(has_chart, f"bind mount 回传: {chart_path.name} ({chart_path.stat().st_size//1024 if has_chart else 0}KB)")
    if not has_signal:
        print(f"  stdout: {stdout[:300]}")
        print(f"  stderr: {result.get('stderr', '')[:300]}")

    # ==================== 测试 3: rasterio 读 GeoTIFF + NDVI ====================
    section("测试 3: rasterio 读 GeoTIFF + NDVI 计算")
    # 先把 GeoTIFF 搬进沙盒 work_dir
    import shutil
    client_id3, work_dir3 = "test_conv_ndvi", sandbox_manager.resolve_work_dir("test_proj", "test_conv_ndvi")
    Path(work_dir3).mkdir(parents=True, exist_ok=True)
    src_in_ws = Path(work_dir3) / "test_t1.tif"
    shutil.copy2(T1, src_in_ws)

    code3 = """
import rasterio
import numpy as np
with rasterio.open('/workspace/test_t1.tif') as src:
    print('shape:', src.width, 'x', src.height, 'x', src.count)
    print('dtype:', src.dtypes[0])
    print('crs:', src.crs)
    print('bounds:', src.bounds)
    data = src.read()
    print('data shape:', data.shape, 'min/max:', float(data.min()), float(data.max()))
    # 模拟 NDVI 计算 (近红外 - 红) / (近红外 + 红)
    # 这里只有 3 波段 (RGB), 用 band1=red, band2=green, band3=blue 近似
    red = data[0].astype('float32')
    nir_proxy = data[2].astype('float32')  # 用蓝波段近似
    denom = nir_proxy + red + 1e-6
    ndvi = (nir_proxy - red) / denom
    print('NDVI mean:', float(np.nanmean(ndvi)), 'std:', float(np.nanstd(ndvi)))
    print('NDVI range:', float(np.nanmin(ndvi)), '~', float(np.nanmax(ndvi)))
print('RASTERIO_OK')
"""
    t0 = time.time()
    result3 = await sandbox_manager.exec_python(client_id3, work_dir3, code3)
    elapsed = time.time() - t0
    rc3 = result3.get("returncode")
    stdout3 = result3.get("stdout", "")
    overall_ok &= check(rc3 == 0, f"rasterio 执行 ({elapsed:.1f}s)")
    overall_ok &= check("RASTERIO_OK" in stdout3, "NDVI 计算完成")
    if rc3 != 0:
        print(f"  stderr: {result3.get('stderr', '')[:400]}")
    else:
        # 打印关键输出
        for line in stdout3.split("\n"):
            if line.strip():
                print(f"    {line}")

    # ==================== 测试 4: COG 转换 ====================
    section("测试 4: rio-cogeo COG 转换 (新加的 P5 包)")
    client_id4, work_dir4 = "test_conv_cog", sandbox_manager.resolve_work_dir("test_proj", "test_conv_cog")
    Path(work_dir4).mkdir(parents=True, exist_ok=True)
    src_in_ws4 = Path(work_dir4) / "test_t1.tif"
    shutil.copy2(T1, src_in_ws4)

    code4 = """
from rio_cogeo.cogeo import cog_translate
from rio_cogeo.profiles import cog_profiles
import os
src = '/workspace/test_t1.tif'
dst = '/workspace/test_t1_cog.tif'
profile = cog_profiles.get('deflate')
cog_translate(src, dst, profile, in_memory=False)
print('COG_SIZE:', os.path.getsize(dst))
# validate
from rio_cogeo.cogeo import cog_validate
is_valid, errors, warnings = cog_validate(dst)
print('COG_VALID:', is_valid)
if errors:
    print('COG_ERRORS:', errors)
print('COG_DONE')
"""
    t0 = time.time()
    result4 = await sandbox_manager.exec_python(client_id4, work_dir4, code4, timeout=120)
    elapsed = time.time() - t0
    rc4 = result4.get("returncode")
    stdout4 = result4.get("stdout", "")
    cog_path = Path(work_dir4) / "test_t1_cog.tif"
    overall_ok &= check(rc4 == 0, f"cog_translate 执行 ({elapsed:.1f}s)")
    overall_ok &= check("COG_DONE" in stdout4, "COG 转换完成")
    overall_ok &= check(cog_path.is_file(), f"COG 产物回传: {cog_path.name} ({cog_path.stat().st_size//1024 if cog_path.is_file() else 0}KB)")
    if "COG_VALID:" in stdout4:
        valid_line = [l for l in stdout4.split("\n") if "COG_VALID" in l]
        if valid_line:
            overall_ok &= check("True" in valid_line[0], f"COG 格式合规: {valid_line[0].strip()}")
    if rc4 != 0:
        print(f"  stderr: {result4.get('stderr', '')[:500]}")

    # ==================== 测试 5: 容器复用 + 会话隔离 ====================
    section("测试 5: 容器复用 + 会话隔离 (验证 (client_id, work_dir) 哈希)")
    # 同一会话调两次, 应该复用容器
    cid_a = "isolation_conv_A"
    wd_a = sandbox_manager.resolve_work_dir("iso_proj", cid_a)
    cid_b = "isolation_conv_B"
    wd_b = sandbox_manager.resolve_work_dir("iso_proj", cid_b)

    # A 会话写文件
    await sandbox_manager.exec_python(cid_a, wd_a, "open('/workspace/flag_A.txt','w').write('hello from A')\nprint('A_WROTE')")
    # B 会话写文件
    await sandbox_manager.exec_python(cid_b, wd_b, "open('/workspace/flag_B.txt','w').write('hello from B')\nprint('B_WROTE')")

    # A 会话检查: 应能看到 flag_A, 看不到 flag_B
    res_a = await sandbox_manager.exec_python(cid_a, wd_a,
        "import os; files = sorted(os.listdir('/workspace')); print('A_FILES:', files)")
    # B 会话检查
    res_b = await sandbox_manager.exec_python(cid_b, wd_b,
        "import os; files = sorted(os.listdir('/workspace')); print('B_FILES:', files)")

    a_files = [l for l in res_a.get("stdout", "").split("\n") if "A_FILES:" in l]
    b_files = [l for l in res_b.get("stdout", "").split("\n") if "B_FILES:" in l]
    print(f"  A 会话文件: {a_files[0] if a_files else '(无输出)'}")
    print(f"  B 会话文件: {b_files[0] if b_files else '(无输出)'}")

    a_has_a = a_files and "flag_A" in a_files[0]
    a_no_b = a_files and "flag_B" not in a_files[0]
    b_has_b = b_files and "flag_B" in b_files[0]
    b_no_a = b_files and "flag_A" not in b_files[0]
    overall_ok &= check(a_has_a and a_no_b, "A 会话只看见自己的文件 (隔离成功)")
    overall_ok &= check(b_has_b and b_no_a, "B 会话只看见自己的文件 (隔离成功)")

    # 容器复用: 看 _containers 里有两个 key
    container_count = len(sandbox_manager._containers)
    overall_ok &= check(container_count >= 2, f"沙盒容器数: {container_count} (至少 2 个隔离会话)")

    # ==================== 总结 ====================
    section("总结")
    sym = "✓ 全部通过" if overall_ok else "✗ 有失败项"
    print(f"  结果: {sym}")
    print(f"  沙盒容器复用数: {len(sandbox_manager._containers)}")
    # 清理
    print("\n  清理沙盒容器...")
    await sandbox_manager.cleanup_all()
    print("  清理完成")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    rc = asyncio.run(main())
    sys.exit(rc)
