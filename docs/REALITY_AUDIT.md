# REALITY AUDIT — SelfConnect Runtime (SCR)

Date: 2026-09-01. Read-only, evidence-based. Method: three independent audit
strands (failure-class sweep, doc/code claims divergence, patent prior-art)
plus first-party unplug tests (T0 no-model, T1 local-model) run from a plain
shell against BOTH the source tree and the frozen `scr.exe`. Prose was treated
as a claim to be checked, never as evidence. Nothing here is fixed — findings
only.

## 1. One-paragraph verdict

**SCR is real software, not a control plane without an engine.** Its machinery
— kernel state-machine, deny-by-default capability enforcement, path/symlink/ADS
containment, hash-chained HMAC-sealed **offline-verifiable** ledger, Ed25519+Merkle
package signing, real subprocess sandbox isolation, kill-9/TerminateProcess
crash-safety, vault, licensing, CLI, and a 27 MB MSI — **runs and is tested
against the real thing with ZERO model**: 372 tests pass with no endpoint,
36 of 62 test files use no model at all, and deterministic tools (`ledger
verify`, `doctor`, the compliance mapper, the embedded stdlib `verify.py`) run
with no LLM whatsoever. The **agent loop** needs *a* model — but any
customer-supplied local one (proven live on qwen3.6:27b through the frozen exe,
no Claude Code / Codex in the path). **It does NOT "work 100%," and it does not
fully live up to its own claim sheet.** Three asserted guarantees are not
enforced end-to-end: (A) the *to-be-ported* PCTC delegation token's attenuation
[already HALTED; not in the shipped repo], (B) **G3 "package re-verified at
every execution" — a false ✅: the `scr run` path never re-verifies** [real
hole], and (C) classification "deny-by-default" [nominal — default ceiling is
unrestricted and no shipped package sets one]. Its cryptography is **entirely
established prior art** and must be described as standard technique, never
invention. The repo's honesty posture is otherwise strong — most claims are
backed by real tests or live artifacts, and most gaps are self-labeled OPEN —
but the false ✅ on G3/G2 and the "STATUS ⊆ tested reality" self-certification
are the exceptions that make parts of the claim sheet lie, and they are the
first things to correct before any customer-facing use.

## 2. Decision gates (yours)

- **DG-1 — Wire G3 re-verification into the run path?** `scr run` and
  `scr run <team>` do not re-verify the installed package before executing it.
  Fixing means calling `registry.verify_installed(...)` (or `verify_package`)
  at run start and refusing on tamper/revocation. Real per-run cost + a design
  choice (full re-hash vs cached signature check). **Security-critical; I did
  not wire it unprompted.** Recommended P0.
- **DG-2 — PCTC delegation-token fix direction** (from the 2026-09-01 Owner's
  Inbox finding): signature-chain / target-verify / bound-caveat-commitment.
  Layer #3 stays halted until you pick.
- **DG-3 — Claim-sheet correction policy.** I can correct the demonstrably
  false ✅ markers (G3, G2, self-cert) in STATUS now (that removes the lie
  without touching code), or leave STATUS frozen for your review. Say which.

## 3. Unplug tests (first-party, captured)

**T0 — no AI at all (plain shell, no model endpoint):**
- `pytest -q` → `372 passed, 7 skipped` (exit 0). The suite needs no model;
  `MockAdapter` is a deterministic stand-in for the LLM only.
- `scr.exe --home <h> doctor` → all checks `OK` (db, disk, lock, models=0, clock).
- `scr.exe ledger verify <real bundle>` → `RESULT: VERIFIED` (chain + seal), no model.
- `python -c "scr.compliance.map_bundle(...)"` → `14/45 controls`, computed with zero model.
- Embedded `verify.py` extracted from a real bundle, run with `python -I` (no
  site, scr not importable) → verifies. Pure stdlib.

**T1 — local model only (Ollama qwen3.6:27b on the Spark, no harness):** RUN F
(security team), the compliance layer, and the local-agent layer each completed
real work end-to-end through the frozen `scr.exe`; sealed bundles VERIFIED;
grounded file reads and written reports present in the chain. No Claude Code /
Codex in any run path.

**T2 — with harness:** not needed; T0/T1 pass. The harness supplies nothing at
runtime.

## 4. Failure-class checklist (independent sweep, all cited in the strand reports)

