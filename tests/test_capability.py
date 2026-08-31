import os

import pytest

from scr.capability import (
    CapabilityDenied,
    CapabilityManifest,
    ExecRule,
    attenuate,
    resolve_within,
)


def manifest(tmp_path, **kw):
    defaults = dict(
        tools=frozenset({"read_file"}),
        fs_read_roots=(str(tmp_path / "ws"),),
        fs_write_roots=(str(tmp_path / "ws" / "out"),),
        net_hosts=frozenset({"api.internal"}),
        exec_rules=(ExecRule("git", r"status.*"),),
        max_budget_usd=10.0,
    )
    defaults.update(kw)
    return CapabilityManifest(**defaults)


# ---------------------------------------------------------- deny-by-default
def test_empty_manifest_denies_everything(tmp_path):
    m = CapabilityManifest()
    with pytest.raises(CapabilityDenied):
        m.check_tool("anything")
    with pytest.raises(CapabilityDenied):
        m.check_read(str(tmp_path / "f"))
    with pytest.raises(CapabilityDenied):
        m.check_write(str(tmp_path / "f"))
    with pytest.raises(CapabilityDenied):
        m.check_net("example.com")
    with pytest.raises(CapabilityDenied):
        m.check_exec("bash", ["-c", "id"])


def test_undeclared_tool_denied(tmp_path):
    m = manifest(tmp_path)
    with pytest.raises(CapabilityDenied):
        m.check_tool("shell")
    m.check_tool("read_file")  # declared → allowed


