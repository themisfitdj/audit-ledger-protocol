"""Bull put spread P&L at expiry — deterministic three-branch math.

Used by audit_ledger.outcomes.compute_synthetic_outcome to compute the
synthetic hold-to-expiry outcome for each bull-put-spread recommendation.
At-expiry only; at-entry EV math is the adapter's responsibility (typically
in the platform's own ranker/EV-gate code).

Three branches:
    spot >= short_strike → full_win  → pnl = +net_credit * 100
    spot <= long_strike  → full_loss → pnl = -(width - net_credit) * 100
    otherwise            → partial   → pnl = (net_credit - (short_strike - spot)) * 100

Returns dict with pnl_dollars, pnl_pct_bp, outcome_class.
"""
from __future__ import annotations


def spread_pnl_at_expiry(
    expiry_spot: float,
    short_strike: float,
    long_strike: float,
    net_credit: float,
) -> dict:
    """Return P&L for a bull put spread held to expiry."""
    spread_width = short_strike - long_strike
    bp_required = (spread_width - net_credit) * 100.0

    if expiry_spot >= short_strike:
        pnl_dollars = net_credit * 100.0
        outcome_class = "full_win"
    elif expiry_spot <= long_strike:
        pnl_dollars = -(spread_width - net_credit) * 100.0
        outcome_class = "full_loss"
    else:
        pnl_dollars = (net_credit - (short_strike - expiry_spot)) * 100.0
        outcome_class = "partial"

    return {
        "pnl_dollars": pnl_dollars,
        "pnl_pct_bp": pnl_dollars / bp_required,
        "outcome_class": outcome_class,
    }
