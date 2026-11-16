#!/usr/bin/env bash
# Put the working tree at exactly the commit CI is asked to test.
#   TREE=head  -> the candidate head commit, unchanged
#   TREE=merge -> the candidate head merged into the target (base) commit
# HEAD_SHA and BASE_SHA are full commit SHAs resolved by the "resolve" job.
# A merge conflict fails the job; nothing is pushed.
set -euo pipefail

: "${TREE:?}" "${HEAD_SHA:?}"
git -c advice.detachedHead=false checkout --quiet "$HEAD_SHA"
if [[ "$TREE" == "merge" ]]; then
  : "${BASE_SHA:?BASE_SHA is required for TREE=merge}"
  git -c advice.detachedHead=false checkout --quiet "$BASE_SHA"
  git -c user.name="ci merge tree" -c user.email="ci@invalid" \
    merge --no-ff --no-edit --quiet "$HEAD_SHA"
elif [[ "$TREE" != "head" ]]; then
  echo "::error::Unknown TREE=$TREE"
  exit 2
fi
TESTED="$(git rev-parse HEAD)"
{
  echo "### Tested tree: \`$TREE\`"
  echo "- head: \`$HEAD_SHA\`"
  [[ "$TREE" == "merge" ]] && echo "- base: \`$BASE_SHA\`"
  echo "- tested commit: \`$TESTED\`"
} >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
echo "TREE=$TREE HEAD=$HEAD_SHA BASE=${BASE_SHA:-} TESTED=$TESTED"
