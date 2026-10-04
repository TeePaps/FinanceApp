#!/usr/bin/env bash
# Double-click installer for FinanceApp (macOS).
#
# Runs install.sh: the copy next to this file if there is one (local testing),
# otherwise the one published with the latest release. Arguments and the
# FINANCEAPP_* environment overrides documented in install.sh pass through.
#
# Not signed: the first time, right-click -> Open (or System Settings ->
# Privacy & Security -> Open Anyway). If macOS says you lack permission to
# run it, run:  chmod +x ~/Downloads/Install-FinanceApp.command
set -euo pipefail

REPO="TeePaps/FinanceApp"
SH_URL="${FINANCEAPP_INSTALL_SH_URL:-https://github.com/${REPO}/releases/latest/download/install.sh}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HOME"

echo "=============================================="
echo " FinanceApp installer"
echo "=============================================="
echo

status=0
if [ -f "$HERE/install.sh" ]; then
  bash "$HERE/install.sh" "$@" || status=$?
else
  tmp="$(mktemp "${TMPDIR:-/tmp}/financeapp-install.XXXXXX")"
  if curl -fsSL "$SH_URL" -o "$tmp"; then
    bash "$tmp" "$@" || status=$?
  else
    echo "ERROR: could not download $SH_URL"
    status=1
  fi
  rm -f "$tmp"
fi

echo
if [ "$status" -eq 0 ]; then
  echo "Done. Open FinanceApp from ~/Applications (or Launchpad) any time."
else
  echo "Installation failed (exit $status). Scroll up for details."
fi
if [ -t 0 ] && [ -z "${FINANCEAPP_NO_PAUSE:-}" ]; then
  read -r -p "Press Return to close this window..." _ || true
fi
exit "$status"
