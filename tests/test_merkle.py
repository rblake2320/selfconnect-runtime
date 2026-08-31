"""Merkle root determinism and sensitivity."""
from scr.merkle import merkle_root


def test_empty_is_zero():
    assert merkle_root({}) == "0" * 64


def test_order_independent():
    a = {"a.txt": "aa" * 32, "b.txt": "bb" * 32, "c.txt": "cc" * 32}
    b = {"c.txt": "cc" * 32, "a.txt": "aa" * 32, "b.txt": "bb" * 32}
    assert merkle_root(a) == merkle_root(b)


def test_single_leaf_change_changes_root():
    base = {"a.txt": "aa" * 32, "b.txt": "bb" * 32}
    changed = {"a.txt": "aa" * 32, "b.txt": "bc" * 32}
    assert merkle_root(base) != merkle_root(changed)


def test_added_file_changes_root():
    base = {"a.txt": "aa" * 32}
    more = {"a.txt": "aa" * 32, "b.txt": "bb" * 32}
    assert merkle_root(base) != merkle_root(more)


def test_odd_leaf_count_is_stable():
    files = {f"f{i}.txt": (f"{i:02x}" * 32) for i in range(5)}
    r1 = merkle_root(files)
    r2 = merkle_root(dict(reversed(list(files.items()))))
    assert r1 == r2 and len(r1) == 64
