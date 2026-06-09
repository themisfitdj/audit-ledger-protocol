"""Per-strategy P&L computers.

v0 ships with `bull_put_spread`. New strategies (futures, iron condor,
naked, strangle, equity) add their own modules; each exposes a function
that computes outcome_class + pnl_dollars + pnl_pct_bp from the
recommendation's parameters and the relevant market data at expiry.
"""
