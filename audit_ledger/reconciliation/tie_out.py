"""Period tie-out — Δ(NLV) decomposition with discrepancy detection.

Decomposes the broker's net-liquidating-value change over a period into
six attributable lines:
    realized_pnl + mark_to_market + dividends + interest
    + balance_adjustments + cash_deposits_withdrawals + cash_other

Anything outside the residual tolerance (max($1, 0.05% × NLV_end)) fires
a `discrepancy` exception. The discrepancy is surfaced; it is never
silently absorbed.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from audit_ledger.broker.types import BrokerTransaction, NlvPoint
from audit_ledger.reconciliation.schema import PeriodReconciliation, ReconciledTrade


_MM_INTEREST = ("Credit Interest", "Debit Interest")
_MM_MTM = ("Mark to Market",)
_MM_DIVIDEND = ("Dividend",)
_MM_BALANCE_ADJ = ("Balance Adjustment",)
_MM_DEPOSITS = ("ACH Deposit", "ACH Withdrawal", "Wire", "Wire Transfer")


def period_tie_out(
    start: date,
    end: date,
    reconciled_trades: list[ReconciledTrade],
    money_movements: list[BrokerTransaction],
    nlv_points: list[NlvPoint],
) -> PeriodReconciliation:
    """Decompose Δ(NLV) into attributable lines. Discrepancy if residual
    exceeds max($1, 0.05% × NLV_end)."""
    in_window_nlv = [n for n in nlv_points if start <= n.date <= end]
    if not in_window_nlv:
        nlv_start = nlv_end = Decimal("0")
    else:
        in_window_nlv.sort(key=lambda n: n.date)
        nlv_start = in_window_nlv[0].close
        nlv_end = in_window_nlv[-1].close
    nlv_delta = nlv_end - nlv_start

    realized = sum(
        (t.realized_pnl_dollars for t in reconciled_trades if t.realized_pnl_dollars is not None),
        Decimal("0"),
    )

    mtm = Decimal("0")
    dividends = Decimal("0")
    interest = Decimal("0")
    balance_adj = Decimal("0")
    deposits = Decimal("0")
    other = Decimal("0")

    for mm in money_movements:
        if not mm.is_money_movement():
            continue
        sub = mm.transaction_sub_type
        if sub in _MM_MTM:
            mtm += mm.net_value
        elif sub in _MM_DIVIDEND:
            dividends += mm.net_value
        elif sub in _MM_INTEREST:
            interest += mm.net_value
        elif sub in _MM_BALANCE_ADJ:
            balance_adj += mm.net_value
        elif sub in _MM_DEPOSITS:
            deposits += mm.net_value
        else:
            other += mm.net_value

    attributed = realized + mtm + dividends + interest + balance_adj + deposits + other
    residual = nlv_delta - attributed

    exceptions: list[dict] = []
    tolerance = max(Decimal("1.00"), nlv_end * Decimal("0.0005"))
    if abs(residual) > tolerance:
        exceptions.append({
            "type": "discrepancy",
            "message": f"Period tie-out residual ${residual} exceeds tolerance ${tolerance}",
            "attributable_amount": str(residual),
        })

    return PeriodReconciliation(
        start=start, end=end,
        nlv_start=nlv_start, nlv_end=nlv_end, nlv_delta=nlv_delta,
        realized_pnl=realized, mark_to_market=mtm, dividends=dividends,
        interest=interest, balance_adjustments=balance_adj,
        cash_deposits_withdrawals=deposits, cash_other=other,
        residual=residual, exceptions=exceptions,
    )
