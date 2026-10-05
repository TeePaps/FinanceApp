#!/usr/bin/env python3
"""Cut a FinanceApp release: bump version.py, commit, tag, (optionally) push.

Usage:
    python3 scripts/release.py X.Y.Z [--dry-run] [--push] [--publish]

Steps:
  1. Require a clean working tree (tracked files) on branch main.
  2. Write __version__ = "X.Y.Z" to version.py.
  3. Commit "Release vX.Y.Z" and create annotated tag vX.Y.Z.
  4. With --push: git push origin main && git push origin vX.Y.Z
     Without --push: print those commands. Pushing the tag triggers
     .github/workflows/release.yml, which builds and publishes the GitHub release.
  5. With --publish (implies --push): also build and publish the release from this
     machine via scripts/publish_release.py (gh CLI), for when Actions can't run.

--dry-run performs the checks and prints what would happen without changing anything.
Pre-release versions (X.Y.Z-beta.1) are published as GitHub pre-releases.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = REPO_ROOT / "version.py"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?$")
VERSION_LINE_RE = re.compile(r"""^__version__\s*=\s*["']([^"']+)["']""", re.M)
TRAILER = "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def git(*args: str, check: bool = True) -> str:
    result = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True)
    if check and result.returncode != 0:
        die(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def version_key(v: str):
    core, _, pre = v.partition("-")
    # A pre-release sorts before its final release.
    return tuple(int(x) for x in core.split(".")), pre == "", pre


def main() -> None:
    parser = argparse.ArgumentParser(description="Bump version, commit, and tag a release.")
    parser.add_argument("version", help="new version, e.g. 1.2.3 or 1.3.0-beta.1 (no leading v)")
    parser.add_argument("--dry-run", action="store_true", help="check and print; change nothing")
    parser.add_argument("--push", action="store_true", help="push main and the tag to origin")
    parser.add_argument("--publish", action="store_true",
                        help="push, then publish the GitHub release locally with gh")
    args = parser.parse_args()

    version = args.version[1:] if args.version.startswith("v") else args.version
    if not VERSION_RE.match(version):
        die(f"invalid version {args.version!r}; expected X.Y.Z or X.Y.Z-suffix")
    tag = f"v{version}"
    if args.publish:
        args.push = True

    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if branch != "main":
        die(f"releases are cut from main (currently on {branch!r})")
    if git("status", "--porcelain", "--untracked-files=no"):
        die("working tree has uncommitted changes to tracked files; commit or stash them first")
    if git("rev-parse", "-q", "--verify", f"refs/tags/{tag}", check=False):
        die(f"tag {tag} already exists")

    text = VERSION_FILE.read_text(encoding="utf-8")
    m = VERSION_LINE_RE.search(text)
    if not m:
        die("could not find __version__ in version.py")
    current = m.group(1)
    if VERSION_RE.match(current) and version_key(version) < version_key(current):
        die(f"new version {version} is lower than current {current}")
    new_text = text[:m.start()] + f'__version__ = "{version}"' + text[m.end():]

    message = f"Release {tag}\n\n{TRAILER}\n"
    push_cmds = ["git push origin main", f"git push origin {tag}"]
    kind = "pre-release" if "-" in version else "release"

    print(f"Release {current} -> {version} ({kind}) on main")
    if args.dry_run:
        print("[dry-run] would write version.py:", f'__version__ = "{version}"'
              + ("" if current != version else "  (unchanged)"))
        print(f"[dry-run] would commit: {message.splitlines()[0]!r} (+ Co-Authored-By trailer)")
        print(f"[dry-run] would tag: {tag} (annotated)")
        print(f"[dry-run] would {'run' if args.push else 'print'}:")
        for cmd in push_cmds:
            print(f"    {cmd}")
        return

    if new_text != text:
        VERSION_FILE.write_text(new_text, encoding="utf-8")
        git("add", "version.py")
        git("commit", "-m", message)
    else:
        # version.py already at this version (e.g. first release): tag an explicit release commit.
        git("commit", "--allow-empty", "-m", message)
    git("tag", "-a", tag, "-m", f"Release {tag}")
    print(f"Committed and tagged {tag} at {git('rev-parse', '--short', 'HEAD')}")

    if args.push:
        git("push", "origin", "main")
        git("push", "origin", tag)
        print(f"Pushed main and {tag}.")
        if args.publish:
            result = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "publish_release.py"),
                                     version], cwd=REPO_ROOT)
            if result.returncode != 0:
                die(f"publishing failed; retry with: python3 scripts/publish_release.py {version}")
        else:
            print("The release workflow will publish the GitHub release "
                  f"(or run: python3 scripts/publish_release.py {version}).")
    else:
        print("Not pushed. To publish, run:")
        for cmd in push_cmds:
            print(f"    {cmd}")


if __name__ == "__main__":
    main()
