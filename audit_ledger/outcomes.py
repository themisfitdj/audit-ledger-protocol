"""Outcome builders.

Three small constructors that map (recommendation, available market data)
to a SyntheticOutcome. The strategy-specific P&L math lives in
audit_ledger.strategies.*; this module just wraps it in the canonical
SyntheticOutcome shape and handles the framework-level pending /
data_unavailable cases.
"""
from __future__ import annotations

import datetime

from audit_ledger.schema import Recommendation, SyntheticOutcome
from audit_ledger.strategies.dispatch import outcome_for_structure


def _dispatch_params(rec: Recommendation) -> dict:
    """Translate a rec's ``strategy_metadata`` (keyed with the ledger
    ``suggested_strikes`` shapes) into the keyword params
    ``outcome_for_structure`` expects for the rec's ``structure_type``.

      * bull_put_spread / bear_call_spread: {short, long, net_credit}
            → short_strike / long_strike / net_credit
      * long_put / long_call:               {long, premium}
            → strike / premium_paid
      * put_debit_spread / call_debit_spread: {bought, sold, net_debit}
            → bought_strike / sold_strike / net_debit
    """
    md = rec.strategy_metadata
    st = rec.structure_type
    if st in ("bull_put_spread", "bear_call_spread"):
        return {
            "short_strike": md["short"],
            "long_strike": md["long"],
            "net_credit": md["net_credit"],
        }
    if st in ("long_put", "long_call"):
        return {"strike": md["long"], "premium_paid": md["premium"]}
    if st in ("put_debit_spread", "call_debit_spread"):
        return {
            "bought_strike": md["bought"],
            "sold_strike": md["sold"],
            "net_debit": md["net_debit"],
        }
    raise ValueError(f"unknown structure_type: {st}")


def compute_synthetic_outcome(
    rec: Recommendation,
    rec_timestamp: datetime.datetime,
    expiry_close: float,
) -> SyntheticOutcome:
    """Build a SyntheticOutcome from a recommendation and the historical close
    on its expiry, dispatching on ``rec.structure_type`` via
    ``audit_ledger.strategies.dispatch.outcome_for_structure``.

    Backward compat: when ``structure_type`` is absent/empty OR
    ``strategy_metadata`` is empty, fall back to the legacy bull-put path off
    ``rec.short_strike`` / ``long_strike`` / ``net_credit`` — so
    pre-discriminator in-memory recs still compute exactly as before.
    """
    if rec.strategy_metadata:
        # Discriminator-driven path: translate the metadata keys into the
        # dispatcher's params for this structure.
        params = _dispatch_params(rec)
    else:
        # Backward-compat: empty metadata → legacy bull-put fields. The
        # dispatcher routes structure_type in {"", None, "bull_put_spread"} to
        # spread_pnl_at_expiry, so an absent/empty discriminator still computes.
        params = {
            "short_strike": rec.short_strike,
            "long_strike": rec.long_strike,
            "net_credit": rec.net_credit,
        }
    pnl = outcome_for_structure(
        rec.structure_type,
        expiry_spot=expiry_close,
        **params,
    )
    return SyntheticOutcome(
        rec=rec,
        rec_timestamp=rec_timestamp,
        rec_time_of_day_utc=rec_timestamp.strftime("%H:%M"),
        expiry_spot=expiry_close,
        pnl_dollars=pnl["pnl_dollars"],
        pnl_pct_bp=pnl["pnl_pct_bp"],
        outcome_class=pnl["outcome_class"],
    )


def pending_outcome(rec: Recommendation, rec_timestamp: datetime.datetime) -> SyntheticOutcome:
    """Build an outcome for a recommendation whose expiry has not yet occurred."""
    return SyntheticOutcome(
        rec=rec,
        rec_timestamp=rec_timestamp,
        rec_time_of_day_utc=rec_timestamp.strftime("%H:%M"),
        expiry_spot=None,
        pnl_dollars=None,
        pnl_pct_bp=None,
        outcome_class="pending",
    )


def unavailable_outcome(rec: Recommendation, rec_timestamp: datetime.datetime) -> SyntheticOutcome:
    """Build an outcome where the historical close could not be fetched.

    Surfaced so the operator/auditor knows the gap exists rather than
    silently dropping the recommendation from the report.
    """
    return SyntheticOutcome(
        rec=rec,
        rec_timestamp=rec_timestamp,
        rec_time_of_day_utc=rec_timestamp.strftime("%H:%M"),
        expiry_spot=None,
        pnl_dollars=None,
        pnl_pct_bp=None,
        outcome_class="data_unavailable",
    )
