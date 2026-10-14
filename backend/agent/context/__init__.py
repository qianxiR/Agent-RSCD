"""
Agent 上下文裁决模块。

★ 状态说明: resolve_context_conflict 实现为纯函数且有测试覆盖
  (tests/agent_guardrail_eval.py G1/G2), 但尚未接入主流程 ——
  validation_stage.build_repair_execution_card 目前用硬编码 latest_evidence_overrides
  做"最新证据压过旧上下文", 未调用本模块。
  保留此模块待主流程接入 (需把硬编码裁决替换为对 resolve_context_conflict 的调用)。
"""
