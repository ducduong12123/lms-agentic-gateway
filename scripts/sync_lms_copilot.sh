#!/usr/bin/env bash
# Deploy this worktree's lms_copilot app into the running Frappe dev container.
#
# Usage (inside WSL, from any directory):
#   bash scripts/sync_lms_copilot.sh [--no-migrate] [--no-build] [--tests]
#
# From Windows:
#   wsl.exe -d Ubuntu -- bash -lc "bash /mnt/d/lms-agentic-gateway-wt/demo/scripts/sync_lms_copilot.sh"
#
# Environment:
#   CONTAINER  Frappe container name        (default: lms-frappe-1)
#   SITE       Frappe site                  (default: lms.localhost)
#   BENCH      bench directory in container (default: /home/frappe/frappe-bench)
#
# Steps:
#   1. Back up the container copy to /home/frappe/lms_copilot.bak-<timestamp> (inside the container).
#   2. Stream lms_copilot/ into apps/lms_copilot with tar over `docker exec -i`, skipping
#      __pycache__/.ruff_cache and converting CRLF to LF in text files (Windows checkouts are CRLF).
#   3. chown to frappe, then migrate, build assets and clear the cache.
#
# The container runs `bench start` under honcho with developer_mode on: the web process
# (`bench serve`, werkzeug reloader) picks up Python changes by itself. The rq worker forks
# a fresh work horse per job, but modules imported by the parent stay cached; killing it would
# make honcho stop the whole container, so the script does not restart it. Restart the
# container (`docker restart lms-frappe-1`) if a background job must see new code at once.
set -euo pipefail

CONTAINER="${CONTAINER:-lms-frappe-1}"
SITE="${SITE:-lms.localhost}"
BENCH="${BENCH:-/home/frappe/frappe-bench}"
MIGRATE=1
BUILD=1
TESTS=0
for arg in "$@"; do
	case "$arg" in
		--no-migrate) MIGRATE=0 ;;
		--no-build) BUILD=0 ;;
		--tests) TESTS=1 ;;
		-h|--help) sed -n '2,26p' "$0"; exit 0 ;;
		*) echo "Unknown option: $arg" >&2; exit 2 ;;
	esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="$(cd "$SCRIPT_DIR/../lms_copilot" && pwd)"
TARGET="$BENCH/apps/lms_copilot"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="/home/frappe/lms_copilot.bak-$STAMP"

in_container() {
	docker exec -u frappe -w "$BENCH" "$CONTAINER" bash -lc "$1"
}

[ -f "$SOURCE/lms_copilot/hooks.py" ] || { echo "No lms_copilot app at $SOURCE" >&2; exit 1; }
docker inspect "$CONTAINER" >/dev/null

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

echo "==> Staging $SOURCE"
tar -C "$SOURCE" --exclude='__pycache__' --exclude='.ruff_cache' --exclude='*.pyc' \
	--exclude='node_modules' -cf - . | tar -C "$STAGE" -xf -
find "$STAGE" -type f \( -name '*.py' -o -name '*.json' -o -name '*.js' -o -name '*.css' \
	-o -name '*.html' -o -name '*.md' -o -name '*.txt' -o -name '*.po' -o -name '*.toml' \
	-o -name '*.cfg' -o -name '.gitignore' -o -name '*.sh' \) -exec sed -i 's/\r$//' {} +

echo "==> Backing up container copy to $BACKUP"
docker exec -u root "$CONTAINER" bash -c "[ -d '$TARGET' ] && cp -a '$TARGET' '$BACKUP' || true"

echo "==> Copying into $CONTAINER:$TARGET"
docker exec -u root "$CONTAINER" bash -c "mkdir -p '$TARGET' && find '$TARGET' -mindepth 1 -maxdepth 1 ! -name '*.egg-info' -exec rm -rf {} +"
tar -C "$STAGE" -cf - . | docker exec -i -u root "$CONTAINER" tar -C "$TARGET" -xf -
docker exec -u root "$CONTAINER" chown -R frappe:frappe "$TARGET"

if [ "$MIGRATE" = 1 ]; then
	echo "==> Migrating $SITE"
	in_container "bench --site $SITE migrate --skip-search-index"
fi
if [ "$BUILD" = 1 ]; then
	echo "==> Building lms_copilot assets"
	in_container "bench build --app lms_copilot"
fi
echo "==> Clearing cache"
in_container "bench --site $SITE clear-cache"

if [ "$TESTS" = 1 ]; then
	echo "==> Running lms_copilot tests"
	in_container "bench --site $SITE run-tests --app lms_copilot"
fi
echo "Deployed. Backup: $CONTAINER:$BACKUP"
