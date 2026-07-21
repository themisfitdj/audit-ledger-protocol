"""Long call P&L at expiry — deterministic three-class math (BRK-N call-side).

A long call is the bullish mirror of the long put: the buyer pays a premium
and profits as spot rises above the strike. Max loss is the full premium; the
premium IS the buying power at risk (no margin beyond it), so
pnl_pct_bp = pnl / premium.

Three discrete outcome classes (mirror of long_put, sign-flipped on spot):

    spot <= strike                     → full_loss (OTM, full premium burned)
    intrinsic > premium_paid * 100     → full_win  (ITM enough to net positive)
    otherwise (0 < intrinsic <= prem)  → partial   (ITM but STILL losing money)

IMPORTANT — ``partial`` on a long call is ALWAYS a net loss: the call expired
in the money (intrinsic > 0) but below the premium paid, so the position still
loses money. Treat the outcome_class as a zone label, not a win/loss flag.

Edge case: premium_paid <= 0 cannot be sized or divided → returns
outcome_class="data_unavailable" with pnl_pct_bp=None (mirrors the
data_unavailable convention used by the replay pipeline).

Returns dict with pnl_dollars, pnl_pct_bp, outcome_class.
"""
from __future__ import annotations


def long_call_pnl_at_expiry(
    expiry_spot: float,
    strike: float,
    premium_paid: float,
) -> dict:
    """Return P&L for a single long call held to expiry."""
    if premium_paid <= 0:
        return {
            "pnl_dollars": None,
            "pnl_pct_bp": None,
            "outcome_class": "data_unavailable",
        }

    intrinsic_at_expiry = max(0.0, expiry_spot - strike) * 100.0
    premium_dollars = premium_paid * 100.0
    pnl_dollars = intrinsic_at_expiry - premium_dollars
    bp = premium_dollars  # premium IS the BP for a long call

    if expiry_spot <= strike:
        outcome_class = "full_loss"   # OTM at expiry, full premium burned
    elif intrinsic_at_expiry > premium_dollars:
        outcome_class = "full_win"    # ITM enough to net positive
    else:
        outcome_class = "partial"     # ITM but below breakeven (still losing)

    return {
        "pnl_dollars": pnl_dollars,
        "pnl_pct_bp": pnl_dollars / bp,
        "outcome_class": outcome_class,
    }
