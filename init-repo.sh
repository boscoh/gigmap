#!/bin/bash
# Create the GitHub repository for this project and push the first commit.
#
#   bash init-repo.sh            private repository (default)
#   bash init-repo.sh --public   public repository
#
# The owner comes from the authenticated gh account.
set -euo pipefail

OWNER="${GH_OWNER:-boscoh}"
NAME="${GH_NAME:-gigmap}"
BRANCH="main"
VISIBILITY="--private"
MESSAGE="Melbourne gig map: FastAPI server, Vue page, data cleanup, deploy"

if [ "${1:-}" = "--public" ]; then
    VISIBILITY="--public"
fi

cd "$(dirname "${BASH_SOURCE[0]}")"

command -v git >/dev/null || { echo "git is missing" >&2; exit 1; }
command -v gh >/dev/null || { echo "the GitHub CLI is missing: brew install gh" >&2; exit 1; }

if ! gh auth status >/dev/null 2>&1; then
    echo "gh is not authenticated. Run: gh auth login" >&2
    exit 1
fi

LOGIN="$(gh api user --jq .login)"
echo "gh account: $LOGIN"
if [ "$LOGIN" != "$OWNER" ]; then
    echo "warning: the account is '$LOGIN', not '$OWNER'. The repository goes under '$LOGIN'." >&2
fi

if [ ! -d .git ]; then
    echo "creating the local repository on '$BRANCH'"
    git init -b "$BRANCH"
fi

git add -A
if git diff --cached --quiet; then
    echo "no changes to commit"
else
    echo "committing"
    git commit -m "$MESSAGE"
fi

if git remote get-url origin >/dev/null 2>&1; then
    echo "origin exists: $(git remote get-url origin)"
    echo "pushing"
    git push -u origin "$BRANCH"
else
    echo "creating $VISIBILITY repository $LOGIN/$NAME"
    gh repo create "$NAME" "$VISIBILITY" --source=. --remote=origin --push
fi

URL="$(gh repo view --json url --jq .url 2>/dev/null || true)"
echo
echo "done: ${URL:-see 'gh repo view'}"
echo
echo "After the folder rename, rebuild the local venv:"
echo "  rm -rf .venv .pytest_cache && uv sync && uv run pytest -q"
