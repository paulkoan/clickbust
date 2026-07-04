#!/usr/bin/env bash
set -euo pipefail

CLICKBUST_DIR="/opt/data/clickbust"
DEPLOY_KEY="$CLICKBUST_DIR/deploy_key"
OUTPUT_DIR="$CLICKBUST_DIR/output"
TMP_DIR="/tmp/clickbust-deploy"
REPO="git@github.com:paulkoan/clickbust.git"

cd "$CLICKBUST_DIR"

# Run clickbust
echo "📡 Fetching articles and rewriting headlines..."
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run clickbust run 2>&1

# Write daily note (with context from today's run)
echo ""
echo "📝 Writing daily note..."
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run clickbust note --context "Clickbust ran today, $(date -u '+%Y-%m-%d')" 2>&1

# Prepare deploy directory
rm -rf "$TMP_DIR"
mkdir -p "$TMP_DIR"
cp -r "$OUTPUT_DIR"/. "$TMP_DIR"/

# Push to gh-pages
cd "$TMP_DIR"
git init -q
git checkout -b gh-pages -q
git config user.email "clickbust-bot@deploy"
git config user.name "Clickbust Bot"
git add -A
git commit -q -m "Clickbust update $(date -u '+%Y-%m-%d %H:%M UTC')"
GIT_SSH_COMMAND="ssh -i $DEPLOY_KEY -o StrictHostKeyChecking=accept-new" git remote add origin "$REPO"
GIT_SSH_COMMAND="ssh -i $DEPLOY_KEY -o StrictHostKeyChecking=accept-new" git push -f origin gh-pages 2>&1

echo ""
echo "✅ Clickbust update complete — $(date -u '+%Y-%m-%d %H:%M UTC')"