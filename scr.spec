# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['installers\\windows\\freeze\\entry_scr.py'],
    pathex=[],
    binaries=[],
    datas=[('C:\\dev\\selfconnect-runtime\\scr\\_evidence_verifier.py', 'scr'), ('C:\\dev\\selfconnect-runtime\\scr\\frameworks\\data', 'scr/frameworks/data')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='scr',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
