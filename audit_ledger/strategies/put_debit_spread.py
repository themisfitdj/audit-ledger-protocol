"""Put debit spread P&L at expiry — deterministic three-branch math (BRK-126).

Bearish debit structure. Buy the higher-strike put (bought_strike, the long
leg paid for), sell the lower-strike put (sold_strike). Net debit paid. By
construction bought_strike > sold_strike. The debit paid is the buying power
at risk (max loss = net_debit * 100).

Three branches:
    spot <= sold_strike   → full_win  → pnl = +(width - net_debit) * 100
    spot >= bought_strike → full_loss → pnl = -net_debit * 100 (debit burned)
    otherwise             → partial   → pnl = (bought - spot)*100 - max_loss

The partial formula is continuous with both endpoints: at spot == sold_strike
it equals max_profit, and at spot == bought_strike it equals -max_loss.

Edge case: net_debit <= 0 is not a valid debit spread → returns
outcome_class="data_unavailable" with pnl_pct_bp=None.

Returns dict with pnl_dollars, pnl_pct_bp, outcome_class.
"""
from __future__ import annotations


def put_debit_spread_pnl_at_expiry(
    expiry_spot: float,
    bought_strike: float,
    sold_strike: float,
    net_debit: float,
) -> dict:
    """Return P&L for a put debit spread held to expiry."""
    if net_debit <= 0:
        return {
            "pnl_dollars": None,
            "pnl_pct_bp": None,
            "outcome_class": "data_unavailable",
        }

    spread_width = bought_strike - sold_strike
    max_profit_dollars = (spread_width - net_debit) * 100.0
    max_loss_dollars = net_debit * 100.0

    if expiry_spot <= sold_strike:
        pnl_dollars = max_profit_dollars
        outcome_class = "full_win"
    elif expiry_spot >= bought_strike:
        pnl_dollars = -max_loss_dollars
        outcome_class = "full_loss"
    else:
        pnl_dollars = ((bought_strike - expiry_spot) * 100.0) - max_loss_dollars
        outcome_class = "partial"

    return {
        "pnl_dollars": pnl_dollars,
        "pnl_pct_bp": pnl_dollars / max_loss_dollars,
        "outcome_class": outcome_class,
    }
