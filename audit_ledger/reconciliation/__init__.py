"""Broker-truth reconciliation engine.

Replaces markdown-based realized-stream matching with a broker-anchored
chain. Classifies every recommendation + every broker fill into one of 11
explicit MatchStatus values, computes realized P&L from broker transaction
net_value (multiplier-agnostic by construction), decomposes the period NLV
delta into six attributable lines.

Three modules:
- schema: MatchStatus / Origin / Structure enums; ReconciledTrade,
  ReconciledTradeLeg, RejectedRecommendation, PeriodReconciliation,
  ReconciliationResult dataclasses
- engine: reconcile() — the broker-agnostic engine
- tie_out: period_tie_out() — Δ(NLV) decomposition with discrepancy detection

Tolerance for period tie-out: max($1, 0.05% × NLV_end). Anything outside
fires a `discrepancy` exception, never silently absorbed.
"""
from audit_ledger.reconciliation.schema import (  # noqa: F401
    DiscrepancyError,
    MatchStatus,
    Origin,
    PeriodReconciliation,
    ReconciledTrade,
    ReconciledTradeLeg,
    ReconciliationResult,
    RejectedRecommendation,
    Structure,
)
from audit_ledger.reconciliation.engine import reconcile  # noqa: F401
from audit_ledger.reconciliation.tie_out import period_tie_out  # noqa: F401
