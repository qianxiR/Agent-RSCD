"""
工具模块 (Agent 层) — 注册表 + 全部工具实现

★ Backend V2: 工具注册表与工具实现均已迁入本目录；
  model/tools 仅保留兼容转发，不再承载注册逻辑。

  导入本包会触发所有 @register_tool 装饰器执行, 完成全局注册。
"""
from backend.agent.tools.registry import (  # noqa: F401
    get_all_tools,
    get_llm_exposed_tool_names,
    get_tool_by_name,
    get_tool_category_map,
    get_tools_catalog_for_llm,
    get_tools_for_llm,
    register_tool,
)

# 基础工具（顺序尽量保持依赖在前）
from backend.agent.tools.data_tools import *
from backend.agent.tools.analysis_tools import *
from backend.agent.tools.layer_tools import *
from backend.agent.tools.skill_tools import *
from backend.agent.tools.mask_tools import *
from backend.agent.tools.preprocess_tools import *
from backend.agent.tools.report_tools import *
from backend.agent.tools.vector_tools import *
from backend.agent.tools.image_preview import *

# 沙盒工具（依赖 sandbox_manager；Docker 不可用时仍可导入）
from backend.agent.tools.sandbox_tools import *
from backend.agent.tools.sandbox_render_tools import *
from backend.agent.tools.sandbox_export_tools import *

# 遥感工具依赖 torch；缺失时跳过，不影响其他工具
try:
    from backend.agent.tools.samseg_tools import *  # noqa: F401,F403
except ImportError as _e:
    import logging
    logging.getLogger(__name__).info(f"samseg_tools 未加载 (依赖缺失, 跳过): {_e}")

# Agent 自身工具
from backend.agent.tools.memory_tools import *
from backend.agent.tools.team_tools import *
