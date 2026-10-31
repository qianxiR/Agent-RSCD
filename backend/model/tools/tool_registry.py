# -*- coding: utf-8 -*-
"""兼容转发：工具注册表已迁移到 backend.agent.tools.registry。

旧导入路径暂时保留，避免一次性修改全部工具/测试；
迁移完成后删除本文件。
"""

from backend.agent.tools.registry import *  # noqa: F401,F403
