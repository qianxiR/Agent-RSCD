"""
工具箱 (Model 层 / Tools)
- tool_registry: 工具注册与调度系统 (@register_tool 装饰器 + 全局注册表)
- data_tools:    GeoServer 操作 + 数据库操作工具 + 元数据检索 (Phase A)
- layer_tools:   图层控制工具 (前端地图交互指令)
- analysis_tools: GeoServer 下载工具 (栅格图层下载为 GeoTIFF)
- skill_tools:   长任务技能检索工具 (按需查 skills 文档, 不注入 prompt)
- samseg_tools:  SamSeg 遥感分析工具 (语义分割 / 变化检测 + VLM 业务判读, 依赖 torch)
- sandbox_tools: 沙盒代码执行工具 (run_python_code / run_shell_command)
- sandbox_render_tools: 沙盒产物渲染工具 (render_sandbox_image, 沙盒图片→前端面板)
- preprocess_tools: 数据预处理 (金字塔/COG/拓扑检查, Phase C)
- report_tools:  报表生成 (统计图表/PDF Word 报告/矢量表格导出, Phase D)
- mask_tools:    掩膜分析 (连通域分析 + 几何特征提取, Phase F)
- vector_tools:  矢量可视化 (GeoJSON → PNG + Shapefile, 渲染到工作区)

★ 导入本包会触发所有 @register_tool 装饰器执行, 将工具注册到全局注册表。
  main.py 启动时只需 `import backend.model.tools` 即可完成全部工具注册。

★ samseg_tools 用 try/except 包裹: torch 未安装时跳过注册, 不影响其他工具。
  工具内部调用时仍会检测 runner.samseg_available() 做运行时降级。

依赖方向: model.tools → data (business_db / geoserver_client), 严禁反向依赖 agent 层。
"""
from backend.model.tools.data_tools import *
from backend.model.tools.layer_tools import *
from backend.model.tools.analysis_tools import *
from backend.model.tools.skill_tools import *

try:
    from backend.model.tools.samseg_tools import *  # noqa: F401,F403
except ImportError as _e:
    # SamSeg 依赖 (torch 等) 未安装时不阻塞其他工具加载
    import logging
    logging.getLogger(__name__).info(f"samseg_tools 未加载 (依赖缺失, 跳过): {_e}")

try:
    from backend.model.tools.sandbox_tools import *  # noqa: F401,F403
except ImportError as _e:
    import logging
    logging.getLogger(__name__).info(f"sandbox_tools 未加载 (依赖缺失, 跳过): {_e}")

try:
    from backend.model.tools.sandbox_render_tools import *  # noqa: F401,F403
except ImportError as _e:
    import logging
    logging.getLogger(__name__).info(f"sandbox_render_tools 未加载 (依赖缺失, 跳过): {_e}")

try:
    from backend.model.tools.sandbox_export_tools import *  # noqa: F401,F403
except ImportError as _e:
    import logging
    logging.getLogger(__name__).info(f"sandbox_export_tools 未加载 (依赖缺失, 跳过): {_e}")

try:
    from backend.model.tools.preprocess_tools import *  # noqa: F401,F403
except ImportError as _e:
    import logging
    logging.getLogger(__name__).info(f"preprocess_tools 未加载 (依赖缺失, 跳过): {_e}")

try:
    from backend.model.tools.report_tools import *  # noqa: F401,F403
except ImportError as _e:
    import logging
    logging.getLogger(__name__).info(f"report_tools 未加载 (依赖缺失, 跳过): {_e}")

try:
    from backend.model.tools.mask_tools import *  # noqa: F401,F403
except ImportError as _e:
    import logging
    logging.getLogger(__name__).info(f"mask_tools 未加载 (依赖缺失, 跳过): {_e}")

try:
    from backend.model.tools.vector_tools import *  # noqa: F401,F403
except ImportError as _e:
    import logging
    logging.getLogger(__name__).info(f"vector_tools 未加载 (依赖缺失, 跳过): {_e}")

