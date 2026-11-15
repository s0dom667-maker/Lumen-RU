#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
pin=$(tr -d '[:space:]' < .github/lumen-ai.commit)
[[ "$pin" =~ ^[0-9a-f]{40}$ ]] || { echo 'Invalid Lumen AI pin' >&2; exit 1; }
dep=.deps/lumen-ai
patch=$(realpath tools/release/lumen-ai-ru.patch)
if [[ ! -d "$dep/.git" ]]; then
    mkdir -p .deps
    git clone --no-checkout https://github.com/subhashhhhhh/lumen-ai.git "$dep"
    git -C "$dep" checkout --detach "$pin"
fi
[[ "$(git -C "$dep" rev-parse HEAD)" == "$pin" ]] || {
    echo 'Engine pin changed. Move .deps/lumen-ai aside, then rerun; no checkout is overwritten.' >&2; exit 1;
}
if git -C "$dep" diff --quiet HEAD && [[ -z "$(git -C "$dep" ls-files --others --exclude-standard)" ]]; then
    git -C "$dep" apply "$patch"
fi
# Include the patch's new resource files when comparing the complete patch.
git -C "$dep" add -N ai/src/main/res/values/lumen_ru_notifications.xml ai/src/main/res/values-ru/lumen_ru_notifications.xml
if ! git -C "$dep" diff --binary HEAD | cmp -s "$patch" -; then
    echo 'Engine has unexpected changes, or the notification patch needs updating. Stopping.' >&2; exit 1
fi
[[ -z "$(git -C "$dep" ls-files --others --exclude-standard)" ]] || {
    echo 'Unexpected untracked files in engine checkout' >&2; exit 1;
}
echo "Lumen AI: $pin + notification translations only"
