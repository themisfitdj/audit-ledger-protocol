"""A leg filled across more than one transaction must carry every fill.

Reported by the brk-tasty adapter: a call debit spread opened as two 1-lot
orders (broker quantity 2) reconciled with each leg at quantity 1 and only one
order's net value, because the engine kept only the first open and first close
transaction per leg symbol. Closing both lots in one order would then have set
two contracts of proceeds against one contract of cost. A partial close read as
fully closed for the same reason.
"""
from datetime import date, datetime
from decimal import Decimal

from audit_ledger.broker.types import BrokerTransaction
from audit_ledger.reconciliation import MatchStatus, reconcile

C270 = "XYZ   261120C00270000"
C280 = "XYZ   261120C00280000"
_ids = iter(range(1000, 9999))


def _tx(symbol, action, qty, net, order_id, sub=None, ttype="Trade"):
    return BrokerTransaction(
        id=next(_ids), transaction_type=ttype, transaction_sub_type=sub or action,
        action=action, symbol=symbol, underlying_symbol="XYZ",
        instrument_type="Equity Option", quantity=Decimal(str(qty)), price=None,
        value=Decimal(str(net)), net_value=Decimal(str(net)),
        commission=Decimal("0"), clearing_fees=Decimal("0"), regulatory_fees=Decimal("0"),
        proprietary_index_option_fees=Decimal("0"), is_estimated_fee=False,
        executed_at=datetime(2026, 8, 19, 15, 34), transaction_date=date(2026, 8, 19),
        order_id=order_id, leg_count=2,
    )


REC = {"recommendation_id": "xyz-1", "run_id": "run-1", "thesis_id": "t", "symbol": "XYZ",
       "structure_type": "call_debit_spread",
       "suggested_strikes": {"bought": 270.0, "sold": 280.0, "expiry": "2026-11-20"}}

OPENS = [
    _tx(C270, "Buy to Open", 1, "-2600.00", 496773555),
    _tx(C280, "Sell to Open", 1, "2150.00", 496773555),
    _tx(C270, "Buy to Open", 1, "-2580.00", 496772301),
    _tx(C280, "Sell to Open", 1, "2140.00", 496772301),
]


def _one(txs):
    trades = [t for t in reconcile(recs=[REC], orders=[], transactions=txs).trades
              if t.match_status != MatchStatus.RECOMMENDED_NOT_TAKEN.value]
    assert len(trades) == 1
    return trades[0]


def _leg(trade, symbol):
    return next(leg for leg in trade.legs if leg.symbol == symbol)


def test_two_open_orders_per_leg_carry_both_lots():
    t = _one(OPENS)
    assert t.match_status == MatchStatus.HELD_OPEN.value
    assert _leg(t, C270).quantity == Decimal("2")
    assert _leg(t, C280).quantity == Decimal("2")
    assert _leg(t, C270).open_net_value == Decimal("-5180.00")
    assert _leg(t, C280).open_net_value == Decimal("4290.00")
    assert sorted(t.order_ids) == [496772301, 496773555]
    assert len(t.transaction_ids) == 4


def test_two_lots_closed_in_one_order_realize_every_fill():
    closes = [_tx(C270, "Sell to Close", 2, "6000.00", 600), _tx(C280, "Buy to Close", 2, "-4800.00", 600)]
    t = _one(OPENS + closes)
    assert t.match_status == MatchStatus.MATCHED.value
    # -5180.00 + 4290.00 + 6000.00 - 4800.00
    assert t.realized_pnl_dollars == Decimal("310.00")
    assert _leg(t, C270).close_net_value == Decimal("6000.00")


def test_one_open_closed_in_two_orders_realizes_both_closes():
    opens = [_tx(C270, "Buy to Open", 2, "-5180.00", 1), _tx(C280, "Sell to Open", 2, "4290.00", 1)]
    closes = [_tx(C270, "Sell to Close", 1, "3000.00", 2), _tx(C280, "Buy to Close", 1, "-2400.00", 2),
              _tx(C270, "Sell to Close", 1, "3100.00", 3), _tx(C280, "Buy to Close", 1, "-2450.00", 3)]
    t = _one(opens + closes)
    assert t.match_status == MatchStatus.MATCHED.value
    # -5180.00 + 4290.00 + 3000 - 2400 + 3100 - 2450
    assert t.realized_pnl_dollars == Decimal("360.00")


def _mismatches(trade):
    return {(e["symbol"], e["opened_quantity"], e["closed_quantity"])
            for e in trade.exceptions if e["type"] == "quantity_mismatch"}


def test_partial_close_is_not_realized_and_is_flagged():
    closes = [_tx(C270, "Sell to Close", 1, "3000.00", 600), _tx(C280, "Buy to Close", 1, "-2400.00", 600)]
    t = _one(OPENS + closes)
    assert t.realized_pnl_dollars is None
    assert t.match_status == MatchStatus.HELD_OPEN.value
    assert _mismatches(t) == {(C270, "2", "1"), (C280, "2", "1")}


def test_more_closed_than_opened_is_not_realized_and_is_flagged():
    """A lot opened before the reconciliation window closes inside it."""
    opens = [_tx(C270, "Buy to Open", 1, "-2580.00", 1), _tx(C280, "Sell to Open", 1, "2140.00", 1)]
    closes = [_tx(C270, "Sell to Close", 2, "6000.00", 2), _tx(C280, "Buy to Close", 2, "-4800.00", 2)]
    t = _one(opens + closes)
    assert t.realized_pnl_dollars is None
    assert t.match_status != MatchStatus.MATCHED.value
    assert _mismatches(t) == {(C270, "1", "2"), (C280, "1", "2")}


def test_partial_close_then_expiry_of_the_rest_realizes():
    opens = [_tx(C270, "Buy to Open", 2, "-5180.00", 1), _tx(C280, "Sell to Open", 2, "4290.00", 1)]
    closes = [_tx(C270, "Sell to Close", 1, "3000.00", 2), _tx(C280, "Buy to Close", 1, "-2400.00", 2)]
    expiries = [_tx(C270, None, 1, "0", None, sub="Expiration", ttype="Receive Deliver"),
                _tx(C280, None, 1, "0", None, sub="Expiration", ttype="Receive Deliver")]
    t = _one(opens + closes + expiries)
    # -5180.00 + 4290.00 + 3000 - 2400 + 0
    assert t.realized_pnl_dollars == Decimal("-290.00")
    assert t.exceptions == []


def test_single_fill_trade_is_unchanged():
    opens = [_tx(C270, "Buy to Open", 1, "-2580.00", 1), _tx(C280, "Sell to Open", 1, "2140.00", 1)]
    closes = [_tx(C270, "Sell to Close", 1, "3000.00", 2), _tx(C280, "Buy to Close", 1, "-2400.00", 2)]
    t = _one(opens + closes)
    assert t.realized_pnl_dollars == Decimal("160.00")
    assert _leg(t, C270).quantity == Decimal("1")
    assert _leg(t, C270).open_tx_id == opens[0].id
    assert _leg(t, C270).close_tx_id == closes[0].id
    assert t.exceptions == []
