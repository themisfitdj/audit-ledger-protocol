"""Recommendation-to-realized-trade matching.

Two public surfaces:

1. ``join_realized`` — the original dataclass join. Bull-put-spread-shaped
   (matches on symbol + expiry + short_strike + long_strike, exact). Suitable
   for a bull-put-only realized stream, where every fill is bull-put-shaped
   and never carries other structures.

2. ``match_structured_rec`` — the structure-aware, strike-tolerant
   matcher used by the broker-truth reconciliation engine. It dispatches on a
   ``structure_type`` discriminator, matches on (symbol, expiry,
   structure_type) + that structure's own leg strikes, attributes a realized
   fill to the NEAREST-strike candidate rec, ALWAYS records per-leg strike
   deviation, and classifies the match as exact / within_tolerance /
   loose_review. It refuses (returns no match) ONLY when no rec shares
   (symbol, expiry, structure_type) — a fill is never silently dropped.

The structure-aware matcher is what lets multi-leg directional strategies
(call_debit_spread, long_call, etc.) attribute fills and lets operator-tweaked
strikes still join.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import Decimal

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

    Bull-put-spread-shaped and exact-match by construction, for a bull-put-only
    markdown realized stream. Structure-aware/tolerant attribution for the
    broker-truth stream lives in ``match_structured_rec``.
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


# ── BRK-155: structure-aware, strike-tolerant matcher ──────────────────────────

# Default strike tolerance, expressed as an ABSOLUTE band in strike points
# (the same unit as the contract's strike — dollars for equities, points for
# futures). A per-leg deviation whose absolute value is <= this band is
# classified ``within_tolerance``; anything beyond it is attributed to the
# nearest rec anyway but flagged ``loose_review``. Operator-approved default:
# 2.5 strike points covers ordinary operator nudges (typically 1-2 strikes on
# $1 / $2.5 ladders) without absorbing a clearly different trade. Module-level
# so a caller can override per-thesis if a wider ladder warrants it.
DEFAULT_STRIKE_TOLERANCE = Decimal("2.5")

# Match classifications carried onto the ReconciledTrade for auditability.
MATCH_EXACT = "exact"
MATCH_WITHIN_TOLERANCE = "within_tolerance"
MATCH_LOOSE_REVIEW = "loose_review"

# Per-structure leg-strike field map. Each entry names the keys to read from a
# rec's ``suggested_strikes`` dict and the option_type each leg carries. The
# order is the canonical leg order (used to pair realized legs to suggested
# legs when computing per-leg deviation).
#
#   bull_put_spread / bear_call_spread -> short, long
#   call_debit_spread / put_debit_spread -> bought, sold
#   long_call / long_put -> single long strike
_STRUCTURE_LEG_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "bull_put_spread": (("short", "P"), ("long", "P")),
    "bear_call_spread": (("short", "C"), ("long", "C")),
    "call_debit_spread": (("bought", "C"), ("sold", "C")),
    "put_debit_spread": (("bought", "P"), ("sold", "P")),
    "long_call": (("long", "C"),),
    "long_put": (("long", "P"),),
}


def structure_leg_fields(structure_type: str) -> tuple[tuple[str, str], ...] | None:
    """Return the ((suggested_strikes_key, option_type), ...) tuple for a
    structure_type, or None if the structure is not strike-matchable here."""
    return _STRUCTURE_LEG_FIELDS.get(structure_type)


def rec_suggested_strikes(rec: dict, structure_type: str) -> list[Decimal] | None:
    """Extract the per-leg suggested strikes (canonical leg order) from a rec
    dict's ``suggested_strikes``. None if the structure is unknown or any leg
    strike is missing."""
    fields = _STRUCTURE_LEG_FIELDS.get(structure_type)
    if fields is None:
        return None
    ss = rec.get("suggested_strikes") or {}
    strikes: list[Decimal] = []
    for key, _opt in fields:
        val = ss.get(key)
        if val is None:
            return None
        strikes.append(Decimal(str(val)))
    return strikes


@dataclass(frozen=True)
class StructuredMatch:
    """Result of attributing a realized fill to a recommendation.

    ``per_leg_deviation`` is realized_strike - suggested_strike, one entry per
    leg in canonical leg order (positive = realized strike above suggested).
    ``classification`` is one of MATCH_EXACT / MATCH_WITHIN_TOLERANCE /
    MATCH_LOOSE_REVIEW. The matched rec is carried so the caller can pull
    recommendation_id / thesis_id / credit, etc.
    """
    rec: dict
    classification: str
    per_leg_deviation: list[Decimal]

    @property
    def max_abs_deviation(self) -> Decimal:
        if not self.per_leg_deviation:
            return Decimal("0")
        return max(abs(d) for d in self.per_leg_deviation)


def _classify(per_leg_deviation: list[Decimal], tolerance: Decimal) -> str:
    if all(d == 0 for d in per_leg_deviation):
        return MATCH_EXACT
    if all(abs(d) <= tolerance for d in per_leg_deviation):
        return MATCH_WITHIN_TOLERANCE
    return MATCH_LOOSE_REVIEW


def match_structured_rec(
    *,
    symbol: str,
    expiry: datetime.date,
    structure_type: str,
    realized_strikes: list[Decimal],
    recs: list[dict],
    used_rec_ids: set[str] | None = None,
    tolerance: Decimal = DEFAULT_STRIKE_TOLERANCE,
) -> StructuredMatch | None:
    """Attribute a realized fill to the nearest-strike candidate recommendation.

    Candidates are recs sharing (symbol, expiry, structure_type). Among those,
    the realized fill is attributed to the rec with the smallest total absolute
    per-leg strike deviation (nearest per leg). The per-leg deviation is always
    recorded; the match is classified exact / within_tolerance / loose_review.

    Returns None ONLY when no rec shares (symbol, expiry, structure_type) — a
    fill is never silently dropped for being off by a strike.

    ``realized_strikes`` must be in the structure's canonical leg order (see
    ``_STRUCTURE_LEG_FIELDS``); callers normalize broker legs to that order.
    ``used_rec_ids`` lets the caller prevent one rec attributing to two fills.
    """
    fields = _STRUCTURE_LEG_FIELDS.get(structure_type)
    if fields is None:
        return None
    if len(realized_strikes) != len(fields):
        return None

    used_rec_ids = used_rec_ids or set()
    expiry_iso = expiry.isoformat()

    scored: list[tuple[Decimal, list[Decimal], dict]] = []
    for rec in recs:
        rid = rec.get("recommendation_id")
        if rid is not None and rid in used_rec_ids:
            continue
        if rec.get("symbol") != symbol:
            continue
        if (rec.get("structure_type") or "bull_put_spread") != structure_type:
            continue
        ss = rec.get("suggested_strikes") or {}
        rec_expiry = ss.get("expiry")
        if rec_expiry and str(rec_expiry) != expiry_iso:
            continue
        suggested = rec_suggested_strikes(rec, structure_type)
        if suggested is None:
            continue
        per_leg = [r - s for r, s in zip(realized_strikes, suggested)]
        total_abs = sum((abs(d) for d in per_leg), Decimal("0"))
        scored.append((total_abs, per_leg, rec))

    if not scored:
        return None

    total_abs, per_leg, rec = min(scored, key=lambda x: x[0])
    return StructuredMatch(
        rec=rec,
        classification=_classify(per_leg, tolerance),
        per_leg_deviation=per_leg,
    )
