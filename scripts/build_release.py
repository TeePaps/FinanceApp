#!/usr/bin/env python3
"""Build FinanceApp release assets (stdlib only; works with macOS system python3).

Usage:
    python3 scripts/build_release.py X.Y.Z [--out dist/] [--ref HEAD]

Produces in the output directory:
    FinanceApp-X.Y.Z.zip      top-level folder FinanceApp-X.Y.Z/
    installer.py, install.sh, install.ps1,
    Install-FinanceApp.command, Install-FinanceApp.bat   (copied from installer/ if present)
    Install-FinanceApp-mac.zip  Install-FinanceApp.command with mode 0755 stored in the zip:
                              a browser-downloaded .command loses its execute bit, but
                              Archive Utility restores it from the zip (README links this)
    SHA256SUMS.txt            "sha256  filename" for every asset above

The zip is built from the committed tree at --ref (default HEAD) via `git archive`,
not from the working tree. That means:
  * only tracked files are shipped, so untracked config.yaml / data_private/ never leak;
  * data_public/public.db is the committed seed blob. setup() marks it skip-worktree, so the
    working-tree copy is the developer's live database and must NOT be shipped;
  * uncommitted edits are not included (a warning is printed if the tree is dirty).

Installer assets are copied from the working-tree installer/ directory (they may not be
committed yet while being developed); missing ones are skipped with a warning.

version.py at --ref must declare __version__ == VERSION, or the build fails.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import re
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Top-level directories excluded from the release zip (see docs/installer-updater-design.md).
EXCLUDED_DIRS = {
    "data_private",
    "_ARCHIVE",
    "_IDEAS",
    "backup",
    "playwright-mcp",
    "venv",
    "logs",
    "requirements",  # requirements-gathering spec dir (requirements.txt is kept)
    ".github",
    ".claude",
}
# Directory names excluded at any depth.
EXCLUDED_ANY_DEPTH = {"__pycache__"}
# Individual files excluded at any depth (belt and braces: never ship user config/secrets).
EXCLUDED_FILES = {"config.yaml", ".DS_Store"}
EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".db-wal", ".db-shm", ".db-journal")

INSTALLER_ASSETS = [
    "installer.py",
    "install.sh",
    "install.ps1",
    "Install-FinanceApp.command",
    "Install-FinanceApp.bat",
]
EXECUTABLE_ASSETS = {"install.sh", "Install-FinanceApp.command"}
MAC_ZIP = "Install-FinanceApp-mac.zip"
MAC_COMMAND = "Install-FinanceApp.command"

REQUIRED_IN_ZIP = ["app.py", "version.py", "paths.py", "config.defaults.yaml",
                   "requirements.txt", "restart_server.py", "data_public/public.db"]

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?$")


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def warn(msg: str) -> None:
    print(f"warning: {msg}", file=sys.stderr)


def git(*args: str, binary: bool = False):
    result = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True)
    if result.returncode != 0:
        die(f"git {' '.join(args)} failed: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout if binary else result.stdout.decode()


def is_excluded(path: str) -> bool:
    parts = path.split("/")
    if parts[0] in EXCLUDED_DIRS:
        return True
    if any(p in EXCLUDED_ANY_DEPTH for p in parts[:-1]):
        return True
    name = parts[-1]
    return name in EXCLUDED_FILES or name.endswith(EXCLUDED_SUFFIXES)


def committed_version(ref: str) -> str:
    text = git("show", f"{ref}:version.py")
    m = re.search(r"""^__version__\s*=\s*["']([^"']+)["']""", text, re.M)
    if not m:
        die(f"could not find __version__ in {ref}:version.py")
    return m.group(1)


def build_zip(version: str, ref: str, out_dir: Path) -> Path:
    prefix = f"FinanceApp-{version}"
    zip_path = out_dir / f"{prefix}.zip"
    tar_bytes = git("archive", "--format=tar", ref, binary=True)

    included, excluded = [], 0
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as tar, \
            zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for member in tar:
            if not member.isfile():
                continue  # directories are implied; symlinks are not expected in this repo
            if is_excluded(member.name):
                excluded += 1
                continue
            data = tar.extractfile(member).read()
            info = zipfile.ZipInfo(f"{prefix}/{member.name}",
                                   date_time=time.localtime(member.mtime)[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3  # Unix, so permission bits are honoured on extract
            info.external_attr = ((member.mode & 0o755) | 0o100000) << 16
            zf.writestr(info, data)
            included.append(member.name)

    missing = [p for p in REQUIRED_IN_ZIP if p not in included]
    if missing:
        if zip_path.exists():
            zip_path.unlink()
        die(f"release zip would be missing required files: {', '.join(missing)}")
    print(f"  {zip_path.name}: {len(included)} files ({excluded} excluded)")
    return zip_path


def copy_installer_assets(out_dir: Path, src_dir: Path) -> list[Path]:
    copied = []
    for name in INSTALLER_ASSETS:
        src = src_dir / name
        if not src.is_file():
            warn(f"{src_dir / name} not found; skipping asset")
            continue
        dest = out_dir / name
        shutil.copyfile(src, dest)
        dest.chmod(0o755 if name in EXECUTABLE_ASSETS else 0o644)
        copied.append(dest)
        print(f"  {name}")
    return copied


def build_mac_zip(out_dir: Path) -> Path | None:
    """Zip Install-FinanceApp.command with its 0755 mode recorded (Unix
    create_system + external_attr), so double-click unzip yields a runnable file."""
    src = out_dir / MAC_COMMAND
    if not src.is_file():
        warn(f"{MAC_COMMAND} not built; skipping {MAC_ZIP}")
        return None
    zip_path = out_dir / MAC_ZIP
    info = zipfile.ZipInfo(MAC_COMMAND, date_time=time.localtime()[:6])
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3  # Unix: Archive Utility / unzip honour the mode bits
    info.external_attr = (0o100755 & 0xFFFF) << 16
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(info, src.read_bytes())
    print(f"  {MAC_ZIP}")
    return zip_path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_checksums(out_dir: Path, assets: list[Path]) -> Path:
    sums = out_dir / "SHA256SUMS.txt"
    lines = [f"{sha256(p)}  {p.name}\n" for p in sorted(assets, key=lambda p: p.name)]
    sums.write_bytes("".join(lines).encode("utf-8"))  # LF endings on every OS
    print(f"  {sums.name}: {len(lines)} entries")
    return sums


def main() -> None:
    parser = argparse.ArgumentParser(description="Build FinanceApp release assets.")
    parser.add_argument("version", help="release version, e.g. 1.2.3 or 1.3.0-beta.1 (no leading v)")
    parser.add_argument("--out", default="dist", help="output directory (default: dist/)")
    parser.add_argument("--ref", default="HEAD", help="git ref to package (default: HEAD)")
    parser.add_argument("--installer-dir", default=str(REPO_ROOT / "installer"),
                        help="where to take installer assets from (default: <repo>/installer)")
    args = parser.parse_args()

    version = args.version[1:] if args.version.startswith("v") else args.version
    if not VERSION_RE.match(version):
        die(f"invalid version {args.version!r}; expected X.Y.Z or X.Y.Z-suffix")

    declared = committed_version(args.ref)
    if declared != version:
        die(f"version.py at {args.ref} declares {declared!r}, expected {version!r}")

    if git("status", "--porcelain", "--untracked-files=no").strip():
        warn("working tree has uncommitted changes; they are NOT included (building from "
             f"{args.ref})")

    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = Path.cwd() / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # Remove stale assets from a previous build so SHA256SUMS matches the directory contents.
    for stale in [*out_dir.glob("FinanceApp-*.zip"), out_dir / "SHA256SUMS.txt",
                  *(out_dir / n for n in INSTALLER_ASSETS), out_dir / MAC_ZIP]:
        if stale.is_file():
            stale.unlink()

    print(f"Building FinanceApp {version} from {args.ref} -> {out_dir}")
    assets = [build_zip(version, args.ref, out_dir)]
    assets += copy_installer_assets(out_dir, Path(args.installer_dir))
    mac_zip = build_mac_zip(out_dir)
    if mac_zip:
        assets.append(mac_zip)
    write_checksums(out_dir, assets)
    print("Done.")


if __name__ == "__main__":
    main()
