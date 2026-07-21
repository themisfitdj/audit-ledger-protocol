"""Per-strategy P&L computers.

v0 shipped with `bull_put_spread`. BRK-126 adds the three non-bull-put
structures BRK-124 introduced (long_put, bear_call_spread, put_debit_spread).
Each module exposes a function that computes outcome_class + pnl_dollars +
pnl_pct_bp from the recommendation's parameters and the relevant market data
at expiry. Future strategies (futures, iron condor, naked, strangle, equity)
add their own modules.
"""
from audit_ledger.strategies.bear_call_spread import (  # noqa: F401
    bear_call_spread_pnl_at_expiry,
)
from audit_ledger.strategies.bull_put_spread import spread_pnl_at_expiry  # noqa: F401
from audit_ledger.strategies.long_put import long_put_pnl_at_expiry  # noqa: F401
from audit_ledger.strategies.put_debit_spread import (  # noqa: F401
    put_debit_spread_pnl_at_expiry,
)

__all__ = [
    "spread_pnl_at_expiry",
    "long_put_pnl_at_expiry",
    "bear_call_spread_pnl_at_expiry",
    "put_debit_spread_pnl_at_expiry",
]
