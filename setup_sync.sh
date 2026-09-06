#!/bin/bash
# Разовая настройка автоматической отправки сводки на GitHub Pages. Запуск: bash setup_sync.sh
set -e
DIR="$HOME/utro/github"
PLIST="$HOME/Library/LaunchAgents/com.ecovelle.utro-sync.plist"
mkdir -p "$HOME/.config/utro" "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
# 1. токен GitHub: берём из config/env.txt (строка GH_TOKEN=...) или спрашиваем
ENV="$HOME/utro/config/env.txt"
TOKEN=""
[ -f "$ENV" ] && TOKEN=$(grep '^GH_TOKEN=' "$ENV" | cut -d= -f2-)
if [ -z "$TOKEN" ]; then read -r -s -p "Вставь GitHub-токен (github_pat_...): " TOKEN; echo; fi
printf '%s' "$TOKEN" > "$HOME/.config/utro/gh_token"; chmod 600 "$HOME/.config/utro/gh_token"
# 2. git должен быть установлен (на macOS ставится вместе с Command Line Tools)
if ! xcode-select -p >/dev/null 2>&1; then
  echo "Устанавливаю Command Line Tools (появится системное окно — нажми Установить), потом запусти этот скрипт ещё раз."
  xcode-select --install; exit 0
fi
chmod +x "$DIR/sync_github.sh"
# 3. launchd: запуск при изменении файлов в папке github и каждый день в 08:20
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.ecovelle.utro-sync</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$DIR/sync_github.sh</string></array>
  <key>WatchPaths</key><array><string>$DIR</string></array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>8</integer><key>Minute</key><integer>20</integer></dict>
  <key>ThrottleInterval</key><integer>60</integer>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/utro-sync.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/utro-sync.log</string>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/com.ecovelle.utro-sync" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
# 4. первая отправка сразу
bash "$DIR/sync_github.sh" && tail -n 3 "$HOME/Library/Logs/utro-sync.log"
echo
echo "Готово. Страница: https://ecovelle.github.io/utro/  Лог: ~/Library/Logs/utro-sync.log"