| Class | Result |
|---|---|
| Instructions masquerading as software | CLEAR — `scr/` is code; packages are data loaded by the runtime, not harness-interpreted prompts |
| Harness welds | CLEAR — no `claude`/`codex` launch, no `.claude/` hook/transcript read in `scr/`; those live only in the *unported* PKA SDK (Tier-H, deferred) |
| Stubs presented as layers | CLEAR — 0 `NotImplementedError`/TODO/Ellipsis bodies in `scr/` |
| Green tests that prove nothing | CLEAR — MockAdapter substitutes only the LLM; crypto, subprocess sandbox, SQLite, sockets tested real; 36/62 files use no model |
| Silent-proceed failures | CLEAR — no broad `except: pass`; every one is narrow OS cleanup with a fallback; `exist_ok=True` only on OUTPUT dirs; empty child result explicitly `not_counted` |
| Missing fault barriers | CLEAR — both boundaries wrapped (`_run_tool_fn`, `_model_call_with_retry` + `_loop` model-error fold) |
| Doc/code divergence | **FOUND** — G3 ✅ not wired to run path; G2 gemma3 self-test stale; classification deny-by-default nominal; DESIGN §5 "Authenticode-signed" overstated; "STATUS ⊆ tested reality" self-cert false (see §5) |
| Missing/tribal source | Noted (not in `scr/`): the PKA SDK `launch/`+`services/` are `.pyc`-only — a *source-of-migration* gap (G-C), not an SCR defect |
| Duplicate lineages / two engines | Noted at inventory: 3 SDK copies + selfconnect-terminal v3 (a 2nd engine) — decided (SCR canonical) |
| Environment traps | Prior live findings fixed (bash-mangled workspace, num_ctx, TEMP, mojibake); verifier ASCII-only |
| Frozen/installed-only defects | Audited against the frozen exe; worker-spawn + evidence-data bugs already fixed and gated |
| Secrets & credentials | CLEAR — vault blob excludes plaintext (tested); no keys in tracked files |
| Confabulation surface | Strong — the ledger + `verify_run.ps1` + execution-summary cross-check model prose against chain facts |

## 5. Claims that DON'T hold (the "lies" to correct)

1. **G3 "Package re-verified at each execution ✅" — FALSE on the shipped
   path.** `cli._run_team` loads the `.scpkg` off disk (`load_team_from_package`,
   provenance hash only); `cli._session_manager` passes no `package_guard`;
   `sessions.py:63` runs a guard only if given one. `registry.verify_installed`
   is called by `install()`/`reload()` only. A package tampered on disk or
   revoked after install still executes. Unit-tested in isolation
   (`test_registry.py`), bypassed in production. **Highest priority (DG-1).**
2. **G2 "self-tests pass against Ollama ✅ live (gemma3)" — STALE.** The
   package's only current self-test is a 3-agent team test STATUS itself says
   gemma3 cannot run (no tool support); the cited gemma3 pass was an older
   2-scenario test no longer shipped.
3. **"STATUS claims ⊆ tested reality ✅" — not fully true** (contradicted by 1–2).
4. **Classification "deny-by-default" — nominal.** Enforcement path is real and
   tested, but default ceiling = `secret` (unrestricted) and no shipped package
   sets any classification, so nothing is actually restricted by this dimension.
5. **DESIGN §5 "Authenticode-signed" MSI — overstated;** the MSI is unsigned
   (disclosed downstream in STATUS/CLEAN_BOX/ADR-015, not in §5).
6. **(Already handled honestly)** PCTC token attenuation is broken — but that
   token is NOT in `scr/` (it's the unported source); STATUS marks layer #3
   ⛔ HALTED. Not a shipped-SCR lie; a correctly-flagged migration blocker.

## 6. Prior-art honesty (all commodity — describe as standard technique, never "invented")

| Technique SCR uses | Established prior art | Verdict |
|---|---|---|
| Macaroon-style attenuable tokens | Google Macaroons 2014; biscuit; IETF attenuating-agent-tokens | COMMODITY |
| Ed25519 `did:key` identity | W3C-CCG did:key | COMMODITY |
| Deny-by-default capability model | object-capability (Dennis&VanHorn 1966; Miller 2006); Capsicum | COMMODITY |
| Hash-chained HMAC ledger | Certificate Transparency RFC 6962/9162; Merkle 1979 | COMMODITY |
| WAL + deterministic replay + recovery | ARIES (Mohan 1992); event sourcing | COMMODITY |
| Vendor-neutral BYO-model agent runtime | OpenCode, Cline, Goose, Aider, OpenHands, TrueForge (2025-26) | COMMODITY (category) |
| Signed Merkle packages + revocation | TUF; Sigstore/Rekor | COMMODITY |

Only defensible differentiation = **integration specificity** (coding-agent
governance binding these standard parts at spawn/mutation time), and even that
is narrowing (Sovereign Execution Broker, arXiv:2606.20520). **Strike from any
copy:** "invented," "novel," "proprietary protocol," "first vendor-neutral
runtime," "unique tamper-proof ledger," "breakthrough delegation." The genuine
patent-leaning novelty (BPC bound-pair) is a *separate, already-filed*
component, not this runtime. `SELFCONNECT_LAYERS.md` already names these as
"macaroon-style"/"did:key" honestly; DESIGN §11 "claim seeds" is where commodity
primitives drift into invention framing and should be reworded to cite prior art.

## 7. What "working / honest" would take (do NOT start — for your order)

1. **Wire G3 into the run path** (DG-1): re-verify installed package at
   `scr run` start; refuse on tamper/revocation; a test that a
   tampered-on-disk installed package is refused by `scr run` (not just by
   `install`). ~1–2 hrs.
2. **Correct the false claim markers** (DG-3): STATUS G3→◑/OPEN, G2→re-run on
   qwen or mark stale, drop the "⊆ tested reality" self-cert or scope it. Reword
   DESIGN §11 to admit prior art; DESIGN §5 MSI→"unsigned (Authenticode pending)".
   Minutes.
3. **Classification**: either ship a package that sets ceilings (make
   deny-by-default real in content) or reword the claim to "supported,
   opt-in". ~1 hr.
4. **PCTC token** (DG-2): port only after you pick a fix direction. Separate track.

The machinery does not need remediation to be called real. The claim sheet does.
