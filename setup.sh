#!/usr/bin/env bash
#
# One-click setup for the read-only Futubull (Futu/moomoo) data MCP server.
# Tested on macOS (zsh/bash); also works on Linux.
#
# What it does:
#   1. Checks prerequisites (Python 3.10+, uv or pip)
#   2. Creates a virtual env and installs this project
#   3. Creates .env from .env.example if missing
#   4. Tests the connection to the local OpenD gateway
#   5. Registers the server with Claude Code (if the `claude` CLI is present)
#
# Usage:   ./setup.sh
#
set -euo pipefail

# --- pretty output ----------------------------------------------------------
BOLD=$'\033[1m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; RESET=$'\033[0m'
info()  { printf "%s==>%s %s\n" "$BOLD" "$RESET" "$*"; }
ok()    { printf "%s✓%s %s\n" "$GREEN" "$RESET" "$*"; }
warn()  { printf "%s!%s %s\n" "$YELLOW" "$RESET" "$*"; }
fail()  { printf "%s✗%s %s\n" "$RED" "$RESET" "$*"; }

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

FUTU_HOST="${FUTU_HOST:-127.0.0.1}"
FUTU_PORT="${FUTU_PORT:-11111}"

echo
info "Futu Data MCP — one-click setup"
echo "    project: $PROJECT_DIR"
echo

# --- 1. prerequisites -------------------------------------------------------
info "Checking prerequisites"

PY=""
# Scan both unversioned and version-specific interpreter names so a freshly
# `brew install python@3.12` is picked up even when the system `python3` is old.
for cand in python3.13 python3.12 python3.11 python3.10 python3 python; do
  if command -v "$cand" >/dev/null 2>&1; then
    ver="$("$cand" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "0.0")"
    major="${ver%%.*}"; minor="${ver##*.}"
    if [ "$major" -eq 3 ] && [ "$minor" -ge 10 ]; then PY="$cand"; break; fi
  fi
done

if [ -z "$PY" ]; then
  fail "Python 3.10+ not found."
  echo "    Install it first:"
  echo "      brew install python@3.12      # needs Homebrew (https://brew.sh)"
  echo "    then re-run ./setup.sh"
  exit 1
fi
ok "Python: $($PY --version)"

USE_UV=0
if command -v uv >/dev/null 2>&1; then
  USE_UV=1
  ok "uv: $(uv --version)"
else
  warn "uv not found — will fall back to venv + pip."
  warn "  (Recommended: install uv →  curl -LsSf https://astral.sh/uv/install.sh | sh )"
fi

# --- 2. install -------------------------------------------------------------
info "Installing project + dependencies"
if [ "$USE_UV" -eq 1 ]; then
  uv venv --python "$PY" >/dev/null
  # shellcheck disable=SC1091
  source .venv/bin/activate
  uv pip install -e . >/dev/null
else
  "$PY" -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install --upgrade pip >/dev/null
  pip install -e . >/dev/null
fi
ok "Installed into .venv"

# --- 3. .env ----------------------------------------------------------------
info "Configuring environment"
if [ ! -f .env ]; then
  cp .env.example .env
  ok "Created .env from .env.example"
else
  ok ".env already exists (left unchanged)"
fi

# --- 4. test OpenD connection ----------------------------------------------
info "Testing connection to OpenD at ${FUTU_HOST}:${FUTU_PORT}"
set +e
conn="$(FUTU_HOST="$FUTU_HOST" FUTU_PORT="$FUTU_PORT" python - <<'PY' 2>/dev/null
import os
host = os.environ.get("FUTU_HOST", "127.0.0.1")
port = int(os.environ.get("FUTU_PORT", "11111"))
try:
    from futu import OpenQuoteContext, RET_OK
    ctx = OpenQuoteContext(host=host, port=port)
    ret, data = ctx.get_market_state(["US.AAPL"])
    ctx.close()
    print("OK" if ret == RET_OK else "ERR:" + str(data))
except Exception as e:
    print("EXC:" + str(e))
PY
)"
set -e

if [ "$conn" = "OK" ]; then
  ok "OpenD reachable and responding 🎉"
else
  warn "Could not get a quote from OpenD yet. (${conn:-no response})"
  echo "    Make sure OpenD is DOWNLOADED, RUNNING and LOGGED IN before using the server."
  echo "    Download: https://www.futunn.com/download/OpenAPI  (or moomoo: https://www.moomoo.com/download/OpenAPI)"
fi

# --- 5. register with Claude Code ------------------------------------------
info "Registering with Claude Code"
if command -v claude >/dev/null 2>&1; then
  if claude mcp list 2>/dev/null | grep -q "futu-data"; then
    ok "MCP server 'futu-data' already registered."
  else
    if [ "$USE_UV" -eq 1 ]; then
      claude mcp add futu-data -- uv run --directory "$PROJECT_DIR" futu-data-mcp \
        && ok "Registered 'futu-data' with Claude Code."
    else
      claude mcp add futu-data -- "$PROJECT_DIR/.venv/bin/futu-data-mcp" \
        && ok "Registered 'futu-data' with Claude Code."
    fi
  fi
else
  warn "Claude Code CLI ('claude') not found — skipping registration."
  echo "    After installing Claude Code, run:"
  if [ "$USE_UV" -eq 1 ]; then
    echo "      claude mcp add futu-data -- uv run --directory \"$PROJECT_DIR\" futu-data-mcp"
  else
    echo "      claude mcp add futu-data -- \"$PROJECT_DIR/.venv/bin/futu-data-mcp\""
  fi
fi

# --- done -------------------------------------------------------------------
echo
ok "Setup complete."
echo
echo "Next:"
echo "  1. Make sure OpenD is running and logged in."
echo "  2. Start Claude Code in this folder and try:"
echo "       \"用 futu-data 看 AAPL 現在的報價\""
echo
