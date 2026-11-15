#!/usr/bin/env bash
# Merge upstream on a review branch. Never overwrite RU files or push anything.
set -euo pipefail
cd "$(dirname "$0")/../.."
if [[ "${1:-}" == '--finish' ]]; then
    tag=${2:?Usage: update-upstream.sh --finish vX.Y.Z}
    [[ -f .git/MERGE_HEAD ]] || { echo 'No pending upstream merge' >&2; exit 1; }
    [[ -z "$(git diff --name-only --diff-filter=U)" ]] || { echo 'Resolve merge conflicts first' >&2; exit 1; }
else
    tag=${1:?Usage: update-upstream.sh vX.Y.Z | --finish vX.Y.Z}
    [[ -z "$(git status --porcelain)" ]] || { echo 'Commit local work first' >&2; exit 1; }
    [[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo 'Use an upstream stable version tag' >&2; exit 1; }
    [[ "$(git remote get-url upstream)" == 'https://github.com/subhashhhhhh/Lumen.git' ]] || { echo 'Unexpected upstream remote' >&2; exit 1; }
    git fetch --no-tags upstream main "refs/tags/$tag:refs/remotes/upstream/tags/$tag"
    git switch -c "upstream-update/$tag"
    if ! git merge --no-ff --no-commit "refs/remotes/upstream/tags/$tag"; then
        echo 'Resolve the merge on this branch, keeping RU identity, versioning, localization, signing and local-only releases.' >&2
        echo "Then run: tools/release/update-upstream.sh --finish $tag" >&2
        exit 1
    fi
    [[ -f .git/MERGE_HEAD ]] || { echo 'This upstream release is already merged'; exit 0; }
fi
[[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || exit 1
commit=$(git rev-parse "refs/remotes/upstream/tags/$tag^{commit}")
[[ "$(cat .git/MERGE_HEAD)" == "$commit" ]] || { echo 'Tag differs from pending merge' >&2; exit 1; }
printf 'version=%s\ntag=%s\ncommit=%s\n' "${tag#v}" "$tag" "$commit" > upstream.properties
if [[ -d .github/workflows ]] && [[ -n "$(find .github/workflows -type f -print -quit)" ]]; then
    echo 'Upstream reintroduced Actions. Remove its workflows from this branch; RU builds locally.' >&2
    exit 1
fi
# On an engine pin change the helper deliberately stops rather than replacing a local checkout.
tools/release/prepare-deps.sh
python3 tools/release/check-translations.py
# Stops for missing/stale translations. Translate/review them and --record-source, then --finish.
./release.sh --build-only
git add -u
git add app/src/main/res/values-ru tools/release/translation-source.json upstream.properties
git commit -m "Merge Lumen $tag into Lumen RU"
echo 'Review this branch, then merge into main without squashing. Publishing is a separate release.sh patch/minor call.'
