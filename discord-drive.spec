# PyInstaller build for the standalone drive -- the one that runs without
# Docker, on SQLite, out of a per-user data directory.
#
#   pyinstaller discord-drive.spec
#
# The result is dist/discord-drive[.exe]: Python, the server, and the file
# manager in one file. It is still a *server* -- SFTP on 2222 and a web UI on
# 8080 -- with no MongoDB and no container behind it. `src/standalone.py` says
# what it does on first run.
#
# Build the client first, or the binary will serve the "not built yet" page:
#
#   cd client/app && npm install && npm run build
#
# PyInstaller only builds for the platform it runs on, exactly as
# electron-builder does (CONTRIBUTING.md says so for both). Windows,
# macOS and Linux each need a build on that platform.

import os

block_cipher = None

# The built file manager, mounted where `standalone.bundled_client()` looks.
# Absent rather than fatal if it was never built: `web.py` already serves a
# page saying so, and the SFTP side does not depend on it at all.
client = os.path.join("client", "app", "dist")
datas = [(client, "web")] if os.path.isdir(client) else []

analysis = Analysis(
    ["src/standalone.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    # Reached only through `Database.connect`, which picks a backend at
    # runtime, so the module scanner cannot see them from the import graph.
    # Both are listed even though this build only ever opens SQLite: `db.py`
    # imports motor at module level for the compose path, and dropping that
    # import to slim the bundle would mean restructuring a file both
    # deployments share.
    hiddenimports=[
        "motor.motor_asyncio",
        "pymongo",
        "src.sqlitedb",
    ],
    hookspath=[],
    runtime_hooks=[],
    # Test-only, and pulling them in would put pytest inside a user-facing
    # binary.
    excludes=["pytest", "pytest_asyncio", "pyflakes"],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(analysis.pure, analysis.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.zipfiles,
    analysis.datas,
    name="discord-drive",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # A console build on purpose. Without a password on the environment or in
    # a file, `standalone.resolve_password()` asks for one here -- which is
    # what keeps it off the disk that holds the database. A windowed build
    # would have nowhere to ask and nothing to print the first-run message to.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
