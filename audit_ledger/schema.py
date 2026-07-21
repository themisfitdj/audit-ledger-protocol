"""Core dataclasses every adapter consumes.

These shapes are the methodology's contract: changes here require a version
bump and an adapter-compatibility note in the changelog. `Recommendation`
carries a `structure_type` discriminator + a per-structure `strategy_metadata`
dict, so all six BRK-124/126 structures (bull put / bear call spread, long put /
long call, put / call debit spread) share one shape. The bull-put strike fields
are retained (Optional) for backward compatibility with pre-discriminator
recs; their removal is the v4.0 Phase 3 change, gated on the historical-ledger
archive.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Recommendation:
    """A single ranked recommendation in a Run.

    Multi-structure via the ``structure_type`` discriminator + a per-structure
    ``strategy_metadata`` dict whose keys mirror the ledger ``suggested_strikes``
    shapes:

      * ``bull_put_spread`` / ``bear_call_spread``: ``{short, long, net_credit}``
      * ``long_put`` / ``long_call``:               ``{long, premium}``
      * ``put_debit_spread`` / ``call_debit_spread``: ``{bought, sold, net_debit}``

    ``bp_required`` is generic across every structure. The bull-put strike
    fields (``short_strike`` / ``long_strike`` / ``net_credit``) are Optional
    and default to ``None`` so non-bull-put structures construct cleanly; they
    are kept only for backward compatibility with pre-discriminator recs (whose
    ``structure_type`` is absent / empty and whose P&L computes off these
    fields). New structures leave them ``None`` and populate ``strategy_metadata``.

    Adapters parse their own ledger format and produce instances of this
    dataclass — the audit_ledger framework consumes only this shape.
    """
    symbol: str
    confidence: str
    risk_flags: tuple[str, ...]
    ev_pct: float
    iv_rank: float
    expiry: datetime.date
    bp_required: float
    # Discriminator + per-structure params (keys mirror suggested_strikes shapes).
    structure_type: str = "bull_put_spread"
    strategy_metadata: dict = field(default_factory=dict)
    # Bull-put backward-compat fields — Optional; unset for non-bull-put structures.
    short_strike: float | None = None
    long_strike: float | None = None
    net_credit: float | None = None


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
