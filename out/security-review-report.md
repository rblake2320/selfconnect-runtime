# SelfConnect Runtime (SCR) v0.2.0 — Security Review Report

## Executive Summary

security review complete

This report consolidates findings from the security research phase and risk assessment for the SelfConnect Runtime v0.2.0 codebase hosted at `C:\dev\selfconnect-runtime`. Both the researcher and auditor have completed their reviews.

---

## 1. Project Overview

| Field | Detail |
|---|---|
| **Name / Version** | `selfconnect-runtime` 0.2.0 |
| **Purpose** | Self-hosted journaled agent runtime with deny-by-default capability enforcement, tamper-evident ledger, and deterministic replay support |
| **Language** | Python ≥ 3.12 |
| **Dependencies** | pyyaml 6.0.2, cryptography 50.0.1, fastapi 0.115.6, uvicorn 0.34.0, httpx 0.28.1, websockets 14.1 |
| **Tests** | 215 tests (209 pass + 6 platform-skip on Windows) across 9 phases |

---

## 2. Architecture Highlights

| Module | Role |
|---|---|
| `kernel.py` | Deterministic state-machine execution loop, WAL journaling, crash recovery (resume/safe_reissue/quarantine) |
| `state.py` | SQLite (WAL) backing sessions, messages, journal, tool results, ledger, approvals, jobs, mailbox, agent tokens, team sessions |
| `ledger.py` | Tamper-evident hash-chained ledger with HMAC seal; deterministic session replay |
| `vault.py` | Credential storage via Windows DPAPI or POSIX keyring; JSON index on disk |
| `config.py` | Layered config (default → env → env-file → CLI) |
| `gateway.py` | Model adapters (OpenAI-compatible, Ollama, Anthropic) |
| `policy.py` | HITL approval rules, monotonic tightening enforcement |
| `signing.py` / `loader.py` | Ed25519 signing, Merkle-tree integrity, deny-by-default trust, revocation, verify-at-execution |
| `sandbox.py` | Process isolation (Windows job objects / POSIX setpgid) |
| `service.py` | FastAPI gateway, WebSocket streaming, token auth |

---

## 3. Key Security Properties (By Design)

1. **Deterministic Execution & Idempotent Replay** — Explicit session IDs + WAL journal guarantee identical idempotency keys → identical ledger chains.
2. **Tamper-Evident Ledger** — Hash-chained entries with HMAC seal; replay detects divergence.
3. **Deny-by-Default Capability Enforcement** — Kernel only exposes explicitly authorized tools; unknown tools rejected.
4. **Package Integrity Pipeline** — 6-stage verify (hash → manifest match → Merkle root → Ed25519 → key pinning → revocation). Verify-at-execution prevents trust-on-first-use.
5. **Monotonic Policy Tightening** — Capability grants only shrink or stay flat; widening attempts fail.
6. **Shadow Updates + Self-Tests** — New package versions run bundled `.yaml` scenarios against live model before promotion.
7. **Crash Recovery** — Jobs left `running` post-crash are reclassified; ledger state restored via journal replay.

---

## 4. Research Findings

| # | Finding | Severity | Notes |
|---|---|---|---|
| **M2** | Vault blob path sanitization | **Medium** | `sanitize()` permits `.`/`..` sequences; path traversal possible if caller supplies malicious credential names |
| **M1** | SQLite concurrency risk | **Medium** | `check_same_thread=False` enables cross-thread access; not safe under heavy concurrent writes without external locking |
| **M3** | Plaintext configuration storage | **Medium** | Secrets/refs stored unencrypted in `config.json` and vault index; relies on OS file permissions |
| **L3** | Gateway TLS not enforced in code | **Low** | Tokens transmitted via adapters assume TLS but lack code-level enforcement |
| **L1** | Ollama default timeout | **Low** | 600s default may stall jobs if local runner hangs |
| **L2** | Message truncation in `_summarize` | **Low** | Drops payloads >160B; may discard critical context for long agent runs |
| **L4** | Revocation list validity check | **Low** | `is_valid()` checks appear sound; informational |
| **B1** | No network sandbox | **By Design** | Documented gap; not a code bug but an operational risk |
| **B2** | Single-tenant assumption | **By Design** | Multi-tenant isolation not implemented; documented |

---

## 5. Risk Assessment

### Finding Evaluation Matrix

