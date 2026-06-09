# audit-ledger-protocol

**An open methodology specification for evaluating AI-trading-agent recommendation ledgers.**

Defines the schema, computation primitives, broker-truth reconciliation protocol, and KMS-signed manifest chain that lets any AI-trading platform make its recommendation history externally auditable. MIT-licensed.

This repository is the *public methodology + verification surface* extracted from a private operating platform. The methodology is open; the platform implementing it is not. External observers can read the protocol here, fetch any participating platform's signed daily manifests, and verify the cryptographic chain without ever needing access to the platform's source code or infrastructure.

---

## What this is for

AI-trading frameworks have proliferated in 2026 — TradingAgents (UCLA, 83k stars, per-ticker decision log via SQLite), Vibe-Trading (v0.1.9, "full audit ledger" claim, walk-forward + Monte Carlo + bootstrap-CI validation), OpenAI Codex Finance plugins ("monitor investment theses"), Robinhood Agentic, plus an open-source long tail. Each ships its own ad-hoc audit-ledger format. None publishes a spec that lets an external observer reproduce the platform's claims without trusting the platform's own runtime.

This repository publishes a methodology — the *protocol* — so that the question "what does a recommendation, an outcome, and a reconciliation actually mean" has one answer regardless of the underlying platform. Adapters plug in the platform-specific bits (ledger source, price source, broker normalization); the framework handles the rest.

Aligned with **EU AI Act Article 12** (full enforcement 2026-08-02), which is converging on append-only logs + hash chaining + immutable storage as the technical reference for AI-agent audit trails.

## The trust model

The framework's value proposition is that **the platform implementing it can stay private while its audit trail remains externally verifiable**. Specifically:

- The methodology in this repo is public, reviewable, and forkable. Anyone can read what "a reconciled trade" or "a period tie-out" means under this protocol.
- Each participating platform publishes its daily SHA-256 + KMS-signed manifests to a separate public repository (the reference platform's manifests live at [themisfitdj/ledger-manifests](https://github.com/themisfitdj/ledger-manifests)). The manifests are signed by the platform's KMS private key; the public key is published alongside.
- An external verifier downloads the manifest + signature + public key, runs `audit_ledger.manifest.verify.verify_signature` locally, and confirms the manifest was signed by the platform claiming ownership of it. **No AWS or platform credentials required for this step.**
- An external verifier walks the daily chain (each day's `prior_manifest_sha256` references the prior day's canonical-bytes SHA-256) and confirms the chain is unbroken. **Also no platform credentials.**
- An auditor *with* the platform's bucket read access can additionally spot-check each manifest record's hash against the actual storage object. This is the only step that requires platform-side access — and the methodology surfaces missing or mismatched hashes loudly when it happens.

Net effect: an external observer can prove "the platform claimed these records existed on date T with these hashes, signed by this KMS key" without seeing a line of the platform's source code.

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

An adapter is the bridge between this framework and a specific platform's data sources. Typical structure:

- Implement `LedgerSource` against your platform's recommendation-history backend (S3, SQLite, NDJSON file, hosted API, etc.)
- Implement `HistoricalPriceSource` against your historical-price provider (Polygon, Yahoo, broker-native, etc.)
- Implement `RealizedTradeSource` against your closed-trades store, OR — for broker-anchored realized streams — write a thin normalizer that produces `audit_ledger.broker.types.BrokerOrder` and `BrokerTransaction` instances and feed them into `audit_ledger.reconciliation.reconcile()`
- Wire a daily job that calls `audit_ledger.manifest.build.build_manifest`, signs the canonical bytes with your KMS asymmetric key (RSASSA-PSS-SHA-256), and publishes manifest + signature + public key somewhere external observers can read (a public GitHub repo is the recommended pattern)

The framework consumes only the Protocol shape; it does not know or care what's behind the adapter.

## Soft-launch status (v0)

v0 ships with synthetic-only outcomes as the default. Realized streams are expected to be empty in early operation because most platforms don't accumulate executed-trade data until after a methodology validation window. The framework handles empty realized streams cleanly; honest-gap framing is the moat, not over-claiming on results.

The reference platform that drove this extraction runs against the protocol in production:

- ✅ Live ledger since 2026-05-03
- ✅ Drill-verified immutable storage (S3 Object Lock GOVERNANCE + 7y retention; restore-drill both halves PASSED 2026-06-05)
- ✅ KMS-signed daily manifest publication since 2026-06-08, viewable at [themisfitdj/ledger-manifests](https://github.com/themisfitdj/ledger-manifests)
- ⏳ Realized stream (empty by design; entry gate has been closed since 2026-04-06, no fills since)

The realized empty-state is itself part of the methodology — see `audit_ledger.reconciliation`'s `recommended_not_taken` status. The framework returns it as a first-class output rather than dropping un-traded recommendations from the report.

## Roadmap

- **Multi-strategy schema:** v0 hardcodes the bull-put-spread shape on `Recommendation`. v1 generalizes to a `structure` discriminator + `strategy_metadata: dict`. Tracked as a roadmap item on the reference platform's backlog.
- **External adapters:** the framework gains credibility as platforms beyond the reference adopter publish manifests against this spec. PRs welcome.
- **Realized-stream validation:** once any participating platform accumulates ≥4 weeks of realized matches against signed manifests, this README will add a results section anchored in those published manifests.
- **PyPI publication:** v0 is GitHub-only; PyPI publication targets v0.2.

## License

MIT. See LICENSE.

## Citation

If you use this protocol or build adapters against it, cite the repository URL and the version tag (when tagged). Issues and PRs welcome.
