"""Core dataclasses every adapter consumes.

These shapes are the methodology's contract: changes here require a version
bump and an adapter-compatibility note in the changelog. v0 of `Recommendation`
is bull-put-spread-shaped; multi-strategy generalization (`structure`
discriminator + `strategy_metadata` dict) is roadmap.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass


@dataclass(frozen=True)
class Recommendation:
    """A single ranked recommendation in a Run.

    v0 is bull-put-spread-shaped. Multi-strategy generalization (structure
    discriminator + strategy_metadata dict) is on the roadmap.

    Adapters parse their own ledger format and produce instances of this
    dataclass — the audit_ledger framework consumes only this shape.
    """
    symbol: str
    confidence: str
    risk_flags: tuple[str, ...]
    ev_pct: float
    iv_rank: float
    short_strike: float
    long_strike: float
    expiry: datetime.date
    net_credit: float
    bp_required: float


# Backwards-compatible alias for adapter authors migrating from brk-tasty's
# pre-extract module name `RankedRec`. New adapters should use `Recommendation`.
RankedRec = Recommendation


@dataclass(frozen=True)
class Run:
    """One execution of an AI-trading-agent's recommendation-ranking pipeline.

    Adapters convert their ledger entries (whatever physical form — S3 JSON,
    SQLite row, NDJSON line) into Runs.
    """
    run_id: str
    timestamp: datetime.datetime
    time_of_day_utc: str
    ranked: tuple[Recommendation, ...]


@dataclass(frozen=True)
class SyntheticOutcome:
    """The 'what if held to expiry' outcome for a single Recommendation.

    `outcome_class` values: full_win | partial | full_loss | pending |
    data_unavailable. The first three are computed by a strategy module
    (e.g. audit_ledger.strategies.bull_put_spread). The last two are
    framework-level: `pending` means expiry hasn't occurred yet;
    `data_unavailable` means the historical close couldn't be fetched.
    """
    rec: Recommendation
    rec_timestamp: datetime.datetime
    rec_time_of_day_utc: str
    expiry_spot: float | None
    pnl_dollars: float | None
    pnl_pct_bp: float | None
    outcome_class: str


@dataclass(frozen=True)
class ClosedTrade:
    """A realized closed trade — the realized stream's atomic unit.

    Adapters' `RealizedTradeSource` produces these. v0 is bull-put-spread-
    shaped; multi-strategy generalization will introduce a discriminator
    and per-structure metadata.
    """
    closed_date: datetime.date
    symbol: str
    expiry: datetime.date
    short_strike: float
    long_strike: float
    entry_credit_per_share: float
    exit_debit_per_share: float
    pnl_dollars: float
    is_roll: bool
    notes: str
