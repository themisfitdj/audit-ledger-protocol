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
from audit_ledger.strategies.bull_put_spread import spread_pnl_at_expiry


def compute_synthetic_outcome(
    rec: Recommendation,
    rec_timestamp: datetime.datetime,
    expiry_close: float,
) -> SyntheticOutcome:
    """Build a SyntheticOutcome from a recommendation and the historical close
    on its expiry. Currently delegates to bull_put_spread for the P&L math;
    multi-strategy generalization will dispatch on a `structure` field."""
    pnl = spread_pnl_at_expiry(
        expiry_spot=expiry_close,
        short_strike=rec.short_strike,
        long_strike=rec.long_strike,
        net_credit=rec.net_credit,
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
