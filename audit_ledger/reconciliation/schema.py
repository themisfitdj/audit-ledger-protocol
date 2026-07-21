"""Reconciliation enums, dataclasses, and result containers.

These shapes are the methodology's contract; adapters consume them and
serialize them however their platform stores reconciled records. v0 freezes
the 11-value MatchStatus enum and the 6-line PeriodReconciliation
decomposition — extensions require a version bump.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum


class MatchStatus(str, Enum):
    MATCHED = "matched"
    RECOMMENDED_NOT_TAKEN = "recommended_not_taken"
    OFF_SYSTEM_FILL = "off_system_fill"
    PARTIAL = "partial"
    ROLL = "roll"
    HELD_OPEN = "held_open"
    EXPIRED_WORTHLESS = "expired_worthless"
    ASSIGNED = "assigned"
    EXERCISED = "exercised"
    STRUCTURE_UNSUPPORTED = "structure_unsupported"
    DISCREPANCY = "discrepancy"


class Origin(str, Enum):
    PROGRAM = "program"
    DISCRETIONARY = "discretionary"
    UNKNOWN = "unknown"


class Structure(str, Enum):
    BULL_PUT_SPREAD = "bull_put_spread"
    BEAR_CALL_SPREAD = "bear_call_spread"
    CALL_DEBIT_SPREAD = "call_debit_spread"
    PUT_DEBIT_SPREAD = "put_debit_spread"
    LONG_CALL = "long_call"
    LONG_PUT = "long_put"
    IRON_CONDOR = "iron_condor"
    STRANGLE = "strangle"
    NAKED = "naked"
    EQUITY = "equity"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ReconciledTradeLeg:
    symbol: str
    instrument_type: str
    action: str
    quantity: Decimal
    strike: Decimal | None
    expiry: date | None
    option_type: str | None
    multiplier: int
    open_net_value: Decimal | None
    close_net_value: Decimal | None
    open_tx_id: int | None
    close_tx_id: int | None


@dataclass(frozen=True)
class ReconciledTrade:
    run_id: str | None
    recommendation_id: str | None
    thesis_id: str | None
    thesis_version: str | None
    origin: str
    symbol: str
    structure: str
    legs: list[ReconciledTradeLeg]
    recommended_credit: Decimal | None
    actual_entry_credit: Decimal | None
    exit_debit: Decimal | None
    realized_pnl_dollars: Decimal | None
    commissions_fees: Decimal
    slippage_vs_model: Decimal | None
    order_ids: list[int]
    transaction_ids: list[int]
    match_status: str
    roll_pair_id: str | None
    exceptions: list[dict]
    # BRK-155: structure-aware tolerant-join provenance. ``match_classification``
    # is exact / within_tolerance / loose_review (None when no rec matched).
    # ``strike_deviation`` is realized_strike - suggested_strike per leg, in the
    # structure's canonical leg order (None when no rec matched). loose_review
    # is the operator-review flag — the fill is attributed, never dropped.
    match_classification: str | None = None
    strike_deviation: list[Decimal] | None = None


@dataclass(frozen=True)
class RejectedRecommendation:
    order_id: int
    status: str
    underlying_symbol: str | None
    leg_symbols: list[str]
    received_at: date | None
    terminal_at: date | None


@dataclass(frozen=True)
class PeriodReconciliation:
    start: date
    end: date
    nlv_start: Decimal
    nlv_end: Decimal
    nlv_delta: Decimal
    realized_pnl: Decimal
    mark_to_market: Decimal
    dividends: Decimal
    interest: Decimal
    balance_adjustments: Decimal
    cash_deposits_withdrawals: Decimal
    cash_other: Decimal
    residual: Decimal
    exceptions: list[dict]

    def has_discrepancy(self) -> bool:
        tolerance = max(Decimal("1.00"), self.nlv_end * Decimal("0.0005"))
        return abs(self.residual) > tolerance


@dataclass(frozen=True)
class ReconciliationResult:
    trades: list[ReconciledTrade]
    rejected: list[RejectedRecommendation]


class DiscrepancyError(Exception):
    pass