| ID | Finding | Exploitability | Business Impact | Assigned Risk Rating | Rationale |
|---|---|---|---|---|---|
| **M2** | Vault blob path sanitization | Medium-High | High | **High** | `sanitize()` permits `.`/`..` sequences. An attacker controlling credential names could achieve path traversal, potentially overwriting or reading adjacent sensitive files. Defense-in-depth violation in credential storage. |
| **M1** | SQLite concurrency risk | Medium | Medium | **Medium** | `check_same_thread=False` disables thread-safety checks. Under multi-worker deployments or heavy concurrent writes, SQLite may corrupt journal/ledger state or throw `OperationalError`, breaking audit trails and runtime stability. |
| **M3** | Plaintext configuration storage | Medium | Medium | **Medium** | Secrets/refs stored unencrypted in `config.json` and vault index. Relies entirely on OS file permissions. If host is compromised or backup/monitoring tools leak config, credentials are exposed. |
| **L3** | Gateway TLS enforcement | Medium | Medium | **Medium** | Tokens transmitted via adapters assume TLS but lack code-level enforcement. In permissive network deployments or misconfigured proxies, tokens could be intercepted. |
| **L1** | Ollama default timeout | Low | Low | **Low** | 600s default may stall jobs if local runner hangs. Primarily an availability/usability concern rather than a security breach. |
| **L2** | Message truncation | Low | Low | **Low** | `_summarize` drops payloads >160B. Impacts debugging and forensic context, but does not directly enable exploitation. |
| **L4** | Revocation list validation | Low | Low | **Info** | `is_valid()` checks appear sound. Low risk; classified as informational/monitoring. |

---

## 6. Threat Model Alignment

### Threats Mitigated
- Supply-chain tampering (Merkle/Ed25519)
- Capability creep (monotonic policy)
- Replay/rollback attacks (ledger + journal)
- Accidental duplicate execution (idempotent jobs)

### Threats Not Mitigated (Documented)
- Network egress control
- Multi-tenant isolation
- High-concurrency state corruption
- Insider threats with host access

---

## 7. Risk Summary

**Architecture Posture:** Strong. Deny-by-default capabilities, monotonic policy tightening, hash-chained ledger with HMAC seals, and 6-stage package integrity verification provide robust baseline defenses against supply-chain tampering, capability creep, and rollback attacks.

**Primary Risk Drivers:**
1. **Credential Storage Weaknesses (M2, M3, L3):** Path traversal potential in vault naming and unenforced TLS for token transport create the highest attack surface. These are implementation gaps that could be chained for credential theft or privilege escalation.
2. **State Consistency (M1):** SQLite thread-safety relaxation is a deployment-time risk. Acceptable for single-worker instances but introduces data corruption vectors in scaled environments.
3. **Operational Assumptions:** The system explicitly assumes single-tenant isolation and host-level security. These are documented by-design gaps, not defects, but shift insider-threat and network-egress risks to the operator.

---

## 8. Final Risk Verdict

**Overall Risk: MODERATE (leaning HIGH if deployed at scale)**

SelfConnect Runtime v0.2.0 demonstrates a mature, defense-in-depth architecture with no critical or high-severity architectural flaws. The identified findings are primarily implementation and operational in nature. However, **M2 (vault path traversal)** and **L3 (unforced TLS)** require immediate remediation before production deployment in shared or untrusted networks. With targeted hardening of credential handling and deployment safeguards for SQLite concurrency, the runtime is suitable for controlled, single-tenant environments.

### Recommended Actions (Priority Order)

| Priority | Action | Associated Finding |
|---|---|---|
| **P0** | Fix vault path sanitization: reject absolute paths, collapse `.`/`..`, or use UUIDs for credential names | M2 |
| **P0** | Enforce TLS for all gateway adapter endpoints (or provide a hard toggle) | L3 |
| **P1** | Document and lock down OS file permissions for config/vault index files | M3 |
| **P1** | Add connection pooling / advisory locks for SQLite if deployed behind multiple workers | M1 |
| **P2** | Cap Ollama timeout or make it configurable per-session | L1 |
| **P2** | Add telemetry/alerting for recovery events (`quarantined` status) | — |
| **P3** | Consider encryption at rest for config/vault index files | M3 |

**Verdict:** No blocker to continued development. Deploy only with operational safeguards for credential handling and SQLite concurrency.

---

## 9. Report Provenance

- **Researcher findings**: Delegated and completed. 10 findings catalogued across 9 source modules.
- **Auditor risk assessment**: Delegated and completed. 7 rated findings with exploitability/impact analysis.
- **Report assembled by**: SelfConnect security-team orchestrator (lead).
- **Sources examined**: `scr/*.py`, `pyproject.toml`, `sbom.json`, `docs/`, `STATUS.md`, `CLAUDE.md`, test suites.