# ------------------------------------------------------------ path escapes
def test_dotdot_traversal_denied(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (tmp_path / "secret.txt").write_text("s")
    m = manifest(tmp_path)
    with pytest.raises(CapabilityDenied):
        m.check_read(str(ws / ".." / "secret.txt"))


def test_deep_traversal_denied(tmp_path):
    ws = tmp_path / "ws"
    (ws / "a" / "b").mkdir(parents=True)
    m = manifest(tmp_path)
    with pytest.raises(CapabilityDenied):
        m.check_read(str(ws / "a" / "b" / ".." / ".." / ".." / ".." / "etc" / "passwd"))


def test_prefix_sibling_not_confused_with_root(tmp_path):
    """/x/ws-evil must NOT pass a containment check for root /x/ws."""
    ws = tmp_path / "ws"
    ws.mkdir()
    evil = tmp_path / "ws-evil"
    evil.mkdir()
    (evil / "f.txt").write_text("x")
    m = manifest(tmp_path)
    with pytest.raises(CapabilityDenied):
        m.check_read(str(evil / "f.txt"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_symlink_escape_denied(tmp_path):
    """Symlink INSIDE the allowed root pointing OUTSIDE it — resolved path is
    checked, so this must be denied."""
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    link = ws / "innocent.txt"
    link.symlink_to(outside)
    m = manifest(tmp_path)
    with pytest.raises(CapabilityDenied):
        m.check_read(str(link))


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_symlinked_directory_escape_denied(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()
    (outside_dir / "f.txt").write_text("secret")
    (ws / "sub").symlink_to(outside_dir)
    m = manifest(tmp_path)
    with pytest.raises(CapabilityDenied):
        m.check_read(str(ws / "sub" / "f.txt"))


def test_legit_path_inside_root_allowed(tmp_path):
    ws = tmp_path / "ws"
    (ws / "sub").mkdir(parents=True)
    f = ws / "sub" / "ok.txt"
    f.write_text("fine")
    m = manifest(tmp_path)
    assert m.check_read(str(f)) == os.path.realpath(str(f))


def test_write_root_narrower_than_read_root(tmp_path):
    ws = tmp_path / "ws"
    (ws / "out").mkdir(parents=True)
    m = manifest(tmp_path)
    m.check_write(str(ws / "out" / "artifact.md"))
    with pytest.raises(CapabilityDenied):
        m.check_write(str(ws / "not-out.md"))


# ------------------------------------------------- ADS (Windows semantics)
def test_ads_syntax_rejected_windows_semantics(tmp_path):
    with pytest.raises(CapabilityDenied):
        resolve_within(r"C:\ws\file.txt:hidden", [r"C:\ws"], windows=True)


def test_ads_in_middle_component_rejected(tmp_path):
    with pytest.raises(CapabilityDenied):
        resolve_within(r"C:\ws\dir:stream\file.txt", [r"C:\ws"], windows=True)


def test_drive_letter_colon_is_legal_windows_semantics(tmp_path):
    # Only asserts ADS rejection logic doesn't false-positive on 'C:'.
    from scr.capability import _reject_ads
    _reject_ads(r"C:\ws\file.txt", windows=True)  # no raise


# ------------------------------------------------------------ exec rules
def test_exec_allowlisted_binary_and_args(tmp_path):
    m = manifest(tmp_path)
    m.check_exec("git", ["status", "--short"])


def test_exec_wrong_binary_denied(tmp_path):
    m = manifest(tmp_path)
    with pytest.raises(CapabilityDenied):
        m.check_exec("bash", ["status"])


def test_exec_arg_injection_denied(tmp_path):
    """Arg pattern is fullmatch — 'status; rm -rf /' must not slide through."""
    m = manifest(tmp_path, exec_rules=(ExecRule("git", r"status( --short)?"),))
    with pytest.raises(CapabilityDenied):
        m.check_exec("git", ["status;", "rm", "-rf", "/"])
    with pytest.raises(CapabilityDenied):
        m.check_exec("git", ["status", "&&", "curl", "evil"])


def test_net_host_allowlist(tmp_path):
    m = manifest(tmp_path)
    m.check_net("api.internal")
    with pytest.raises(CapabilityDenied):
        m.check_net("exfil.example.com")


# ------------------------------------------------------------ attenuation
def test_attenuation_is_intersection(tmp_path):
    parent = manifest(tmp_path, tools=frozenset({"a", "b"}))
    child = manifest(tmp_path, tools=frozenset({"b", "c"}))
    eff = attenuate(parent, child)
    assert eff.tools == frozenset({"b"})


def test_child_cannot_widen_fs_roots(tmp_path):
    ws = tmp_path / "ws"
    (ws / "sub").mkdir(parents=True)
    parent = manifest(tmp_path, fs_read_roots=(str(ws),))
    child = manifest(tmp_path, fs_read_roots=(str(ws / "sub"), str(tmp_path)))
    eff = attenuate(parent, child)
    assert eff.fs_read_roots == (str(ws / "sub"),)  # tmp_path (wider) dropped


def test_child_cannot_widen_exec(tmp_path):
    parent = manifest(tmp_path, exec_rules=(ExecRule("git", r"status.*"),))
    child = manifest(
        tmp_path, exec_rules=(ExecRule("git", r"status.*"), ExecRule("bash", r".*"))
    )
    eff = attenuate(parent, child)
    assert eff.exec_rules == (ExecRule("git", r"status.*"),)


def test_budget_takes_minimum(tmp_path):
    parent = manifest(tmp_path, max_budget_usd=10.0)
    child = manifest(tmp_path, max_budget_usd=50.0)
    assert attenuate(parent, child).max_budget_usd == 10.0


def test_attenuation_chain_is_monotonic(tmp_path):
    """Three-level delegation: grandchild ⊆ child ⊆ parent, always."""
    ws = tmp_path / "ws"
    (ws / "a").mkdir(parents=True)
    root = manifest(tmp_path, tools=frozenset({"a", "b", "c"}), fs_read_roots=(str(ws),))
    mid = manifest(tmp_path, tools=frozenset({"b", "c", "d"}), fs_read_roots=(str(ws / "a"),))
    leaf = manifest(tmp_path, tools=frozenset({"c", "d", "e"}), fs_read_roots=(str(ws),))
    eff_mid = attenuate(root, mid)
    eff_leaf = attenuate(eff_mid, leaf)
    assert eff_leaf.tools <= eff_mid.tools <= root.tools
    # leaf asked for ws (wider than mid's ws/a) — must be dropped
    assert eff_leaf.fs_read_roots == ()


# ---------------------------------------- exec arg-injection (newline vector)
def test_exec_dotstar_rejects_newline_injected_args(tmp_path):
    """A `.*` arg pattern must NOT authorize a multi-line / newline-injected
    argument — `re.fullmatch('.*', ...)` excludes newlines by default, so an
    attacker who smuggles a newline into an exec arg is denied. Rule authors
    must opt into multi-line args explicitly with (?s)."""
    m = manifest(tmp_path, exec_rules=(ExecRule("python", r".*"),))
    m.check_exec("python", ["-c", "print(1)"])                     # single line OK
    with pytest.raises(CapabilityDenied):
        m.check_exec("python", ["-c", "print(1)\nimport os; os.system('x')"])
    # explicit opt-in matches
    m2 = manifest(tmp_path, exec_rules=(ExecRule("python", r"(?s).*"),))
    m2.check_exec("python", ["-c", "line1\nline2"])                # now allowed
