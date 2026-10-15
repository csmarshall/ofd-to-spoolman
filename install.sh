#!/usr/bin/env bash
# install.sh — copy-deploy ofd2spoolman.py to the deploy path of your choice.
#
# The deployed copy gets a pointer header (after the shebang) naming this repo,
# so nobody edits it in place. --check reports drift without writing anything.
#
# The target comes from --target PATH, else TARGET=PATH in a gitignored
# install.local file next to this script. One of the two is required.
#
# Usage: ./install.sh [--check] [--target PATH]
unset TMOUT
set -euo pipefail

REPO_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SRC="$REPO_DIR/ofd2spoolman.py"
TARGET=""
CHECK=0

usage() { sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; }
log() { printf '%s [%s] %s\n' "$(date '+%F %T')" "$1" "$2"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --check) CHECK=1 ;;
        --target)
            [[ $# -ge 2 ]] || { log ERROR "--target needs a value"; usage; exit 64; }
            TARGET=$2; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage; exit 64 ;;
    esac
    shift
done

# install.local is DATA, never sourced: only its last TARGET= line is used.
# Surrounding quotes are stripped and a leading ~ is expanded by hand.
if [[ -z "$TARGET" && -f "$REPO_DIR/install.local" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
        if [[ $line =~ ^[[:space:]]*TARGET=(.*)$ ]]; then
            value=${BASH_REMATCH[1]}
            if [[ $value =~ ^\"(.*)\"[[:space:]]*$ || $value =~ ^\'(.*)\'[[:space:]]*$ ]]; then
                value=${BASH_REMATCH[1]}
            fi
            [[ $value == "~" || $value == "~/"* ]] && value="$HOME${value:1}"
            TARGET=$value
        fi
    done < "$REPO_DIR/install.local"
fi

if [[ -z "$TARGET" ]]; then
    log ERROR "no target: pass --target PATH or set TARGET=PATH in $REPO_DIR/install.local"
    usage
    exit 64
fi

render() {
    sed -n '1p' "$SRC"
    cat <<EOF
# ===========================================================================
#  MANAGED by ofd-to-spoolman — DO NOT EDIT HERE.
#  source: $REPO_DIR/ofd2spoolman.py
#  edit there, then run $REPO_DIR/install.sh
#  (target from install.local, or --target <this file>)
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
