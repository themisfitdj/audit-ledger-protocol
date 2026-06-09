"""Reconciliation engine — broker-truth realized stream.

Builds ReconciledTrade records from (recommendations, broker orders,
broker transactions). Classifies each into a match_status, computes
realized P&L from broker tx net_value (multiplier-agnostic by
construction — the broker has already applied the multiplier), and emits
RejectedRecommendation entries for cancelled/rejected orders.

The engine is broker-agnostic. It consumes the normalized
audit_ledger.broker shapes; brokers' SDK-specific normalizers live in
adapters.

Provability principle: every dollar traces to a broker transaction.
ReconciledTrade records are immutable; corrections emit new records via
the operator's persistence layer, never mutate prior ones.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from audit_ledger.broker.types import (
    BrokerOrder,
    BrokerTransaction,
    parse_option_symbol,
)
from audit_ledger.reconciliation.schema import (
    MatchStatus,
    Origin,
    ReconciledTrade,
    ReconciledTradeLeg,
    ReconciliationResult,
    RejectedRecommendation,
    Structure,
)


_MULTIPLIER_DEFAULTS = {
    "Equity Option": 100,
    "Future Option": 100,
    "Equity": 1,
    "Future": 1,
}


def _structure_key(tx: BrokerTransaction) -> tuple | None:
    """Group key for txs that belong to the same logical trade (spread lineage)."""
    parsed = parse_option_symbol(tx.symbol or "")
    if parsed:
        return (parsed["underlying"], parsed["expiry"])
    if tx.underlying_symbol:
        return (tx.underlying_symbol, None)
    return None


def _classify_structure(legs: list[ReconciledTradeLeg]) -> str:
    """Determine the topology from the leg set."""
    if not legs:
        return Structure.UNKNOWN.value
    if all(leg.instrument_type == "Equity" for leg in legs):
        return Structure.EQUITY.value
    if len(legs) == 4 and all(leg.instrument_type in ("Equity Option", "Future Option") for leg in legs):
        return Structure.IRON_CONDOR.value
    if len(legs) == 2 and all(leg.option_type == "P" for leg in legs):
        return Structure.BULL_PUT_SPREAD.value
    if len(legs) == 2 and {leg.option_type for leg in legs} == {"P", "C"}:
        return Structure.STRANGLE.value
    if len(legs) == 1 and legs[0].instrument_type in ("Equity Option", "Future Option"):
        return Structure.NAKED.value
    return Structure.UNKNOWN.value


def _is_supported_v0(structure: str) -> bool:
    """V0 computes P&L for bull_put_spread only. Other structures are recorded
    as structure_unsupported with no P&L."""
    return structure == Structure.BULL_PUT_SPREAD.value


def _make_leg_from_txs(
    symbol: str,
    instrument_type: str,
    multiplier: int,
    open_tx: BrokerTransaction | None,
    close_tx: BrokerTransaction | None,
) -> ReconciledTradeLeg:
    parsed = parse_option_symbol(symbol)
    action = (open_tx or close_tx).action if (open_tx or close_tx) else ""
    quantity = (open_tx or close_tx).quantity if (open_tx or close_tx) else Decimal("0")
    return ReconciledTradeLeg(
        symbol=symbol,
        instrument_type=instrument_type,
        action=action or "",
        quantity=quantity or Decimal("0"),
        strike=parsed["strike"] if parsed else None,
        expiry=parsed["expiry"] if parsed else None,
        option_type=parsed["option_type"] if parsed else None,
        multiplier=multiplier,
        open_net_value=open_tx.net_value if open_tx else None,
        close_net_value=close_tx.net_value if close_tx else None,
        open_tx_id=open_tx.id if open_tx else None,
        close_tx_id=close_tx.id if close_tx else None,
    )


def _rec_matches_structure(rec: dict, underlying: str, expiry: date, strikes: set[Decimal]) -> bool:
    """Structure-based match: rec's suggested_strikes align with the trade's
    underlying + expiry + strike set."""
    if rec.get("symbol") != underlying:
        return False
    ss = rec.get("suggested_strikes") or {}
    rec_expiry = ss.get("expiry")
    if rec_expiry and str(rec_expiry) != expiry.isoformat():
        return False
    rec_strikes = {Decimal(str(ss.get("short", 0))), Decimal(str(ss.get("long", 0)))}
    return rec_strikes == strikes


def reconcile(
    recs: list[dict],
    orders: list[BrokerOrder],
    transactions: list[BrokerTransaction],
    open_positions: list = None,
) -> ReconciliationResult:
    """Reconcile ledger recs against broker orders and transactions.

    Returns:
      trades: list of ReconciledTrade (one per logical trade)
      rejected: list of RejectedRecommendation (cancelled/rejected orders)
    """
    open_positions = open_positions or []

    rejected: list[RejectedRecommendation] = []
    for o in orders:
        if o.is_cancelled_or_rejected():
            rejected.append(RejectedRecommendation(
                order_id=o.id,
                status=o.status,
                underlying_symbol=o.underlying_symbol,
                leg_symbols=[leg.symbol for leg in o.legs],
                received_at=o.received_at.date() if o.received_at else None,
                terminal_at=o.terminal_at.date() if o.terminal_at else None,
            ))

    filled_orders = [o for o in orders if o.is_filled()]
    trade_txs = [t for t in transactions if t.is_trade()]

    roll_order_ids = {o.id for o in filled_orders if o.is_roll()}

    realized_close_txs = [
        t for t in transactions
        if t.transaction_sub_type in (
            "Expiration", "Cash Settled Expiration",
            "Assignment", "Cash Settled Assignment",
            "Exercise",
        )
    ]

    groups: dict[tuple, list[BrokerTransaction]] = {}
    for tx in trade_txs:
        if tx.order_id in roll_order_ids:
            continue
        key = _structure_key(tx)
        if key is None:
            continue
        groups.setdefault(key, []).append(tx)

    for tx in realized_close_txs:
        key = _structure_key(tx)
        if key is None:
            continue
        groups.setdefault(key, []).append(tx)

    trades: list[ReconciledTrade] = []
    used_rec_ids: set[str] = set()

    for (underlying, expiry), txs in groups.items():
        opens = [t for t in txs if t.action and "Open" in t.action]
        closes_trade = [t for t in txs if t.action and "Close" in t.action]
        realized_closes = [t for t in txs if t.transaction_sub_type in (
            "Expiration", "Cash Settled Expiration",
            "Assignment", "Cash Settled Assignment",
            "Exercise",
        )]

        leg_symbols = sorted({t.symbol for t in txs if t.symbol})
        legs: list[ReconciledTradeLeg] = []
        order_ids: set[int] = set()
        tx_ids: list[int] = []
        sum_open_nv = Decimal("0")
        sum_close_nv = Decimal("0")
        sum_fees = Decimal("0")
        any_open = False
        any_close = False

        for sym in leg_symbols:
            sym_txs = [t for t in txs if t.symbol == sym]
            open_tx = next((t for t in sym_txs if t.action and "Open" in t.action), None)
            close_tx = next((t for t in sym_txs if t.action and "Close" in t.action), None)
            realized_close_tx = next(
                (t for t in sym_txs if t.transaction_sub_type in (
                    "Expiration", "Cash Settled Expiration",
                    "Assignment", "Cash Settled Assignment",
                    "Exercise",
                )),
                None,
            )
            inst_type = next((t.instrument_type for t in sym_txs if t.instrument_type), "")
            multiplier = _MULTIPLIER_DEFAULTS.get(inst_type, 100)
            effective_close = close_tx or realized_close_tx
            leg = _make_leg_from_txs(
                symbol=sym, instrument_type=inst_type, multiplier=multiplier,
                open_tx=open_tx, close_tx=effective_close,
            )
            legs.append(leg)
            for t in sym_txs:
                tx_ids.append(t.id)
                if t.order_id:
                    order_ids.add(t.order_id)
                sum_fees += (
                    t.commission + t.clearing_fees + t.regulatory_fees
                    + t.proprietary_index_option_fees
                )
            if open_tx is not None:
                sum_open_nv += open_tx.net_value
                any_open = True
            if close_tx is not None:
                sum_close_nv += close_tx.net_value
                any_close = True
            if realized_close_tx is not None:
                any_close = True

        structure = _classify_structure(legs)
        supported = _is_supported_v0(structure)

        strikes_set = {leg.strike for leg in legs if leg.strike is not None}
        matched_rec = None
        if structure == Structure.BULL_PUT_SPREAD.value and any_open:
            for rec in recs:
                rid = rec.get("recommendation_id")
                if rid and rid in used_rec_ids:
                    continue
                if _rec_matches_structure(rec, underlying, expiry, strikes_set):
                    matched_rec = rec
                    break

        realized_pnl: Decimal | None = None
        actual_entry_credit: Decimal | None = None
        exit_debit: Decimal | None = None
        slippage: Decimal | None = None
        recommended_credit: Decimal | None = None

        if supported:
            mult = legs[0].multiplier if legs else 100
            if any_open and (any_close or realized_closes):
                if realized_closes and not closes_trade:
                    realized_pnl = sum_open_nv
                else:
                    realized_pnl = sum_open_nv + sum_close_nv
            if any_open:
                actual_entry_credit = (sum_open_nv / mult).quantize(Decimal("0.0001"))
            if any_close and not realized_closes:
                exit_debit = (sum_close_nv / mult).quantize(Decimal("0.0001"))

        if not supported:
            match_status = MatchStatus.STRUCTURE_UNSUPPORTED.value
        elif realized_closes:
            sub = realized_closes[0].transaction_sub_type
            if sub in ("Expiration", "Cash Settled Expiration"):
                match_status = MatchStatus.EXPIRED_WORTHLESS.value
            elif sub in ("Assignment", "Cash Settled Assignment"):
                match_status = MatchStatus.ASSIGNED.value
            else:
                match_status = MatchStatus.EXERCISED.value
        elif matched_rec and any_open and any_close:
            match_status = MatchStatus.MATCHED.value
        elif matched_rec and any_open and not any_close:
            match_status = MatchStatus.HELD_OPEN.value
        else:
            match_status = MatchStatus.OFF_SYSTEM_FILL.value

        if matched_rec:
            origin = Origin.PROGRAM.value
        else:
            origin = Origin.DISCRETIONARY.value

        if matched_rec:
            ss = matched_rec.get("suggested_strikes") or {}
            if ss.get("net_credit") is not None:
                recommended_credit = Decimal(str(ss["net_credit"]))
            if recommended_credit is not None and actual_entry_credit is not None:
                slippage = actual_entry_credit - recommended_credit

        if matched_rec:
            used_rec_ids.add(matched_rec["recommendation_id"])

        trades.append(ReconciledTrade(
            run_id=matched_rec.get("run_id") if matched_rec else None,
            recommendation_id=matched_rec.get("recommendation_id") if matched_rec else None,
            thesis_id=matched_rec.get("thesis_id") if matched_rec else None,
            thesis_version=matched_rec.get("thesis_version") if matched_rec else None,
            origin=origin,
            symbol=underlying,
            structure=structure,
            legs=legs,
            recommended_credit=recommended_credit,
            actual_entry_credit=actual_entry_credit,
            exit_debit=exit_debit,
            realized_pnl_dollars=realized_pnl,
            commissions_fees=sum_fees,
            slippage_vs_model=slippage,
            order_ids=sorted(order_ids),
            transaction_ids=tx_ids,
            match_status=match_status,
            roll_pair_id=None,
            exceptions=[],
        ))

    # Rolls — each emits two trades sharing a roll_pair_id
    for order in filled_orders:
        if not order.is_roll():
            continue
        roll_pair_id = str(uuid.uuid4())
        order_txs = [t for t in trade_txs if t.order_id == order.id]
        open_txs = [t for t in order_txs if t.action and "Open" in t.action]
        close_txs = [t for t in order_txs if t.action and "Close" in t.action]

        if close_txs:
            sum_close_nv = sum((t.net_value for t in close_txs), Decimal("0"))
            sum_fees_close = sum(
                (t.commission + t.clearing_fees + t.regulatory_fees + t.proprietary_index_option_fees
                 for t in close_txs),
                Decimal("0"),
            )
            close_legs = [
                _make_leg_from_txs(
                    symbol=t.symbol or "",
                    instrument_type=t.instrument_type or "",
                    multiplier=_MULTIPLIER_DEFAULTS.get(t.instrument_type or "", 100),
                    open_tx=None, close_tx=t,
                )
                for t in close_txs
            ]
            trades.append(ReconciledTrade(
                run_id=None, recommendation_id=None,
                thesis_id=None, thesis_version=None,
                origin=Origin.DISCRETIONARY.value,
                symbol=order.underlying_symbol or "",
                structure=Structure.UNKNOWN.value,
                legs=close_legs,
                recommended_credit=None, actual_entry_credit=None,
                exit_debit=None, realized_pnl_dollars=None,
                commissions_fees=sum_fees_close, slippage_vs_model=None,
                order_ids=[order.id],
                transaction_ids=[t.id for t in close_txs],
                match_status=MatchStatus.ROLL.value,
                roll_pair_id=roll_pair_id,
                exceptions=[],
            ))
        if open_txs:
            sum_open_nv = sum((t.net_value for t in open_txs), Decimal("0"))
            sum_fees_open = sum(
                (t.commission + t.clearing_fees + t.regulatory_fees + t.proprietary_index_option_fees
                 for t in open_txs),
                Decimal("0"),
            )
            open_legs = [
                _make_leg_from_txs(
                    symbol=t.symbol or "",
                    instrument_type=t.instrument_type or "",
                    multiplier=_MULTIPLIER_DEFAULTS.get(t.instrument_type or "", 100),
                    open_tx=t, close_tx=None,
                )
                for t in open_txs
            ]
            trades.append(ReconciledTrade(
                run_id=None, recommendation_id=None,
                thesis_id=None, thesis_version=None,
                origin=Origin.DISCRETIONARY.value,
                symbol=order.underlying_symbol or "",
                structure=Structure.UNKNOWN.value,
                legs=open_legs,
                recommended_credit=None, actual_entry_credit=None,
                exit_debit=None, realized_pnl_dollars=None,
                commissions_fees=sum_fees_open, slippage_vs_model=None,
                order_ids=[order.id],
                transaction_ids=[t.id for t in open_txs],
                match_status=MatchStatus.ROLL.value,
                roll_pair_id=roll_pair_id,
                exceptions=[],
            ))

    # Recs with no matching trade → recommended_not_taken
    for rec in recs:
        rid = rec.get("recommendation_id")
        if rid in used_rec_ids:
            continue
        trades.append(ReconciledTrade(
            run_id=rec.get("run_id"),
            recommendation_id=rid,
            thesis_id=rec.get("thesis_id"),
            thesis_version=rec.get("thesis_version"),
            origin=Origin.PROGRAM.value,
            symbol=rec.get("symbol", ""),
            structure=Structure.BULL_PUT_SPREAD.value,
            legs=[],
            recommended_credit=Decimal(str((rec.get("suggested_strikes") or {}).get("net_credit", 0)))
                              if (rec.get("suggested_strikes") or {}).get("net_credit") is not None else None,
            actual_entry_credit=None,
            exit_debit=None,
            realized_pnl_dollars=None,
            commissions_fees=Decimal("0"),
            slippage_vs_model=None,
            order_ids=[],
            transaction_ids=[],
            match_status=MatchStatus.RECOMMENDED_NOT_TAKEN.value,
            roll_pair_id=None,
            exceptions=[],
        ))

    return ReconciliationResult(trades=trades, rejected=rejected)
