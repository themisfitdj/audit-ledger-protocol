"""KMS-signed daily manifest publication layer.

Lets an external observer verify (with only the published files + the
`cryptography` library, no AWS access) that a ledger object's SHA-256
matches the publisher's signed claim, and that a chain of daily manifests
forms a continuous hash chain.

Two halves:
- build: canonical JSON encoding + hash chaining + retention metadata
- verify: signature verification + chain walk + S3 hash spot-check
"""
from audit_ledger.manifest.build import (  # noqa: F401
    SCHEMA_VERSION,
    build_manifest,
    canonical_bytes,
    self_sha256,
)
from audit_ledger.manifest.verify import (  # noqa: F401
    VerificationResult,
    compute_sha256,
    verify_manifest_hashes,
    verify_signature,
    walk_chain,
)
