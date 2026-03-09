#!/usr/bin/env bash
set -euo pipefail

# Определяем директорию проекта
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="support-bot"

echo "=== Deploy $SERVICE_NAME ==="

# Проверяем .env
if [ ! -f "$PROJECT_DIR/.env" ]; then
    echo "Ошибка: .env не найден. Скопируйте .env.example и заполните:"
    echo "  cp .env.example .env && nano .env"
    exit 1
fi

# Создаём venv и ставим зависимости
echo "[1/3] Установка зависимостей..."
python3 -m venv "$PROJECT_DIR/venv"
"$PROJECT_DIR/venv/bin/pip" install -q -r "$PROJECT_DIR/requirements.txt"

# Создаём systemd unit
echo "[2/3] Настройка systemd сервиса..."
sudo tee /etc/systemd/system/$SERVICE_NAME.service > /dev/null <<EOF
[Unit]
Description=Telegram Support Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=$PROJECT_DIR
ExecStart=$PROJECT_DIR/venv/bin/python $PROJECT_DIR/bot.py
Restart=on-failure
RestartSec=5
EnvironmentFile=$PROJECT_DIR/.env

[Install]
WantedBy=multi-user.target
EOF

# Запускаем
echo "[3/3] Запуск бота..."
sudo systemctl daemon-reload
sudo systemctl enable $SERVICE_NAME
sudo systemctl restart $SERVICE_NAME

echo ""
echo "=== Готово! ==="
echo "Статус:    sudo systemctl status $SERVICE_NAME"
echo "Логи:      sudo journalctl -u $SERVICE_NAME -f"
echo "Стоп:      sudo systemctl stop $SERVICE_NAME"
echo "Рестарт:   sudo systemctl restart $SERVICE_NAME"
