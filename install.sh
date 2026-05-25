#!/usr/bin/env bash
# AnimaDex installer (POSIX).
#
# Usage:
#   ./install.sh                    Core install.
#   ./install.sh --with-scoring     Also install the artist-scorer deps.
#   ./install.sh --with-generation  Also install ComfyUI client deps.
#   ./install.sh --all              Both extras.

set -euo pipefail

cd "$(dirname "$0")"

WITH_SCORING=0
WITH_GENERATION=0
for arg in "$@"; do
    case "$arg" in
        --with-scoring)   WITH_SCORING=1 ;;
        --with-generation) WITH_GENERATION=1 ;;
        --all)            WITH_SCORING=1; WITH_GENERATION=1 ;;
        -h|--help)
            sed -n '2,8p' "$0"; exit 0 ;;
        *) echo "Unknown arg: $arg" >&2; exit 2 ;;
    esac
done

# --- 1. Python check ---
PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "Python 3.11+ is required but '$PYTHON' was not found." >&2
    exit 1
fi
PYVER="$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
case "$PYVER" in
    3.11|3.12|3.13|3.14) ;;
    *) echo "Python 3.11+ required (have $PYVER)." >&2; exit 1 ;;
esac
echo "Using $("$PYTHON" --version)"

# --- 2. venv ---
if [ ! -d .venv ]; then
    echo "Creating virtualenv in .venv/"
    "$PYTHON" -m venv .venv
fi
# shellcheck disable=SC1091
. .venv/bin/activate

# --- 3. deps ---
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if [ "$WITH_SCORING" = "1" ]; then
    python -m pip install -r requirements-scoring.txt
fi
if [ "$WITH_GENERATION" = "1" ]; then
    python -m pip install -r requirements-generation.txt
fi
python -m pip install -e .

# --- 4. config ---
if [ ! -f config.toml ]; then
    cp config.toml.example config.toml
    echo ""
    echo "==> Created config.toml from the example. Open it and set at least:"
    echo "      [server].secret_key  (run: python -m animadex genkey)"
    echo "      [admin].password     (only if you want the admin inbox)"
    echo ""
fi

# --- 5. data dir + schema ---
python -m animadex db-init

# --- 6. seed samples (only on the first run, only if data dir is empty) ---
DATA_DIR="$(python -c 'from animadex.config import load; print(load().paths.data_dir)')"
if [ -d "samples" ] && [ ! -e "${DATA_DIR}/.seeded" ]; then
    echo ""
    echo "==> Seeding ${DATA_DIR} from samples/ (one-time)"
    mkdir -p "${DATA_DIR}/characters/thumbs" \
             "${DATA_DIR}/artists/thumbs" \
             "${DATA_DIR}/copyrights/thumbs"
    cp -r samples/images/characters/thumbs/* \
          "${DATA_DIR}/characters/thumbs/" 2>/dev/null || true
    cp -r samples/images/artists/thumbs/* \
          "${DATA_DIR}/artists/thumbs/" 2>/dev/null || true
    cp -r samples/images/copyrights/thumbs/* \
          "${DATA_DIR}/copyrights/thumbs/" 2>/dev/null || true
    python -m animadex build-db samples/characters.csv --mode characters
    python -m animadex build-db samples/artists.csv    --mode artists
    touch "${DATA_DIR}/.seeded"
fi

echo ""
echo "Install complete. Start the app:  ./run.sh"
