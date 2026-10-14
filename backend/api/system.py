"""系统/调试路由 (/api/v1/debug)。"""
import json
import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request

router = APIRouter()
logger = logging.getLogger(__name__)

# 项目根目录 (供前端诊断日志落盘)
_ROOT_DIR = Path(__file__).resolve().parent.parent.parent


@router.post("/api/v1/debug/log")
async def debug_log(request: Request):
    """前端诊断日志 → 追加写入项目根目录 wms-debug.log（排查地图渲染，用完可删）"""
    try:
        body = await request.json()
        msg = str(body.get("msg", ""))
    except Exception:
        msg = ""
    with open(_ROOT_DIR / "wms-debug.log", "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
    return {"status": "ok"}
