"""Recommendation-to-realized-trade matching.

Joins a Recommendation against a list of ClosedTrade entries by strike key.
v0 is bull-put-spread-shaped (matches on symbol + expiry + short_strike +
long_strike). Multi-strategy generalization will dispatch on a `structure`
field with per-structure match-key functions.
"""
from __future__ import annotations

import datetime

from audit_ledger.schema import ClosedTrade, Recommendation


def join_realized(
    rec: Recommendation,
    rec_date: datetime.date,
    closed_trades: list[ClosedTrade],
) -> ClosedTrade | None:
    """Find the closed trade matching this recommendation, or None.

    Match key: (symbol, expiry, short_strike, long_strike). Excludes rolls
    (is_roll=True). On multi-match, returns the earliest close on or after
    rec_date.
    """
    candidates = [
        t for t in closed_trades
        if t.symbol == rec.symbol
        and t.expiry == rec.expiry
        and t.short_strike == rec.short_strike
        and t.long_strike == rec.long_strike
        and t.closed_date >= rec_date
        and not t.is_roll
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda t: t.closed_date)
