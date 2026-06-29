#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

UV=""
for c in "$HOME/.hermes/bin/uv" "$HOME/.cargo/bin/uv" "$HOME/.local/bin/uv" /usr/local/bin/uv /usr/bin/uv; do
    [[ -x "$c" ]] && { UV="$c"; break; }
done
[[ -z "$UV" ]] && { echo "uv not found"; exit 1; }

[[ ! -d .venv ]] && "$UV" sync --all-extras

export WHISPER_CACHE_DIR="$SCRIPT_DIR/cache/whisper"
export SILERO_CACHE_DIR="$SCRIPT_DIR/cache/silero"
export XDG_CACHE_HOME="$SCRIPT_DIR/cache"
mkdir -p "$WHISPER_CACHE_DIR" "$SILERO_CACHE_DIR"

[[ -f .env ]] && set -a && source .env && set +a

exec "$UV" run --project "$SCRIPT_DIR" python -m src.bot "$@"