#!/usr/bin/env bash
# install.sh — copy-deploy ofd2spoolman.py to rosa's Spoolman compose dir.
#
# The deployed copy gets a pointer header (after the shebang) naming this repo,
# so nobody edits it in place. --check reports drift without writing anything.
#
# Usage: ./install.sh [--check] [--target PATH]
unset TMOUT
set -euo pipefail

REPO_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SRC="$REPO_DIR/ofd2spoolman.py"
TARGET=/opt/sw/docker-compose/spoolman/ofd2spoolman.py
CHECK=0

usage() { sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'; }
log() { printf '%s [%s] %s\n' "$(date '+%F %T')" "$1" "$2"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --check) CHECK=1 ;;
        --target) TARGET=$2; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage; exit 64 ;;
    esac
    shift
done

render() {
    sed -n '1p' "$SRC"
    cat <<EOF
# ===========================================================================
#  MANAGED by ofd-to-spoolman — DO NOT EDIT HERE.
#  source: $REPO_DIR/ofd2spoolman.py
#  edit there, then:  cd $REPO_DIR && ./install.sh
# ===========================================================================
EOF
    sed '1d' "$SRC"
}

rendered=$(mktemp)
trap 'rm -f "$rendered"' EXIT
render > "$rendered"

if [[ -f "$TARGET" ]] && cmp -s "$rendered" "$TARGET"; then
    log INFO "up to date: $TARGET"
    exit 0
fi

if [[ $CHECK -eq 1 ]]; then
    if [[ -f "$TARGET" ]]; then
        log ERROR "drift: $TARGET differs from the repo"
        diff -u "$TARGET" "$rendered" || true
    else
        log ERROR "missing: $TARGET"
    fi
    exit 1
fi

/usr/bin/python3 -I -m py_compile "$rendered"
install -m 0755 "$rendered" "$TARGET.new"
mv -f "$TARGET.new" "$TARGET"
log INFO "installed $TARGET"
