"""
工具箱 (迁移期 / 兼容层)

★ Backend V2: 工具实现与注册均已迁到 backend.agent.tools。
  本包只做兼容转发，保证旧的 `import backend.model.tools` 仍然触发注册；
  新代码请直接使用 `backend.agent.tools`。

  旧的子模块路径（data_tools / samseg_tools / report_tools ...）保留同名 shim 文件，
  直接 `from backend.model.tools.xxx import yyy` 仍然可用。
"""

from backend.agent.tools import *  # noqa: F401,F403
