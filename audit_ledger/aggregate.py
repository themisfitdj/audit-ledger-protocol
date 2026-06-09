"""Bucketing primitives for outcome aggregation.

Strategy-agnostic; uses the SyntheticOutcome shape and a caller-supplied
`keys_fn` for the bucket dimension.
"""
from __future__ import annotations

from audit_ledger.schema import SyntheticOutcome


def decile_key(value: float) -> str:
    """Bucket a 0–1 metric (EV%, confidence, IV-rank-normalized, etc.) into
    a string decile key for aggregation."""
    if value < 0:
        return "negative"
    if value >= 1.0:
        return "1.0+"
    lo = int(value * 10) / 10.0
    return f"{lo:.1f}-{lo + 0.1:.1f}"


def aggregate_by(
    outcomes: list[SyntheticOutcome],
    keys_fn,
) -> dict[str, dict]:
    """Group outcomes by keys_fn → per-bucket count, mean P&L, outcome class counts.

    `keys_fn` returns a list of keys for each outcome, letting one outcome
    contribute to multiple buckets (used for risk_flags). Outcomes with
    None pnl_dollars must be filtered by the caller before passing in.
    """
    buckets: dict[str, list[SyntheticOutcome]] = {}
    for o in outcomes:
        for key in keys_fn(o):
            buckets.setdefault(key, []).append(o)

    result: dict[str, dict] = {}
    for key, items in buckets.items():
        count = len(items)
        oc_counts: dict[str, int] = {}
        for o in items:
            oc_counts[o.outcome_class] = oc_counts.get(o.outcome_class, 0) + 1
        result[key] = {
            "count": count,
            "mean_pnl_dollars": sum(o.pnl_dollars for o in items) / count,
            "mean_pnl_pct_bp": sum(o.pnl_pct_bp for o in items) / count,
            "outcome_class_counts": oc_counts,
        }
    return result


# Backwards-compatible alias for adapters migrating from brk-tasty's pre-extract
# naming. New adapters should use decile_key.
ev_pct_decile_key = decile_key
