#!/bin/bash
# SPDX-License-Identifier: MIT
# Install or update cartolex for this user, on Linux or macOS, without administrator rights.
#
# Everything goes into one folder, ~/cartolex (CARTOLEX_HOME changes it): the Python
# environment, uv and the Python it downloads, their caches and install.log. The
# route: uv (installed into that folder unless one is on the PATH), then uv trusting
# the system's certificates (UV_NATIVE_TLS, for networks that inspect HTTPS), then the
# system's Python 3.10 or later. HTTPS_PROXY, HTTP_PROXY and NO_PROXY are followed.
#
# cartolex is installed with the t-SNE layout (the extra "tsne", openTSNE); where that
# cannot be installed (no wheel for this computer and no compiler), without it.
#
# For testing: CARTOLEX_WHEEL=/path/to/cartolex-*.whl (or a wheel next to this file)
# installs that file instead of the pinned release; CARTOLEX_ROUTE=system skips uv.
# Running this again updates the installation.
#
# The language models of English, French and Portuguese are installed; those of Spanish,
# German and Italian are offered at the end (each with its licence), or installed
# without asking with CARTOLEX_EXTRA_LANGUAGES="es de it".

set -u

PIN="@CARTOLEX_VERSION@"
MODELS="en fr pt"
EXTRA_MODELS="es de it"
UV_PYTHON_VERSION="3.12"

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="${CARTOLEX_HOME:-$HOME/cartolex}"
ENV_DIR="$ROOT/env"
LOG="$ROOT/install.log"

mkdir -p "$ROOT" || { echo "cannot create $ROOT"; exit 1; }
exec > >(tee -a "$LOG") 2>&1

say() { printf '%s\n' "$*"; }
pause() {
  if [ -t 0 ]; then
    read -r -p "Press Enter to close this window. " _ || true
  fi
}
step() { printf '\n== %s\n' "$*"; }
fail() {
  printf '\nThe installation stopped: %s\n' "$*"
  printf 'The whole log is in %s\n' "$LOG"
  pause
  exit 1
}

say ""
say "==== cartolex installer, $(date '+%Y-%m-%d %H:%M:%S'), $(uname -s) $(uname -m)"
say "folder: $ROOT"
for name in HTTPS_PROXY https_proxy HTTP_PROXY http_proxy NO_PROXY no_proxy; do
  [ -n "${!name:-}" ] && say "proxy setting in use: $name"
done

# macOS quarantines everything extracted from a downloaded archive, so a double-clicked
# .command can die with "is damaged and can't be opened", which right-click > Open does
# not lift (and which macOS 15 no longer lets anyone bypass). Running through `sh` is not
# subject to it (Gatekeeper gates execution, not reading): that is how the README tells
# people to start this. Clear the flag on this folder now, so it behaves normally after.
if [ "$(uname -s)" = "Darwin" ]; then
  xattr -dr com.apple.quarantine "$HERE" 2>/dev/null || true
fi

# What to install: the pinned release, or a wheel given for testing.
WHEEL="${CARTOLEX_WHEEL:-}"
if [ -z "$WHEEL" ]; then
  for candidate in "$HERE"/cartolex-*.whl; do
    [ -f "$candidate" ] && WHEEL="$candidate"
  done
fi
if [ -n "$WHEEL" ]; then
  [ -f "$WHEEL" ] || fail "the wheel $WHEEL does not exist"
  WHEEL="$(cd "$(dirname "$WHEEL")" && pwd)/$(basename "$WHEEL")"
  SPEC="$WHEEL"
  FULL_SPEC="cartolex[tsne] @ file://$WHEEL"
  say "installing the wheel $WHEEL"
elif [ "${PIN#@}" != "$PIN" ]; then
  fail "this copy of the installer names no version: use the kit of a release"
else
  SPEC="cartolex==$PIN"
  FULL_SPEC="cartolex[tsne]==$PIN"
  say "installing cartolex $PIN"
fi

export UV_CACHE_DIR="$ROOT/cache/uv"
export UV_PYTHON_INSTALL_DIR="$ROOT/python"
export UV_PYTHON_PREFERENCE="only-managed"
export UV_NO_MODIFY_PATH=1
export PIP_CACHE_DIR="$ROOT/cache/pip"
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PYTHONUTF8=1

env_python() { printf '%s' "$ENV_DIR/bin/python"; }
env_works() { [ -x "$(env_python)" ] && "$(env_python)" -c "import sys" >/dev/null 2>&1; }

# ── uv ───────────────────────────────────────────────────────────────────────

find_uv() {
  if [ -x "$ROOT/uv/uv" ]; then printf '%s' "$ROOT/uv/uv"; return 0; fi
  command -v uv 2>/dev/null
}

get_uv() {
  UV="$(find_uv)" && { say "uv: $UV"; return 0; }
  say "downloading uv from astral.sh"
  local script="$ROOT/uv-install.sh"
  if command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh -o "$script" || { say "astral.sh cannot be reached (curl)"; return 1; }
  elif command -v wget >/dev/null 2>&1; then
    wget -q https://astral.sh/uv/install.sh -O "$script" || { say "astral.sh cannot be reached (wget)"; return 1; }
  else
    say "neither curl nor wget is available to download uv"
    return 1
  fi
  UV_INSTALL_DIR="$ROOT/uv" sh "$script" || { say "the uv installer failed"; return 1; }
  rm -f "$script"
  UV="$ROOT/uv/uv"
  [ -x "$UV" ] || { say "uv is not where it was installed"; return 1; }
  say "uv: $UV"
}

