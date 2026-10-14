"""
路由层请求体模型 (集中定义, 原 main.py 内散落的 Pydantic 模型)。

各 router 从此文件 import 自己需要的模型, 保持请求 schema 单一来源。
"""
from typing import Optional

from pydantic import BaseModel


# ==================== 会话管理 ====================

class UpdateConversationRequest(BaseModel):
    title: str = None
    project_id: str = None  # 空字符串表示移出项目


class UpdateConversationTraceRequest(BaseModel):
    eventLogs: list[dict] = []
    promptLogs: list[dict] = []
    wsLogs: list[dict] = []


# ==================== v2.5 消息分支 (Git-like DAG) ====================

class ForkRequest(BaseModel):
    """从指定消息节点分叉, 开新分支"""
    parent_node_id: str  # 分叉点: 新分支挂在这条消息之后


class RegenerateRequest(BaseModel):
    """
    入参: prompt 必填；parent_node_id 与 replace_node_id 按分支模式二选一。
    方法: 校验重新生成或编辑替换请求的结构化参数。
    出参: FastAPI regenerate 路由使用的请求模型。
    """
    parent_node_id: Optional[str] = None   # 分叉点 (从哪条消息之后重新开始)
    replace_node_id: Optional[str] = None  # 编辑模式: 替换该用户消息及其后续分支
    prompt: str           # 新的 prompt (编辑后的用户输入)
    model: Optional[str] = None  # 可选, 指定模型 (默认用 settings.llm_model)


# ==================== GeoServer 图层管理 ====================

class PublishLayerRequest(BaseModel):
    """前端发布图层请求 (图层面板"上传图层"按钮)。
    file_path: 已上传到服务器的文件绝对路径 (先经 /api/v1/files/upload 获得)
    layer_name: 发布后的图层名, 留空则用文件名
    workspace: 已废弃 — 前端无传参入口, 后端按图层类型固定工作空间 (保留字段仅向后兼容)
    layer_type: 强制指定类型 "raster"/"vector"; 留空则按扩展名推断
    """
    file_path: str
    layer_name: str = ""
    workspace: str = ""
    layer_type: str = ""


# ==================== 沙盒文件导出 ====================

class SandboxDownloadRequest(BaseModel):
    """前端卡片下载按钮请求体。"""
    file_path: str   # 沙盒内路径或 agent-files 相对路径
    dest_dir: str    # 宿主机目标文件夹绝对路径 (任意盘符)
