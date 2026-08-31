# Phase 9 plan — Hardening + full matrix (design §9)

## New tests (`tests/test_hardening.py`)

- **Windows TerminateProcess chaos twin** (skipif not nt): kill a live run mid
  non-idempotent side effect with TerminateProcess (proc.kill), then recover
  from a fresh Store → quarantine + `PRAGMA integrity_check` clean. Mirrors the
  POSIX SIGKILL test.
- **Windows junction escape twin** (skipif not nt): a directory junction inside
  the jail pointing outside is denied by resolved-path containment (reparse
  point equivalent of the POSIX symlink test).
- **Disk-full chaos** (all OS): a write failure (ENOSPC) during atomic write
  leaves the ORIGINAL file intact and no `.scr-tmp-` temp behind.
- **Clock-jump chaos** (all OS): a backward `time.time()` jump does not break
  the ledger or recovery — the kernel's wall-clock guard uses `time.monotonic`
  and the ledger/hash chain use no timestamps.
- **Dual-instance contention storm** (all OS): many threads storm the workspace
  lock; mutual exclusion holds (a shared critical-section counter never sees
  two holders).
- **Upgrade-path matrix** (all OS): v1→v2(ok)→v3(fails, rolls back to v2)→
  v4(ok); `active()` is never a bad build.

## Pen-style self-review

`docs/PEN_REVIEW.md` — an adversarial read of the capability kernel and
sandbox with each considered attack and its disposition (defended / documented
limitation / fixed). Any exploitable finding becomes a regression test here.

## Final STATUS

`STATUS.md` gains a Definition-of-Done table mapping each DoD item to the
test(s) that prove it, with OPEN items (installer build/clean-box install,
DPAPI-NG, live cloud conformance) listed explicitly.

## Decisions (ADR-010)

- No new dependencies; hardening is tests + docs + any fixes they surface.
