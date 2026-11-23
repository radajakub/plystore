#!/usr/bin/env bash
# plydata's history without what slim-plydata removed (IMPORTED of every data folder, read from scripts/server.sh): a mirror clone rewritten by
# git filter-repo (through uvx), checked to end in the same tree as before and to hold none of those paths in any commit, then force-pushed after
# asking. No copy of the old history is kept: Postgres and its dumps hold the data. Run it where GitHub takes your pushes (the laptop), after
# slim-plydata; then every other clone is replaced (DEPLOY.md, "Files off and a slim plydata").
#
#   scripts/rewrite-plydata.sh <new folder> [remote]     (remote: the origin of the plydata beside this checkout)
set -euo pipefail

HERE=$(cd "$(dirname "$0")/.." && pwd)
eval "$(grep -E '^(STORES|IMPORTED)=' "$HERE/scripts/server.sh")"

say() { printf '%s\n' "$*"; }
fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}
ask() {
  local answer
  read -r -p "$1 [y/N] " answer </dev/tty
  [ "$answer" = y ] || [ "$answer" = Y ]
}

[ $# -ge 1 ] || fail "usage: scripts/rewrite-plydata.sh <new folder> [remote]"
work=$1
remote=${2:-$(git -C "$HERE/../plydata" remote get-url origin)}
[ ! -e "$work" ] || fail "$work exists: give a new folder"

# one regex over every path: a data folder (or none: before 2026-10-21 plybench's folder was plydata's root, moved unchanged), then an imported
# pattern (a glob: * stays within a folder) naming a file or a folder; but not the links' own .keep (slim-plydata wrote them; plyservice's check
# needs the links to resolve)
folders=$(for pair in "${STORES[@]}"; do echo "${pair##*:}"; done | sort -u | paste -sd '|' -)
patterns=$(for pattern in $IMPORTED; do printf '%s\n' "$pattern"; done | sed -e 's/\./\\./g' -e 's/\*/[^\/]*/g' | paste -sd '|' -)
regex="^(?!($folders)/(experiments|results)/\\.keep\$)(($folders)/)?($patterns)(/|\$)"

say "cloning $remote into $work"
git clone -q --mirror "$remote" "$work"
cd "$work"
[ "$(git for-each-ref --format='%(refname)')" = refs/heads/master ] || fail "plydata has other refs than master: $(git for-each-ref --format='%(refname)' | paste -sd ' ' -)"
old=$(git rev-parse refs/heads/master)
tree=$(git rev-parse "refs/heads/master^{tree}")
commits=$(git rev-list --count refs/heads/master)
size=$(git count-objects -vH | sed -n 's/^size-pack: //p')

say "rewriting: every path matching $regex goes from every commit"
uvx git-filter-repo --invert-paths --path-regex "$regex"

[ "$(git rev-parse "refs/heads/master^{tree}")" = "$tree" ] || fail "the rewritten master does not end in the tree it had: nothing pushed"
# every file any commit adds, changes or deletes: none may match
left=$(git log --all --format= --name-only --no-renames | sort -u | python3 -c "import re, sys; print(' '.join([p.strip() for p in sys.stdin if re.search(sys.argv[1], p)][:5]))" "$regex")
[ -z "$left" ] || fail "the rewritten history still holds removed paths, e.g. $left: nothing pushed"
say "rewritten: master ends in the same tree; $commits commits -> $(git rev-list --count refs/heads/master), $size -> $(git count-objects -vH | sed -n 's/^size-pack: //p') packed"

ask "force-push the rewritten master to $remote (only if it is still $old; every other clone must then be replaced)?" || exit 0
git push -q --force-with-lease="refs/heads/master:$old" "$remote" refs/heads/master:refs/heads/master
say "pushed $(git rev-parse --short refs/heads/master); next: replace the other clones (DEPLOY.md)"
