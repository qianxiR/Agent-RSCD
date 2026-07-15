"""
单 Agent 沙盒链路后端验证。

验证目标 (用户核心诉求): agent 在沙盒调用代码 -> 读取成果 -> 生成前端渲染指令。
链路三环:
  1. run_python_code: Docker 沙盒执行代码, 产物落宿主 work_dir (容器 /workspace 实体)
  2. render_sandbox_image: 校验产物存在 + 防越权 + 拼 image_url
  3. 返回 frontend_action(render_image), 前端据此 <img> 渲染
不依赖 LLM / WebSocket / 前端, 直接调工具函数 + 注入运行时上下文。

运行方式:
python tests\\sandbox_pipeline_test.py
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agent.runtime.context_vars import set_runtime_context
from backend.model.tools.sandbox_manager import sandbox_manager

CONV_ID = "sandbox-pipeline-verify-0001"
PROJECT_ID = "smoke"
FILENAME = "sandbox_pipeline_chart.png"


def _check(cond, label):
    """
    入参:
      - cond: 需要成立的断言条件。
      - label: 失败时的定位标签。
    方法:
      - 条件不成立立即抛 AssertionError, 让 traceback 暴露失败点。
    出参:
      - 无返回。
    """
    if not cond:
        raise AssertionError(label)


async def run():
    """
    入参: 无 (会话 id / 项目 id 用模块常量)。
    方法:
      - 注入运行时上下文, 让沙盒工具派生与本会话一致的 work_dir。
      - 环1: run_python_code 用 Pillow 在沙盒生成 PNG, 断言 stdout + 产物落盘。
      - 环2: render_sandbox_image 读回产物, 断言 frontend_action + image_url 指向产物。
      - 反例: 不存在的文件必须返回 error, 证明读取成果的健壮性。
    出参: 无 (任一环失败抛异常)。
    """
    set_runtime_context(CONV_ID, user_id="verify", project_id=PROJECT_ID)

    avail = await sandbox_manager.is_available()
    _check(avail, f"沙盒不可用: {sandbox_manager._last_unavailable_reason}")

    from backend.model.tools.sandbox_tools import run_python_code
    code = (
        "from PIL import Image, ImageDraw\n"
        "img = Image.new('RGB', (160, 100), (30, 60, 120))\n"
        "d = ImageDraw.Draw(img)\n"
        "d.rectangle([20, 20, 80, 80], fill=(255, 200, 0))\n"
        "d.text((24, 24), 'sandbox', fill=(0, 0, 0))\n"
        f"img.save('{FILENAME}')\n"
        "print('CHART_SAVED')\n"
    )
    r1 = await run_python_code.ainvoke({"code": code})
    _check(
        r1.get("type") == "success",
        f"环1 run_python_code 失败: type={r1.get('type')} msg={r1.get('msg')}",
    )
    stdout = (r1.get("data") or {}).get("stdout", "")
    _check("CHART_SAVED" in stdout, f"环1 stdout 未含 CHART_SAVED: {stdout!r}")
    work_dir = r1["data"]["host_workspace_path"]
    artifact = Path(work_dir) / FILENAME
    _check(artifact.is_file(), f"环1 产物未落盘: {artifact}")
    _check(artifact.stat().st_size > 0, "环1 产物为空文件")

    from backend.model.tools.sandbox_render_tools import render_sandbox_image
    r2 = render_sandbox_image.invoke({"filename": FILENAME})
    _check(
        r2.get("type") == "frontend_action",
        f"环2 类型异常: {r2.get('type')} msg={r2.get('msg')}",
    )
    _check(r2.get("action") == "chat_image", f"环2 action 异常: {r2.get('action')}")
    params = r2["instruction"]["params"]
    _check(
        params["image_url"].startswith("/api/v1/download/"),
        f"环2 image_url 异常: {params['image_url']}",
    )
    _check(FILENAME in params["image_url"], "环2 image_url 未指向产物")
    _check(
        r2["data"]["local_path"] == str(artifact.resolve()),
        "环2 local_path 与产物路径不一致",
    )

    r3 = render_sandbox_image.invoke({"filename": "not_exist_xyz.png"})
    _check(r3.get("type") == "error", "环2 反例: 缺失产物应返回 error")

    print("=== 沙盒链路验证通过 ===")
    print(f"work_dir  : {work_dir}")
    print(f"产物      : {artifact} ({artifact.stat().st_size} bytes)")
    print(f"image_url : {params['image_url']}")


if __name__ == "__main__":
    asyncio.run(run())
