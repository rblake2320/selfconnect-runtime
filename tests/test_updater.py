"""Staged updater: switch on healthy probe, roll back on failure."""
from scr.updater import Updater


def test_successful_update_switches(tmp_path):
    u = Updater(str(tmp_path))
    u.install_initial("1.0.0")
    assert u.active() == "1.0.0"
    res = u.apply("1.1.0", health_probe=lambda staged: True)
    assert res.ok and res.active_version == "1.1.0"
    assert u.active() == "1.1.0"


def test_failing_probe_rolls_back(tmp_path):
    u = Updater(str(tmp_path))
    u.install_initial("1.0.0")
    res = u.apply("1.1.0", health_probe=lambda staged: False)
    assert not res.ok and res.rolled_back
    assert u.active() == "1.0.0"           # never switched to the bad build


def test_throwing_probe_treated_as_unhealthy(tmp_path):
    u = Updater(str(tmp_path))
    u.install_initial("1.0.0")

    def boom(staged):
        raise RuntimeError("probe crashed")

    res = u.apply("1.1.0", health_probe=boom)
    assert not res.ok and res.rolled_back
    assert u.active() == "1.0.0"


def test_staged_dir_created_for_offline_payload(tmp_path):
    u = Updater(str(tmp_path))
    u.install_initial("1.0.0")
    staged = u.stage("2.0.0")
    import os
    assert os.path.isdir(staged)           # offline update payload unpacks here
