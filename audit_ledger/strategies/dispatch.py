"""Structure-keyed dispatch to the per-structure at-expiry P&L computers.

``outcome_for_structure`` routes a ledger entry's ``structure_type`` to the
right at-expiry P&L function in ``audit_ledger.strategies``. This keeps the
methodology package self-contained: a caller passes the structure discriminator
plus that structure's parameters and receives the standard outcome dict
(pnl_dollars, pnl_pct_bp, outcome_class).

Backward compat: a ``structure_type`` of "bull_put_spread", the empty string,
or None all route to the bull-put computer — the original single-structure
ledger shape, before the discriminator existed.
"""
from __future__ import annotations

from audit_ledger.strategies.bear_call_spread import bear_call_spread_pnl_at_expiry
from audit_ledger.strategies.bull_put_spread import spread_pnl_at_expiry
from audit_ledger.strategies.call_debit_spread import call_debit_spread_pnl_at_expiry
from audit_ledger.strategies.long_call import long_call_pnl_at_expiry
from audit_ledger.strategies.long_put import long_put_pnl_at_expiry
from audit_ledger.strategies.put_debit_spread import put_debit_spread_pnl_at_expiry


def outcome_for_structure(
    structure_type: str | None,
    *,
    expiry_spot: float,
    **params,
) -> dict:
    """Route to the per-structure at-expiry P&L computer.

    Backward compat: structure_type="bull_put_spread" OR empty string OR
    missing/None routes to the bull put computer. An unknown structure_type
    raises ValueError (loud failure, never a silent mis-computed outcome).
    """
    if structure_type in ("bull_put_spread", "", None):
        return spread_pnl_at_expiry(
            expiry_spot=expiry_spot,
            short_strike=params["short_strike"],
            long_strike=params["long_strike"],
            net_credit=params["net_credit"],
        )
    if structure_type == "long_put":
        return long_put_pnl_at_expiry(
            expiry_spot=expiry_spot,
            strike=params["strike"],
            premium_paid=params["premium_paid"],
        )
    if structure_type == "bear_call_spread":
        return bear_call_spread_pnl_at_expiry(
            expiry_spot=expiry_spot,
            short_strike=params["short_strike"],
            long_strike=params["long_strike"],
            net_credit=params["net_credit"],
        )
    if structure_type == "put_debit_spread":
        return put_debit_spread_pnl_at_expiry(
            expiry_spot=expiry_spot,
            bought_strike=params["bought_strike"],
            sold_strike=params["sold_strike"],
            net_debit=params["net_debit"],
        )
    if structure_type == "long_call":
        return long_call_pnl_at_expiry(
            expiry_spot=expiry_spot,
            strike=params["strike"],
            premium_paid=params["premium_paid"],
        )
    if structure_type == "call_debit_spread":
        return call_debit_spread_pnl_at_expiry(
            expiry_spot=expiry_spot,
            bought_strike=params["bought_strike"],
            sold_strike=params["sold_strike"],
            net_debit=params["net_debit"],
        )
    raise ValueError(f"unknown structure_type: {structure_type}")
