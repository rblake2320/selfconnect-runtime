# Phase 8 plan — Remaining adapters + ops surface (design §3.2, §3.8, §5)

## Files

| File | Purpose |
|---|---|
| `scr/adapters_cloud.py` | `BedrockAdapter` (AWS SigV4-signed InvokeModel; Anthropic-on-Bedrock message shape) and `AzureOpenAIAdapter` (api-version query + api-key header) — both through the gateway's `build_request`/`parse_response` contract. SigV4 signing key derivation is verified against AWS's published example vector. |
| `scr/resilience.py` | `CircuitBreaker` (closed→open after N consecutive failures→half-open after cooldown→closed on success) with an injectable clock; `FallbackChain` that tries adapters in order, skipping open breakers, and raises only when all fail. |
| `scr/backup.py` | `create_backup(home, key)` → AES-256-GCM encrypted snapshot of the state DB + config; `restore_backup(archive, key, dest)`. Wrong key or a tampered archive fails (auth tag). |
| `scr/observability.py` | JSON log formatter with a correlation-id contextvar; a tiny metrics registry (`inc`/`observe`/`render_prometheus`) that is **off by default**. |
| `docs/ADMIN_GUIDE.md`, `docs/SECURITY_OVERVIEW.md`, `docs/NIST_800-171_mapping.md` | Internal drafts (private repo — no public/marketing text). |

## Tests

- `test_adapters_cloud.py`: SigV4 signing-key derivation matches the AWS
  documented example bytes; Bedrock build_request is deterministic and carries
  a well-formed `Authorization` SigV4 header + `X-Amz-Date`; Bedrock/Azure
  response parsing into the internal `ModelResponse`; Azure build_request
  carries the api-version + api-key.
- `test_resilience.py`: breaker opens after N failures, blocks while open,
  half-opens after cooldown, closes on a success; fallback chain returns the
  first healthy adapter's result, skips an open breaker, and raises when all
  are down; a recovered primary is used again after cooldown.
- `test_backup.py`: backup→restore round-trip reproduces the DB + config;
  wrong key fails; a single-byte tamper fails (GCM auth tag); restore is
  atomic (no partial dir on failure).
- `test_observability.py`: JSON formatter emits parseable JSON with the
  correlation id; metrics registry counts and renders Prometheus text; metrics
  are absent/inert when disabled.

## Decisions (ADR-009)

- SigV4 implemented directly (hmac/hashlib) rather than pulling `boto3` —
  keeps the dependency surface minimal and is fully offline-testable against
  AWS's published vector. No new dependency (AES-GCM via existing
  `cryptography`).
- Metrics are off by default (design §3.8); enabling is an explicit opt-in.
- NIST 800-171 mapping is an internal DRAFT, clearly labeled, in the private
  repo only.
