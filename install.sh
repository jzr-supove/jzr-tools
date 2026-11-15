#!/bin/sh
# Install jzr-tools on macOS or Linux:
#
#   curl -LsSf https://raw.githubusercontent.com/jzr-supove/jzr-tools/main/install.sh | sh
#
# Installs uv (https://docs.astral.sh/uv/) when it is missing; uv then
# downloads a suitable Python by itself, so Python does not have to be
# installed first. Running it again upgrades jzr-tools.
set -eu

fail() {
    echo "jzr-tools was not installed: $1" >&2
    exit 1
}

if ! command -v uv >/dev/null 2>&1; then
    echo "Installing uv..."
    if command -v curl >/dev/null 2>&1; then
        curl -LsSf https://astral.sh/uv/install.sh | sh || fail "the uv installer failed"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO- https://astral.sh/uv/install.sh | sh || fail "the uv installer failed"
    else
        fail "curl or wget is needed to download uv"
    fi
    # The uv installer puts uv here; this shell has not picked up the new PATH yet.
    PATH="${UV_INSTALL_DIR:-${XDG_BIN_HOME:-$HOME/.local/bin}}:$PATH"
    export PATH
    command -v uv >/dev/null 2>&1 || fail "uv was installed but cannot be found; open a new terminal and run this again"
fi

echo "Installing jzr-tools..."
uv tool install --upgrade jzr-tools || fail "uv tool install failed (see above)"
# Make sure the folder with the jzr command is on PATH for new terminals.
uv tool update-shell >/dev/null 2>&1 || true

echo
echo "Done. Open a new terminal and run: jzr --help"
echo "Video and audio commands need ffmpeg; if you do not have it, run: jzr ffmpeg install"
