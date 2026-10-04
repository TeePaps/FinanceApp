#!/usr/bin/env bash
# FinanceApp installer bootstrap (macOS / Linux).
#
#   curl -fsSL https://raw.githubusercontent.com/TeePaps/FinanceApp/main/install.sh | bash
#   curl -fsSL .../install.sh | bash -s -- --port 9000 --import-from ~/Apps/Claude/FinanceApp
#
# Ensures uv (official installer, PATH left untouched), downloads installer.py
# from the latest release, and runs it with uv-managed Python 3.12. All
# arguments are passed to installer.py (see installer.py --help).
#
# Testing / offline overrides (environment):
#   FINANCEAPP_INSTALLER_PY   local installer.py to run instead of downloading
#   FINANCEAPP_INSTALLER_URL  URL (or file://) to download installer.py from
#   FINANCEAPP_INSTALLER_ZIP  local release zip (read by installer.py as --zip)
#   FINANCEAPP_UPDATE_FEED    release feed JSON (read by installer.py as --feed-url)
#   FINANCEAPP_UV             uv executable to use
set -euo pipefail

REPO="TeePaps/FinanceApp"
INSTALLER_URL="${FINANCEAPP_INSTALLER_URL:-https://github.com/${REPO}/releases/latest/download/installer.py}"

say() { printf '%s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

find_uv() {
  if [ -n "${FINANCEAPP_UV:-}" ] && [ -x "${FINANCEAPP_UV}" ]; then
    printf '%s\n' "${FINANCEAPP_UV}"; return 0
  fi
  if command -v uv >/dev/null 2>&1; then
    command -v uv; return 0
  fi
  local c
  for c in "${UV_INSTALL_DIR:-}/uv" "${XDG_BIN_HOME:-}/uv" "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
    if [ -x "$c" ]; then printf '%s\n' "$c"; return 0; fi
  done
  return 1
}

UV="$(find_uv || true)"
if [ -z "$UV" ]; then
  say "Installing uv (Python package manager) from astral.sh ..."
  command -v curl >/dev/null 2>&1 || fail "curl is required"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
  UV="$(find_uv || true)"
  [ -n "$UV" ] || fail "uv installation failed (expected ~/.local/bin/uv)"
fi
export FINANCEAPP_UV="$UV"
say "Using uv: $UV"

TMPDIR_FA="$(mktemp -d "${TMPDIR:-/tmp}/financeapp-bootstrap.XXXXXX")"
cleanup() { rm -rf "$TMPDIR_FA"; }
trap cleanup EXIT

if [ -n "${FINANCEAPP_INSTALLER_PY:-}" ]; then
  [ -f "$FINANCEAPP_INSTALLER_PY" ] || fail "FINANCEAPP_INSTALLER_PY not found: $FINANCEAPP_INSTALLER_PY"
  INSTALLER="$FINANCEAPP_INSTALLER_PY"
else
  INSTALLER="$TMPDIR_FA/installer.py"
  say "Downloading installer ..."
  curl -fsSL "$INSTALLER_URL" -o "$INSTALLER" || fail "could not download $INSTALLER_URL"
fi

# Only uv-managed Pythons: never use or modify the system Python.
export UV_PYTHON_PREFERENCE=only-managed
status=0
"$UV" run --no-project --python 3.12 "$INSTALLER" "$@" || status=$?
exit "$status"