install_with_uv() {
  if ! env_works; then
    rm -rf "$ENV_DIR"
    "$UV" venv --quiet --python "$UV_PYTHON_VERSION" "$ENV_DIR" || return 1
  fi
  if ! "$UV" pip install --python "$(env_python)" --upgrade "$FULL_SPEC"; then
    say "the t-SNE layout (openTSNE) cannot be installed here; installing cartolex without it"
    "$UV" pip install --python "$(env_python)" --upgrade "$SPEC" || return 1
  fi
  # cartolex models add installs with uv when the environment has no pip.
  PATH="$(dirname "$UV"):$PATH" "$ENV_DIR/bin/cartolex" models add $MODELS --yes || return 1
}

# ── the system's Python ──────────────────────────────────────────────────────

find_python() {
  local candidate
  for candidate in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1 &&
      "$candidate" -c "import sys; sys.exit(sys.version_info < (3, 10))" >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}

install_with_python() {
  if ! env_works; then
    local python
    python="$(find_python)" || fail "no Python 3.10 or later on this computer, and uv could not be used. Install Python from https://www.python.org/downloads/ and run the installer again."
    say "Python: $python ($("$python" --version 2>&1))"
    rm -rf "$ENV_DIR"
    "$python" -m venv "$ENV_DIR" ||
      fail "Python could not create an environment (on Debian or Ubuntu: sudo apt install python3-venv)"
  fi
  "$(env_python)" -m pip --version >/dev/null 2>&1 || "$(env_python)" -m ensurepip --upgrade || return 1
  if ! "$(env_python)" -m pip install --upgrade "$FULL_SPEC"; then
    say "the t-SNE layout (openTSNE) cannot be installed here; installing cartolex without it"
    "$(env_python)" -m pip install --upgrade "$SPEC" || return 1
  fi
  "$ENV_DIR/bin/cartolex" models add $MODELS --yes || return 1
}

# ── the route ────────────────────────────────────────────────────────────────

ROUTE=""
if [ "${CARTOLEX_ROUTE:-}" = "system" ]; then
  say "route: the system's Python (asked by CARTOLEX_ROUTE)"
elif get_uv; then
  step "installing with uv"
  if install_with_uv; then
    ROUTE="uv"
  else
    step "uv failed; again with the system's certificates (UV_NATIVE_TLS)"
    export UV_NATIVE_TLS=1
    if install_with_uv; then
      ROUTE="uv with native TLS"
    else
      unset UV_NATIVE_TLS
      say "uv failed with the system's certificates too"
    fi
  fi
else
  say "uv cannot be used"
fi
if [ -z "$ROUTE" ]; then
  step "installing with the system's Python"
  install_with_python || fail "pip could not install cartolex (see the messages above)"
  ROUTE="system Python"
fi
say "route taken: $ROUTE"

"$ENV_DIR/bin/cartolex" --help >/dev/null 2>&1 || fail "cartolex does not start"
VERSION="$("$(env_python)" -c "import importlib.metadata as m; print(m.version('cartolex'))")"
say "cartolex $VERSION is installed in $ENV_DIR"

# ── the shortcut ─────────────────────────────────────────────────────────────

step "creating the shortcut"
CARTOLEX="$ENV_DIR/bin/cartolex"
if [ "$(uname -s)" = "Darwin" ]; then
  LAUNCHER="$HOME/Desktop/cartolex.command"
  mkdir -p "$HOME/Desktop"
  cat >"$LAUNCHER" <<EOF
#!/bin/bash
# Opens cartolex in the browser. Keep this window open while you use it.
exec "$CARTOLEX" app
EOF
  chmod +x "$LAUNCHER"
  say "on the desktop: cartolex.command"
else
  ICON="$("$(env_python)" -c "import cartolex, pathlib; print(pathlib.Path(cartolex.__file__).parent / 'app/static/brand/favicon.svg')")"
  APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
  mkdir -p "$APPS"
  DESKTOP_FILE="$APPS/cartolex.desktop"
  cat >"$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=cartolex
Comment=Map a research field from the texts of its people
Exec="$CARTOLEX" app
Icon=$ICON
Terminal=true
Categories=Science;Education;
EOF
  chmod +x "$DESKTOP_FILE"
  say "in the applications menu: cartolex"
  DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
  if [ -n "$DESKTOP_DIR" ] && [ "$DESKTOP_DIR" != "$HOME" ] && [ -d "$DESKTOP_DIR" ]; then
    cp "$DESKTOP_FILE" "$DESKTOP_DIR/cartolex.desktop"
    chmod +x "$DESKTOP_DIR/cartolex.desktop"
    gio set "$DESKTOP_DIR/cartolex.desktop" metadata::trusted true >/dev/null 2>&1 || true
    say "on the desktop: cartolex"
  fi
fi

# ── the other text languages ─────────────────────────────────────────────────

step "other languages of the texts"
EXTRA="${CARTOLEX_EXTRA_LANGUAGES:-}"
# cartolex models add installs with uv when the environment has no pip.
MODELS_PATH="$(dirname "${UV:-$CARTOLEX}"):$PATH"
if [ -n "$EXTRA" ]; then
  PATH="$MODELS_PATH" "$CARTOLEX" models add $EXTRA --yes || say "some language models were not installed"
elif [ -t 0 ]; then
  say "cartolex also reads texts in Spanish (es), German (de) and Italian (it)."
  read -r -p "Type the codes of those your texts are in (for example: es de), or press Enter: " EXTRA || EXTRA=""
  for code in $EXTRA; do
    case " $EXTRA_MODELS " in
      *" $code "*) PATH="$MODELS_PATH" "$CARTOLEX" models add "$code" || say "$code: not installed" ;;
      *) say "$code: not a language cartolex reads" ;;
    esac
  done
fi
say "Later, in a terminal: $CARTOLEX models add es de it"

say ""
say "Done. Open cartolex with the shortcut, or run: $CARTOLEX"
say "The log is in $LOG"
pause
