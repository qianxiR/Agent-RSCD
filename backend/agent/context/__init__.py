"""
Agent 上下文模块。

- builder: ContextBuilder，把上下文组装从记忆实现中拆出；
- context_priority: 上下文冲突裁决（待主流程接入）。
"""

from backend.agent.context.builder import ContextBuilder, get_context_builder

__all__ = ["ContextBuilder", "get_context_builder"]

