# audit-ledger-protocol

**An open methodology specification for evaluating AI-trading-agent recommendation ledgers.**

Provides the schema, computation primitives, broker-truth reconciliation protocol, and KMS-signed manifest chain for any AI-trading platform that wants its recommendation history to be externally auditable. MIT-licensed.

[brk-tasty](https://github.com/themisfitdj/brk-tasty) is the first reference adapter implementation.

---

## Why this exists

AI-trading frameworks have proliferated in 2026 — TradingAgents (UCLA, 83k stars, per-ticker decision log via SQLite), Vibe-Trading (v0.1.9, "full audit ledger" claim, walk-forward + Monte Carlo + bootstrap-CI validation), OpenAI Codex Finance plugins ("monitor investment theses"), Robinhood Agentic, plus an open-source long tail. Each ships its own ad-hoc audit-ledger format. None publishes a spec that lets an external observer reproduce the platform's claims without trusting the platform's own runtime.

This repo publishes a methodology — the *protocol* — so that the question "what does a recommendation, an outcome, and a reconciliation actually mean" has one answer regardless of the underlying platform. Adapters plug in the platform-specific bits (ledger source, price source, broker normalization); the framework handles the rest.

Aligned with **EU AI Act Article 12** (full enforcement 2026-08-02), which is converging on append-only logs + hash chaining + immutable storage as the technical reference for AI-agent audit trails.

## What's in the package

### `audit_ledger.schema`

Core dataclasses every adapter consumes:

- `Recommendation` — a single recommendation entry (today: bull-put-spread shape; multi-strategy generalization tracked as roadmap item)
- `Run` — a recommendation ledger run (timestamp + ranked recommendations)
- `SyntheticOutcome` — the "what-if-held-to-expiry" outcome for one recommendation
- `ClosedTrade` — a realized closed trade for matching against recommendations

### `audit_ledger.outcomes`

Pure functions for outcome computation:

- `compute_synthetic_outcome(rec, rec_timestamp, expiry_close)` → hold-to-expiry P&L
- `pending_outcome(rec, rec_timestamp)` — expiry in future
- `unavailable_outcome(rec, rec_timestamp)` — close price not fetched

### `audit_ledger.aggregate`

Bucketing primitives:

- `aggregate_by(outcomes, keys_fn)` → per-bucket count, mean P&L, outcome-class counts
- `decile_key(value)` → string bucket name for any 0–1 metric (EV%, confidence, etc.)

### `audit_ledger.match`

Recommendation-to-closed-trade matching:

- `join_realized(rec, rec_date, closed_trades)` → matched `ClosedTrade` or `None`

### `audit_ledger.strategies.bull_put_spread`

The first reference strategy. Three-branch P&L at expiry:

- `spread_pnl_at_expiry(expiry_spot, short_strike, long_strike, net_credit)` → `{pnl_dollars, pnl_pct_bp, outcome_class}`

### `audit_ledger.reconciliation`

The broker-truth reconciliation engine — replaces markdown-based realized-stream matching with a broker-anchored chain. Classifies every recommendation + every broker fill into one of 11 explicit `MatchStatus` values, computes realized P&L from broker transaction net-value (multiplier-agnostic by construction), decomposes the period NLV delta into six attributable lines.

- `MatchStatus` enum — `matched | recommended_not_taken | off_system_fill | partial | roll | held_open | expired_worthless | assigned | exercised | structure_unsupported | discrepancy`
- `Origin` enum — `program | discretionary | unknown`
- `Structure` enum — `bull_put_spread | iron_condor | strangle | naked | equity | unknown`
- `ReconciledTrade`, `ReconciledTradeLeg`, `RejectedRecommendation` dataclasses (immutable)
- `reconcile(recs, orders, transactions, open_positions)` → `ReconciliationResult`
- `period_tie_out(start, end, reconciled_trades, money_movements, nlv_points)` → `PeriodReconciliation`

Tolerance for the period tie-out: `max($1, 0.05% × NLV_end)`. Anything outside that surfaces as a `discrepancy` exception, never silently absorbed.

### `audit_ledger.manifest`

The daily KMS-signed manifest layer. Lets an external observer verify (with only the published files + the `cryptography` library, no AWS access) that a ledger object's SHA-256 matches the publisher's signed claim, and that a chain of daily manifests forms a continuous hash chain.

- `build_manifest(s3_client, bucket, prefixes, generated_at, prior_manifest_sha256, gap_days)` → canonical manifest dict
- `canonical_bytes(manifest)` → deterministic JSON-bytes encoding (`sort_keys=True, separators=(",", ":")`); same bytes → same signature
- `self_sha256(manifest)` → SHA-256 of canonical bytes (the chain anchor)
- `verify_signature(canonical, signature_b64, public_key_pem)` → bool (RSASSA-PSS-SHA-256)
- `walk_chain(manifests)` → chain integrity report
- `verify_manifest_hashes(s3_client, bucket, prefixes, manifest)` → optional S3 hash spot-check (auditor-only)

### `audit_ledger.adapters`

Abstract `Protocol` interfaces every adapter must implement:

- `LedgerSource` — `load_runs(start, end) -> list[Run]`
- `HistoricalPriceSource` — `close_at(symbol, date) -> float | None`
- `RealizedTradeSource` — `load_closed(start, end) -> list[ClosedTrade]`

## How to write an adapter

Look at the [brk-tasty reference adapter](https://github.com/themisfitdj/brk-tasty):

- `scripts/replay_ledger_outcomes.py` — `LedgerSource` over S3 + `RealizedTradeSource` over a curated markdown file
- `clients/massive.py` — `HistoricalPriceSource` over Polygon's daily-aggregates endpoint
- `clients/broker_transactions.py` — TastyTrade-specific broker normalizer feeding the reconciliation engine
- `scripts/manifest_lambda.py` — daily KMS-signed manifest publication

Your adapter implements the Protocol interfaces against your platform's data sources. The framework handles the methodology.

## Soft-launch status (v0)

This v0 ships with **synthetic-only outcomes** as the default — realized streams are expected to be empty in early operation because most platforms don't accumulate executed-trade data until after a methodology validation window. The framework handles empty realized streams cleanly; honest-gap framing is the moat, not over-claiming on results.

The reference brk-tasty adapter ships with:
- ✅ Live ledger (since 2026-05-03)
- ✅ Drill-verified immutable storage (Object Lock GOVERNANCE, B46-A drill PASSED 2026-06-05)
- ✅ KMS-signed daily manifest publication (since 2026-06-08, at https://github.com/themisfitdj/ledger-manifests)
- ⏳ Realized stream (empty by design; entry gate has been closed since 2026-04-06)

The realized empty-state is itself part of the methodology — see `audit_ledger.reconciliation`'s `recommended_not_taken` status.

## Roadmap

- **Multi-strategy schema:** v0 hardcodes the bull-put-spread shape on `Recommendation`. v1 generalizes to a `structure` discriminator + `strategy_metadata: dict`. Tracked in brk-tasty BACKLOG.md as B66.
- **Public adapters:** the framework gains credibility as external adapters publish. PRs welcome.
- **Realized-stream validation:** once any adapter accumulates ≥4 weeks of realized matches, this README adds a results section anchored in the adapter's published manifests.

## License

MIT. See LICENSE.

## Citation

If you use this protocol or build adapters against it, cite the repository URL and the version tag (when tagged). v0 is GitHub-only; PyPI publication targets v0.2.
