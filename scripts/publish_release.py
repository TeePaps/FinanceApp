#!/usr/bin/env python3
"""Build and publish a GitHub release from this machine (no GitHub Actions needed).

Usage:
    python3 scripts/publish_release.py X.Y.Z [--out DIR] [--dry-run]

Builds the release assets for the existing tag vX.Y.Z with build_release.py, then
creates the GitHub release with the GitHub CLI (`gh`, logged in via `gh auth login`)
and uploads every asset. Pre-release versions (X.Y.Z-beta.1) are published as
pre-releases. If the release already exists (e.g. the workflow published it), the
assets are re-uploaded with --clobber instead.

Use this when the tag-triggered workflow can't run; scripts/release.py --publish
calls it after pushing.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REPO = "TeePaps/FinanceApp"


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    if check and result.returncode != 0:
        die(f"{' '.join(cmd[:3])} failed: {(result.stderr or result.stdout).strip()}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish a GitHub release using the gh CLI.")
    parser.add_argument("version", help="version whose tag vX.Y.Z already exists and is pushed")
    parser.add_argument("--out", help="asset directory (default dist/vX.Y.Z)")
    parser.add_argument("--dry-run", action="store_true", help="build assets but do not publish")
    args = parser.parse_args()

    version = args.version[1:] if args.version.startswith("v") else args.version
    tag = f"v{version}"
    out = Path(args.out) if args.out else REPO_ROOT / "dist" / tag

    gh = shutil.which("gh")
    if not gh and not args.dry_run:
        die("GitHub CLI not found. Install it with `brew install gh` (or winget install GitHub.cli), "
            "then run `gh auth login`.")
    if gh and not args.dry_run and run([gh, "auth", "status"], check=False).returncode != 0:
        die("gh is not logged in. Run `gh auth login` first.")

    if run(["git", "ls-remote", "--tags", "origin", f"refs/tags/{tag}"]).stdout.strip() == "":
        die(f"tag {tag} is not on origin; push it first (scripts/release.py --push)")

    run([sys.executable, str(REPO_ROOT / "scripts" / "build_release.py"), version,
         "--ref", tag, "--out", str(out)])
    assets = sorted(p for p in out.iterdir() if p.is_file())
    print(f"Built {len(assets)} assets in {out}")

    if args.dry_run:
        for p in assets:
            print(f"  {p.name}")
        print("[dry-run] not published")
        return

    exists = run([gh, "release", "view", tag, "--repo", REPO], check=False).returncode == 0
    files = [str(p) for p in assets]
    if exists:
        run([gh, "release", "upload", tag, *files, "--repo", REPO, "--clobber"])
        print(f"Release {tag} already existed; re-uploaded assets.")
    else:
        cmd = [gh, "release", "create", tag, *files, "--repo", REPO, "--title", tag,
               "--generate-notes", "--verify-tag"]
        if "-" in version:
            cmd.append("--prerelease")
        else:
            cmd.append("--latest")
        run(cmd)
        print(f"Published https://github.com/{REPO}/releases/tag/{tag}")


if __name__ == "__main__":
    main()
