"""scr doctor full check set (design §3.8): integrity, disk, package
signatures (tamper detection), lock, models, clock."""
import os
import zipfile

from scr.cli import main
from scr.signer import sign_package
from scr.signing import generate_keypair


def _pkg(tmp_path, name="ent"):
    src = tmp_path / "src" / "agents"; src.mkdir(parents=True)
    (src / "a.yaml").write_bytes(b"role: lead\n")
    priv, pub = generate_keypair()
    out = str(tmp_path / f"{name}.scpkg")
    sign_package(str(tmp_path / "src"), out, name, "1.0.0", priv)
    trust = tmp_path / "trust.txt"; trust.write_text(pub + "\n")
    return out, str(trust)


def test_doctor_all_ok(tmp_path, capsys):
    home = str(tmp_path / "home")
    main(["--home", home, "init"])
    pkg, trust = _pkg(tmp_path)
    main(["--home", home, "package", "install", pkg, "--trust", trust])
    capsys.readouterr()
    rc = main(["--home", home, "doctor"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "db_integrity" in out and "OK" in out
    assert "disk_headroom" in out
    assert "pkg:ent" in out and "intact" in out
    assert "lock_health" in out and "clock" in out


def test_doctor_flags_tampered_installed_package(tmp_path, capsys):
    home = str(tmp_path / "home")
    main(["--home", home, "init"])
    pkg, trust = _pkg(tmp_path)
    main(["--home", home, "package", "install", pkg, "--trust", trust])
    # tamper the STORED package on disk
    from scr.registry import PackageRegistry
    from scr.signing import Keystore
    reg = PackageRegistry(home, Keystore())
    stored = reg.get("ent").path
    tampered = stored + ".t"
    with zipfile.ZipFile(stored) as zin, zipfile.ZipFile(tampered, "w") as zout:
        for it in zin.namelist():
            d = zin.read(it)
            if it == "agents/a.yaml":
                d += b"# evil\n"
            zout.writestr(it, d)
    os.replace(tampered, stored)
    capsys.readouterr()
    rc = main(["--home", home, "doctor"])
    out = capsys.readouterr().out
    assert rc == 1                       # doctor fails
    assert "FAIL" in out and "pkg:ent" in out
