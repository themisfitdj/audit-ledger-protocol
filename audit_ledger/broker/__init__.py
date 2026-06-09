"""Normalized broker-transaction schemas.

These are the canonical shapes the reconciliation engine consumes. Adapters
normalize their broker's SDK output (TastyTrade, IBKR, Schwab, Robinhood
agentic, etc.) into these shapes; the engine doesn't know or care which
broker produced them.

Includes a generic option-symbol parser that handles equity-option
(OCC-formatted, e.g. "USO   260515P00105000") and CME futures-option
(e.g. "./ZNM6 OZNM6 260522P106") shapes. Brokers that use other symbol
formats can extend or replace the parser at the adapter level.
"""
from audit_ledger.broker.types import (  # noqa: F401
    BrokerFill,
    BrokerLeg,
    BrokerOrder,
    BrokerTransaction,
    NlvPoint,
    parse_option_symbol,
)
