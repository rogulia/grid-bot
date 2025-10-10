#!/bin/bash
# Скрипт установки SOL-Trader как системного сервиса

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SERVICE_FILE="$PROJECT_DIR/sol-trader.service"
SYSTEMD_DIR="/etc/systemd/system"

echo "=========================================="
echo "  SOL-Trader Service Setup"
echo "=========================================="
echo ""

# Проверка прав sudo
if [ "$EUID" -ne 0 ]; then
    echo "⚠️  Этот скрипт требует sudo права"
    echo ""
    echo "Запустите:"
    echo "  sudo bash scripts/setup_service.sh"
    echo ""
    exit 1
fi

# Проверка существования service файла
if [ ! -f "$SERVICE_FILE" ]; then
    echo "❌ Файл $SERVICE_FILE не найден!"
    exit 1
fi

echo "📁 Проект: $PROJECT_DIR"
echo "📄 Service файл: $SERVICE_FILE"
echo ""

# Остановить сервис если он уже запущен
if systemctl is-active --quiet sol-trader; then
    echo "⏹️  Останавливаю существующий сервис..."
    systemctl stop sol-trader
fi

# Копировать service файл
echo "📋 Копирую service файл в $SYSTEMD_DIR..."
cp "$SERVICE_FILE" "$SYSTEMD_DIR/sol-trader.service"

# Перезагрузить systemd
echo "🔄 Перезагружаю systemd daemon..."
systemctl daemon-reload

# Включить автозапуск
echo "✅ Включаю автозапуск при загрузке системы..."
systemctl enable sol-trader

echo ""
echo "=========================================="
echo "  ✅ Установка завершена!"
echo "=========================================="
echo ""
echo "📋 Доступные команды:"
echo ""
echo "  Запустить бота:"
echo "    sudo systemctl start sol-trader"
echo ""
echo "  Остановить бота:"
echo "    sudo systemctl stop sol-trader"
echo ""
echo "  Перезапустить бота:"
echo "    sudo systemctl restart sol-trader"
echo ""
echo "  Статус бота:"
echo "    sudo systemctl status sol-trader"
echo ""
echo "  Отключить автозапуск:"
echo "    sudo systemctl disable sol-trader"
echo ""
echo "  Просмотр логов:"
echo "    sudo journalctl -u sol-trader -f"
echo "    или"
echo "    tail -f $PROJECT_DIR/logs/bot_*.log"
echo ""
echo "=========================================="
echo ""
echo "⚠️  РЕКОМЕНДАЦИЯ:"
echo "   1. Сначала протестируйте бота вручную"
echo "   2. Убедитесь что всё работает"
echo "   3. Только потом запускайте как сервис"
echo ""
echo "🚀 Готов к запуску!"
echo ""
