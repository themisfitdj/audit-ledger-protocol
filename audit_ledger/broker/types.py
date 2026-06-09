"""Normalized broker-transaction shapes.

Read-only dataclasses with strict typing (Decimal for money, datetime for
timestamps, str | None for sparse fields). Adapters convert their broker's
SDK output (TastyTrade, IBKR, Schwab, etc.) into these shapes via
from_sdk_dict-style classmethods; the audit_ledger reconciliation engine
consumes only these shapes.

Includes parse_option_symbol() which handles the two most common option-
symbol grammars: OCC (US equity options, "USO   260515P00105000") and CME
(futures options, "./ZNM6 OZNM6 260522P106"). Brokers that use other
formats can wrap or replace this at the adapter level.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any


def _to_decimal(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    return Decimal(str(v))


def _to_required_decimal(v: Any, default: str = "0") -> Decimal:
    """For fee fields that should always be present even when zero."""
    if v is None or v == "":
        return Decimal(default)
    return Decimal(str(v))


def _to_datetime(v: Any) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    # Common SDK shapes: "...Z" or "...+00:00" or "2026-03-10 00:00:00+00"
    s = str(v).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    s = s.replace(" ", "T", 1) if "T" not in s and " " in s else s
    # Handle "+00" (no minutes) suffix
    s = re.sub(r"([+-]\d{2})$", r"\1:00", s)
    return datetime.fromisoformat(s)


def _to_date(v: Any) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    return date.fromisoformat(str(v)[:10])


# ── Option symbol parser ────────────────────────────────────────────────────

# Equity options: "USO   260515P00105000" — 6-char underlying (padded), 6-digit
# YYMMDD, P|C, 8-digit strike (×1000).
_EQUITY_OPT_RE = re.compile(
    r"^([A-Z]{1,6})\s+(\d{6})([PC])(\d{8})$"
)
# Future options: "./ZNM6 OZNM6 260522P106" — leading "./" + future code + space
# + option root + space + YYMMDD + P|C + strike (no padding multiplier).
_FUTURE_OPT_RE = re.compile(
    r"^(\./[A-Z0-9]+)\s+[A-Z0-9]+\s+(\d{6})([PC])(\d+(?:\.\d+)?)$"
)


def parse_option_symbol(symbol: str) -> dict | None:
    """Parse a broker option symbol into (underlying, expiry, option_type, strike).

    Returns None for non-option symbols (equity tickers like 'BSOL')."""
    if not symbol:
        return None
    s = symbol.strip()

    m = _EQUITY_OPT_RE.match(s)
    if m:
        underlying, yymmdd, opt_type, strike_padded = m.groups()
        year = 2000 + int(yymmdd[:2])
        month = int(yymmdd[2:4])
        day = int(yymmdd[4:6])
        # Strike is encoded as integer × 1000
        strike = Decimal(strike_padded) / Decimal("1000")
        return {
            "underlying": underlying,
            "expiry": date(year, month, day),
            "option_type": opt_type,
            "strike": strike,
        }

    m = _FUTURE_OPT_RE.match(s)
    if m:
        underlying_dot, yymmdd, opt_type, strike_str = m.groups()
        # Strip the leading "./" for the underlying — caller usually wants "/ZNM6"
        underlying = underlying_dot.lstrip(".")
        year = 2000 + int(yymmdd[:2])
        month = int(yymmdd[2:4])
        day = int(yymmdd[4:6])
        return {
            "underlying": underlying,
            "expiry": date(year, month, day),
            "option_type": opt_type,
            "strike": Decimal(strike_str),
        }

    return None


# ── Dataclasses ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class BrokerFill:
    fill_id: str
    quantity: Decimal
    fill_price: Decimal
    filled_at: datetime
    destination_venue: str | None = None

    @classmethod
    def from_sdk_dict(cls, d: dict) -> "BrokerFill":
        return cls(
            fill_id=str(d["fill_id"]),
            quantity=Decimal(str(d["quantity"])),
            fill_price=Decimal(str(d["fill_price"])),
            filled_at=_to_datetime(d["filled_at"]),
            destination_venue=d.get("destination_venue"),
        )


@dataclass(frozen=True)
class BrokerLeg:
    symbol: str
    instrument_type: str
    action: str
    quantity: Decimal
    fills: list[BrokerFill] = field(default_factory=list)

    @classmethod
    def from_sdk_dict(cls, d: dict) -> "BrokerLeg":
        return cls(
            symbol=d["symbol"],
            instrument_type=d.get("instrument_type", ""),
            action=d["action"],
            quantity=Decimal(str(d["quantity"])),
            fills=[BrokerFill.from_sdk_dict(f) for f in d.get("fills") or []],
        )

    def filled_quantity(self) -> Decimal:
        return sum((f.quantity for f in self.fills), Decimal("0"))

    def weighted_avg_fill_price(self) -> Decimal | None:
        total_qty = self.filled_quantity()
        if total_qty == 0:
            return None
        weighted = sum((f.fill_price * f.quantity for f in self.fills), Decimal("0"))
        return weighted / total_qty


@dataclass(frozen=True)
class BrokerOrder:
    id: int
    status: str
    order_type: str | None
    underlying_symbol: str | None
    underlying_instrument_type: str | None
    source: str | None
    external_identifier: str | None
    received_at: datetime | None
    terminal_at: datetime | None
    legs: list[BrokerLeg] = field(default_factory=list)

    @classmethod
    def from_sdk_dict(cls, d: dict) -> "BrokerOrder":
        return cls(
            id=int(d["id"]),
            status=str(d["status"]),
            order_type=d.get("order_type"),
            underlying_symbol=d.get("underlying_symbol"),
            underlying_instrument_type=d.get("underlying_instrument_type"),
            source=d.get("source"),
            external_identifier=d.get("external_identifier"),
            received_at=_to_datetime(d.get("received_at")),
            terminal_at=_to_datetime(d.get("terminal_at")),
            legs=[BrokerLeg.from_sdk_dict(leg) for leg in d.get("legs") or []],
        )

    def is_filled(self) -> bool:
        return self.status == "Filled"

    def is_cancelled_or_rejected(self) -> bool:
        return self.status in ("Cancelled", "Rejected")

    def leg_actions(self) -> set[str]:
        return {leg.action for leg in self.legs}

    def is_roll(self) -> bool:
        """A roll is a multi-leg filled order with both *Open and *Close legs."""
        if not self.is_filled() or len(self.legs) < 2:
            return False
        actions = self.leg_actions()
        has_open = any("Open" in a for a in actions)
        has_close = any("Close" in a for a in actions)
        return has_open and has_close


@dataclass(frozen=True)
class BrokerTransaction:
    id: int
    transaction_type: str
    transaction_sub_type: str | None
    action: str | None
    symbol: str | None
    underlying_symbol: str | None
    instrument_type: str | None
    quantity: Decimal | None
    price: Decimal | None
    value: Decimal
    net_value: Decimal
    commission: Decimal
    clearing_fees: Decimal
    regulatory_fees: Decimal
    proprietary_index_option_fees: Decimal
    is_estimated_fee: bool
    executed_at: datetime | None
    transaction_date: date | None
    order_id: int | None
    leg_count: int | None
    destination_venue: str | None = None
    description: str | None = None

    @classmethod
    def from_sdk_dict(cls, d: dict) -> "BrokerTransaction":
        return cls(
            id=int(d["id"]),
            transaction_type=str(d["transaction_type"]),
            transaction_sub_type=d.get("transaction_sub_type"),
            action=d.get("action"),
            symbol=d.get("symbol"),
            underlying_symbol=d.get("underlying_symbol"),
            instrument_type=d.get("instrument_type"),
            quantity=_to_decimal(d.get("quantity")),
            price=_to_decimal(d.get("price")),
            value=_to_required_decimal(d.get("value")),
            net_value=_to_required_decimal(d.get("net_value")),
            commission=_to_required_decimal(d.get("commission")),
            clearing_fees=_to_required_decimal(d.get("clearing_fees")),
            regulatory_fees=_to_required_decimal(d.get("regulatory_fees")),
            proprietary_index_option_fees=_to_required_decimal(
                d.get("proprietary_index_option_fees")
            ),
            is_estimated_fee=bool(d.get("is_estimated_fee", False)),
            executed_at=_to_datetime(d.get("executed_at")),
            transaction_date=_to_date(d.get("transaction_date")),
            order_id=int(d["order_id"]) if d.get("order_id") else None,
            leg_count=int(d["leg_count"]) if d.get("leg_count") not in (None, "") else None,
            destination_venue=d.get("destination_venue"),
            description=d.get("description"),
        )

    def is_trade(self) -> bool:
        return self.transaction_type == "Trade"

    def is_money_movement(self) -> bool:
        return self.transaction_type == "Money Movement"


@dataclass(frozen=True)
class NlvPoint:
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal

    @classmethod
    def from_sdk_dict(cls, d: dict) -> "NlvPoint":
        # SDK gives 'time' like "2026-03-10 00:00:00+00"
        dt = _to_datetime(d["time"])
        return cls(
            date=dt.date(),
            open=Decimal(str(d["open"])),
            high=Decimal(str(d["high"])),
            low=Decimal(str(d["low"])),
            close=Decimal(str(d["close"])),
        )


# Async broker-SDK fetchers (fetch_orders / fetch_transactions / fetch_nlv)
# live in the adapter — see e.g. brk-tasty's clients/broker_transactions.py
# for the TastyTrade SDK implementation. Other adapters bring their own
# broker-SDK code and return lists of these dataclass shapes.
