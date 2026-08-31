# NIST SP 800-171 Control Mapping (internal DRAFT)

INTERNAL DRAFT — indicative, not an assessment. Maps a subset of 800-171
controls to SCR mechanisms. Gaps are listed, not hidden.

| Control | Family | SCR mechanism | Evidence (test) |
|---|---|---|---|
| 3.1.1 Limit system access to authorized users | AC | Bearer token auth + RBAC roles | `test_service.py`, `test_rbac.py` |
| 3.1.2 Limit access to permitted transactions/functions | AC | RBAC matrix, deny-by-default | `test_rbac.py` |
| 3.1.5 Least privilege | AC | capability manifests, per-edge attenuation | `test_capability.py`, `test_orchestration.py` |
| 3.1.7 Prevent non-privileged users from privileged functions | AC | policy tightening intersection-only | `test_policy.py` |
| 3.3.1 Create/retain audit records | AU | hash-chained ledger, evidence bundles | `test_ledger.py`, `test_evidence.py` |
| 3.3.2 Trace actions to users | AU | approval events carry approver identity; correlation ids | `test_approval.py`, `test_observability.py` |
| 3.3.8 Protect audit information from unauthorized modification | AU | HMAC seals, offline verify, tamper localization | `test_evidence.py` |
| 3.4.x Configuration management (signed baselines) | CM | signed `.scpkg`, pinning, revocation | `test_loader.py`, `test_signing.py` |
| 3.5.x Identification & authentication | IA | token auth; DPAPI-protected credentials | `test_service.py`, `test_vault.py` |
| 3.13.11 Cryptographic protection | SC | Ed25519, SHA-256, HMAC, AES-256-GCM backup | `test_signing.py`, `test_backup.py` |
| 3.13.16 Protect data at rest | SC | DPAPI vault, encrypted backups, no plaintext secrets | `test_vault.py`, `test_backup.py` |
| 3.14.x System integrity | SI | fail-closed loader, crash recovery, quarantine | `test_recovery.py`, `test_loader.py` |

## Known gaps (not yet claimed)

- Media protection (3.8), physical protection (3.10) — out of software scope.
- Full audit-reduction/reporting tooling (3.3.6) — partial (evidence export).
- Continuous monitoring (3.11 risk assessment cadence) — operational, not code.
