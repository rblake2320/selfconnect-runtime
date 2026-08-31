"""RBAC matrix — deny-by-default across the four roles."""
import pytest

from scr.rbac import AccessDenied, permitted, require


def test_viewer_cannot_run():
    assert not permitted("viewer", "run")
    with pytest.raises(AccessDenied):
        require("viewer", "run")


def test_auditor_reads_ledger_not_run():
    assert permitted("auditor", "read_ledger")
    assert permitted("auditor", "export_evidence")
    assert not permitted("auditor", "run")


def test_operator_runs_not_manage_tokens():
    assert permitted("operator", "run")
    assert permitted("operator", "approve")
    assert not permitted("operator", "manage_tokens")
    assert not permitted("operator", "install_package")


def test_admin_all():
    for action in ("run", "cancel", "approve", "read_ledger",
                   "manage_tokens", "install_package"):
        assert permitted("admin", action)


def test_unknown_role_and_none_denied():
    with pytest.raises(AccessDenied):
        require("wizard", "run")
    with pytest.raises(AccessDenied):
        require(None, "read_status")


def test_unknown_action_denied_by_default():
    assert not permitted("admin", "self_destruct")
