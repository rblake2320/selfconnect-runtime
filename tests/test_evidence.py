"""Evidence bundle export + offline verification, including a subprocess
proof that the bundle verifies with nothing but Python stdlib."""
import json
import subprocess
import sys
import zipfile

import pytest

from scr.capability import CapabilityManifest
from scr.evidence import export_bundle, seal_on_close, verify_bundle
from scr.gateway import MockAdapter, ModelResponse, ToolCall
from scr.kernel import Kernel, ToolSpec
from scr.state import Store

KEY = bytes.fromhex("ab" * 32)


def _session_with_ledger(db_path):
    store = Store(db_path)
    sid = store.create_session()
    tool = ToolSpec("noop", lambda a: "ok", idempotent=True)
    kernel = Kernel(store, MockAdapter([
        ModelResponse("", (ToolCall("c1", "noop", {}),)),
        ModelResponse("finished"),
    ]), {"noop": tool}, CapabilityManifest(tools=frozenset({"noop"})))
    kernel.run(sid, "do a thing")
    seal_on_close(store, sid, KEY)
    return store, sid


def test_export_and_verify_ok(tmp_path):
    store, sid = _session_with_ledger(str(tmp_path / "s.db"))
    out = str(tmp_path / "evidence.scevidence")
    export_bundle(store, sid, KEY, out, package={"name": "enterprise", "version": "1.0.0"})
    report = verify_bundle(out, KEY)
    assert report.ok, report.text
    assert report.report["chain_ok"]
    assert report.report["session_seal_ok"] is True
    assert report.report["bundle_seal_ok"] is True
    assert report.report["count"] >= 3


def test_wrong_key_fails_seals(tmp_path):
    store, sid = _session_with_ledger(str(tmp_path / "s.db"))
    out = str(tmp_path / "e.scevidence")
    export_bundle(store, sid, KEY, out)
    report = verify_bundle(out, bytes.fromhex("cd" * 32))
    assert not report.ok
    assert report.report["bundle_seal_ok"] is False


def test_event_tamper_breaks_chain(tmp_path):
    store, sid = _session_with_ledger(str(tmp_path / "s.db"))
    out = str(tmp_path / "e.scevidence")
    export_bundle(store, sid, KEY, out)
    # rewrite bundle.json with a flipped event
    with zipfile.ZipFile(out) as z:
        bundle = json.loads(z.read("bundle.json"))
        others = {n: z.read(n) for n in z.namelist() if n != "bundle.json"}
    # Mutate an event's content so its stored hash no longer matches.
    ev = json.loads(bundle["events"][1]["event"])
    ev["_tamper"] = "x"
    bundle["events"][1]["event"] = json.dumps(ev, sort_keys=True, separators=(",", ":"))
    tampered = str(tmp_path / "t.scevidence")
    with zipfile.ZipFile(tampered, "w") as z:
        z.writestr("bundle.json", json.dumps(bundle, sort_keys=True, separators=(",", ":")))
        for n, data in others.items():
            z.writestr(n, data)
    report = verify_bundle(tampered, KEY)
    assert not report.ok
    assert not report.report["chain_ok"]


def test_bundle_mutation_breaks_bundle_seal(tmp_path):
    store, sid = _session_with_ledger(str(tmp_path / "s.db"))
    out = str(tmp_path / "e.scevidence")
    export_bundle(store, sid, KEY, out, package={"name": "p", "version": "1"})
    with zipfile.ZipFile(out) as z:
        bundle = json.loads(z.read("bundle.json"))
        hmac_val = z.read("bundle.hmac")
        verify_src = z.read("verify.py")
    bundle["package"]["version"] = "9"  # change metadata, keep old hmac
    mutated = str(tmp_path / "m.scevidence")
    with zipfile.ZipFile(mutated, "w") as z:
        z.writestr("bundle.json", json.dumps(bundle, sort_keys=True, separators=(",", ":")))
        z.writestr("bundle.hmac", hmac_val)
        z.writestr("verify.py", verify_src)
    report = verify_bundle(mutated, KEY)
    assert not report.ok
    assert report.report["bundle_seal_ok"] is False


def test_offline_standalone_verifier_no_scr(tmp_path):
    """Extract the embedded verify.py and run it in a subprocess whose working
    dir contains ONLY the bundle files — proving offline verifiability with
    nothing but Python stdlib (scr not importable from that cwd)."""
    store, sid = _session_with_ledger(str(tmp_path / "s.db"))
    out = str(tmp_path / "e.scevidence")
    export_bundle(store, sid, KEY, out)

    workdir = tmp_path / "offline"
    workdir.mkdir()
    with zipfile.ZipFile(out) as z:
        for n in ("bundle.json", "bundle.hmac", "verify.py"):
            (workdir / n).write_bytes(z.read(n))

    # Run with -S (no site) and cwd in an isolated dir; scr is not on the path.
    good = subprocess.run(
        [sys.executable, "-S", "verify.py", "bundle.json", "--key", KEY.hex()],
        cwd=str(workdir), capture_output=True, text=True)
    assert good.returncode == 0, good.stdout + good.stderr
    assert "VERIFIED" in good.stdout

    # Tamper the extracted bundle → standalone verifier must fail (exit 1).
    b = json.loads((workdir / "bundle.json").read_text())
    b["events"][0]["hash"] = "00" * 32
    (workdir / "bundle.json").write_text(json.dumps(b, sort_keys=True, separators=(",", ":")))
    bad = subprocess.run(
        [sys.executable, "-S", "verify.py", "bundle.json", "--key", KEY.hex()],
        cwd=str(workdir), capture_output=True, text=True)
    assert bad.returncode == 1
    assert "TAMPERED" in bad.stdout


def test_unsealed_session_reports_na(tmp_path):
    store = Store(str(tmp_path / "u.db"))
    sid = store.create_session()
    tool = ToolSpec("noop", lambda a: "ok", idempotent=True)
    Kernel(store, MockAdapter([ModelResponse("done")]),
           {"noop": tool}, CapabilityManifest()).run(sid, "hi")
    out = str(tmp_path / "u.scevidence")
    export_bundle(store, sid, KEY, out)  # no seal_on_close
    report = verify_bundle(out, KEY)
    assert report.ok                      # chain + bundle seal still valid
    assert report.report["session_seal_ok"] is None  # n/a (unsealed)
