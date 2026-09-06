#!/bin/bash
# Отправляет папку github/ (index.html, robots.txt) в репозиторий ecovelle/utro на GitHub.
# Запускается launchd автоматически (при изменении файлов и в 08:20) или вручную: bash sync_github.sh
set -e
DIR="$HOME/Desktop/OZON Analytics/github"
LOG="$HOME/Library/Logs/utro-sync.log"
TOKEN_FILE="$HOME/.config/utro/gh_token"
REPO="ecovelle/utro"
cd "$DIR"
exec >>"$LOG" 2>&1
echo "--- $(date '+%Y-%m-%d %H:%M:%S') sync start"
[ -f "$TOKEN_FILE" ] || { echo "нет файла с токеном $TOKEN_FILE"; exit 1; }
TOKEN=$(cat "$TOKEN_FILE")
export GIT_TERMINAL_PROMPT=0
if [ ! -d .git ]; then
  git init -q -b main
  git config user.name "utro-bot"; git config user.email "utro-bot@users.noreply.github.com"
fi
git remote remove origin 2>/dev/null || true
git remote add origin "https://x-access-token:${TOKEN}@github.com/${REPO}.git"
# подтянуть то, что уже лежит в репозитории (например, загруженное руками), не теряя локальные файлы
if git fetch -q origin main 2>/dev/null; then
  git reset -q --soft origin/main 2>/dev/null || true
fi
git add -A
if git diff --cached --quiet; then echo "изменений нет"; exit 0; fi
git commit -q -m "brief $(date '+%Y-%m-%d %H:%M')"
git push -q origin HEAD:main
echo "отправлено: https://ecovelle.github.io/utro/"
