#!/bin/bash
# Утилита управления SOL-Trader ботом

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

show_help() {
    echo ""
    echo "=========================================="
    echo "  SOL-Trader Bot Control"
    echo "=========================================="
    echo ""
    echo "Использование: ./scripts/bot_control.sh [команда]"
    echo ""
    echo "Команды:"
    echo "  start      - Запустить бота (фон)"
    echo "  stop       - Остановить бота"
    echo "  restart    - Перезапустить бота"
    echo "  status     - Показать статус"
    echo "  logs       - Показать логи (следить в реальном времени)"
    echo "  logs-bot   - Показать логи из файла"
    echo "  enable     - Включить автозапуск при загрузке"
    echo "  disable    - Выключить автозапуск"
    echo ""
    echo "Примеры:"
    echo "  ./scripts/bot_control.sh start"
    echo "  ./scripts/bot_control.sh status"
    echo "  ./scripts/bot_control.sh logs"
    echo ""
}

check_service() {
    if ! systemctl list-unit-files | grep -q sol-trader.service; then
        echo "❌ Сервис не установлен!"
        echo ""
        echo "Установите сервис командой:"
        echo "  sudo bash scripts/setup_service.sh"
        echo ""
        exit 1
    fi
}

case "$1" in
    start)
        check_service
        echo "🚀 Запускаю бота..."
        sudo systemctl start sol-trader
        sleep 2
        sudo systemctl status sol-trader --no-pager
        echo ""
        echo "✅ Бот запущен!"
        echo "📊 Смотреть логи: ./scripts/bot_control.sh logs"
        ;;

    stop)
        check_service
        echo "⏹️  Останавливаю бота..."
        sudo systemctl stop sol-trader
        echo "✅ Бот остановлен"
        ;;

    restart)
        check_service
        echo "🔄 Перезапускаю бота..."
        sudo systemctl restart sol-trader
        sleep 2
        sudo systemctl status sol-trader --no-pager
        echo ""
        echo "✅ Бот перезапущен!"
        ;;

    status)
        check_service
        sudo systemctl status sol-trader --no-pager
        echo ""
        echo "📊 Для просмотра логов:"
        echo "   ./scripts/bot_control.sh logs"
        ;;

    logs)
        check_service
        echo "📋 Логи бота (Ctrl+C для выхода):"
        echo ""
        sudo journalctl -u sol-trader -f
        ;;

    logs-bot)
        LATEST_LOG=$(ls -t $PROJECT_DIR/logs/bot_*.log 2>/dev/null | head -1)
        if [ -z "$LATEST_LOG" ]; then
            echo "❌ Логи не найдены в $PROJECT_DIR/logs/"
        else
            echo "📋 Логи из файла (Ctrl+C для выхода):"
            echo "   $LATEST_LOG"
            echo ""
            tail -f "$LATEST_LOG"
        fi
        ;;

    enable)
        check_service
        echo "✅ Включаю автозапуск при загрузке..."
        sudo systemctl enable sol-trader
        echo "✅ Автозапуск включен"
        ;;

    disable)
        check_service
        echo "⏸️  Выключаю автозапуск..."
        sudo systemctl disable sol-trader
        echo "✅ Автозапуск выключен"
        ;;

    help|--help|-h|"")
        show_help
        ;;

    *)
        echo "❌ Неизвестная команда: $1"
        show_help
        exit 1
        ;;
esac
