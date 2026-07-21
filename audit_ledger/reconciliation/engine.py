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
from decimal import Decimal

from audit_ledger.broker.types import (
    BrokerOrder,
    BrokerTransaction,
    parse_option_symbol,
)
from audit_ledger.match import (
    DEFAULT_STRIKE_TOLERANCE,
    match_structured_rec,
    structure_leg_fields,
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
    """Whether v0 credit semantics (recommended_credit / actual_entry_credit /
    exit_debit / slippage_vs_model) apply. Frozen to bull_put_spread — the only
    credit structure for which those credit-named fields are meaningful.
    Broadening realized P&L (BRK-157) does NOT change this set: debit/long
    structures get realized_pnl_dollars but leave the credit-named fields None
    so credit semantics are never forced onto a debit attribution."""
    return structure == Structure.BULL_PUT_SPREAD.value


# BRK-157: structures for which realized P&L = sum(open_net_value) +
# sum(close_net_value) across legs is a meaningful broker-truth number. The
# net_value sign convention (credit +, debit -) makes the sum profit-positive
# for credit AND debit/long structures alike, so it is structure-agnostic
# across this set. Genuinely unpriceable topologies (iron_condor, strangle,
# naked, equity, unknown) are excluded — they stay structure_unsupported.
_PRICEABLE_STRUCTURES = frozenset({
    Structure.BULL_PUT_SPREAD.value,
    Structure.BEAR_CALL_SPREAD.value,
    Structure.CALL_DEBIT_SPREAD.value,
    Structure.PUT_DEBIT_SPREAD.value,
    Structure.LONG_CALL.value,
    Structure.LONG_PUT.value,
})


def _is_priceable(structure: str) -> bool:
    return structure in _PRICEABLE_STRUCTURES


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


# How each structure_type's canonical leg order maps onto broker-leg open
# actions. For spreads the two legs are distinguished by their opening action:
# a "Sell to Open" leg vs a "Buy to Open" leg. The tuple is the canonical leg
# order (matching audit_ledger.match._STRUCTURE_LEG_FIELDS) expressed as the
# open-action keyword each leg carries.
#   bull_put / bear_call: (short=Sell, long=Buy)
#   call_debit / put_debit: (bought=Buy, sold=Sell)
#   long_call / long_put: (Buy,)
_STRUCTURE_LEG_OPEN_SIDE: dict[str, tuple[str, ...]] = {
    "bull_put_spread": ("Sell", "Buy"),
    "bear_call_spread": ("Sell", "Buy"),
    "call_debit_spread": ("Buy", "Sell"),
    "put_debit_spread": ("Buy", "Sell"),
    "long_call": ("Buy",),
    "long_put": ("Buy",),
}


def _realized_strikes_for_structure(structure_type, legs) -> list[Decimal] | None:
    """Order the trade's realized leg strikes into the structure's canonical
    leg order (so per-leg deviation lines up with the rec's suggested strikes).

    Returns None when the trade's legs don't fit the structure's leg count or
    the legs lack the expected open-side actions / strikes."""
    sides = _STRUCTURE_LEG_OPEN_SIDE.get(structure_type)
    if sides is None:
        return None
    option_legs = [leg for leg in legs if leg.strike is not None]
    if len(option_legs) != len(sides):
        return None
    ordered: list[Decimal] = []
    remaining = list(option_legs)
    for side in sides:
        match = next(
            (leg for leg in remaining if leg.action and side in leg.action),
            None,
        )
        if match is None:
            return None
        ordered.append(match.strike)
        remaining.remove(match)
    return ordered


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
        # BRK-157: realized P&L is a close-time quantity — only computed when
        # EVERY opened leg has a corresponding close (trade close, expiration,
        # assignment, or exercise). A leg opened with no close keeps the trade
        # not-fully-closed and leaves realized_pnl_dollars None.
        any_open_leg_unclosed = False

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
            # A leg that opened but has neither a trade-close nor a realized
            # close (expiration/assignment/exercise) is still open.
            if open_tx is not None and close_tx is None and realized_close_tx is None:
                any_open_leg_unclosed = True

        structure = _classify_structure(legs)
        supported = _is_supported_v0(structure)

        # BRK-155: structure-aware, strike-tolerant attribution. Try every
        # structure_type present among recs that share (symbol, expiry); for
        # each, order this trade's realized strikes into that structure's
        # canonical leg order and ask match.match_structured_rec for the
        # nearest-strike rec. A fill attributes whenever ANY rec shares
        # (symbol, expiry, structure_type) — operator-tweaked strikes still
        # join (classified within_tolerance / loose_review, never dropped).
        matched_rec = None
        match_classification = None
        strike_deviation = None
        if any_open and expiry is not None:
            expiry_iso = expiry.isoformat()
            candidate_structs = {
                rec.get("structure_type") or "bull_put_spread"
                for rec in recs
                if rec.get("symbol") == underlying
                and str((rec.get("suggested_strikes") or {}).get("expiry") or expiry_iso)
                == expiry_iso
                and structure_leg_fields(rec.get("structure_type") or "bull_put_spread")
                is not None
            }
            best = None  # (max_abs_dev, struct, StructuredMatch)
            for cand in candidate_structs:
                realized_strikes = _realized_strikes_for_structure(cand, legs)
                if realized_strikes is None:
                    continue
                sm = match_structured_rec(
                    symbol=underlying, expiry=expiry, structure_type=cand,
                    realized_strikes=realized_strikes, recs=recs,
                    used_rec_ids=used_rec_ids,
                    tolerance=DEFAULT_STRIKE_TOLERANCE,
                )
                if sm is None:
                    continue
                if best is None or sm.max_abs_deviation < best[0]:
                    best = (sm.max_abs_deviation, cand, sm)
            if best is not None:
                _, matched_struct, sm = best
                matched_rec = sm.rec
                match_classification = sm.classification
                strike_deviation = list(sm.per_leg_deviation)
                # The rec's declared structure is ground truth for a matched
                # fill (broker leg topology can't disambiguate call-debit from
                # bear-call). Bull-put keeps its topology value so the v0 P&L
                # path is byte-identical.
                if matched_struct != Structure.BULL_PUT_SPREAD.value:
                    structure = matched_struct
                    supported = _is_supported_v0(structure)

        realized_pnl: Decimal | None = None
        actual_entry_credit: Decimal | None = None
        exit_debit: Decimal | None = None
        slippage: Decimal | None = None
        recommended_credit: Decimal | None = None

        # BRK-157: realized P&L = sum(open_net_value) + sum(close_net_value)
        # across legs is broker-truth and structure-agnostic (net_value already
        # encodes the credit/debit cash-flow sign AND the multiplier), so it is
        # computed for every priceable structure — bull_put plus the ai-bif
        # debit/long structures (call_debit, put_debit, long_call, long_put,
        # bear_call) — but ONLY when the trade is fully closed. The bull_put
        # arithmetic is unchanged: this is the same sum_open_nv + sum_close_nv
        # (and the realized-close-only branch) the v0 path already used, so a
        # bull_put's realized_pnl_dollars stays byte-identical.
        fully_closed = any_open and not any_open_leg_unclosed
        if _is_priceable(structure) and fully_closed:
            if realized_closes and not closes_trade:
                realized_pnl = sum_open_nv
            else:
                realized_pnl = sum_open_nv + sum_close_nv

        # Credit-named fields (actual_entry_credit / exit_debit, and downstream
        # recommended_credit / slippage_vs_model) carry credit semantics, so
        # they stay scoped to the v0-supported credit structure (bull_put).
        # Debit/long structures leave them None — BRK-157 does not force credit
        # framing onto a debit attribution.
        if supported:
            mult = legs[0].multiplier if legs else 100
            if any_open:
                actual_entry_credit = (sum_open_nv / mult).quantize(Decimal("0.0001"))
            if any_close and not realized_closes:
                exit_debit = (sum_close_nv / mult).quantize(Decimal("0.0001"))

        # BRK-155: a matched rec attributes the fill regardless of whether v0
        # computes P&L for its structure. structure_unsupported is reserved for
        # UNMATCHED fills whose topology v0 can't price (iron condor, strangle,
        # naked, equity). A matched ai-bif fill (no v0 P&L yet) still surfaces
        # MATCHED/HELD_OPEN so the attribution isn't masked.
        if realized_closes:
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
        elif not supported:
            match_status = MatchStatus.STRUCTURE_UNSUPPORTED.value
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
            match_classification=match_classification,
            strike_deviation=strike_deviation,
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
