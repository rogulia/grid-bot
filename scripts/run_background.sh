#!/bin/bash
# Альтернатива systemd: запуск бота в фоне через screen

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SESSION_NAME="sol-trader-bot"

show_help() {
    echo ""
    echo "=========================================="
    echo "  SOL-Trader Background Runner"
    echo "=========================================="
    echo ""
    echo "Использование: ./scripts/run_background.sh [команда]"
    echo ""
    echo "Команды:"
    echo "  start      - Запустить бота в фоне"
    echo "  stop       - Остановить бота"
    echo "  attach     - Подключиться к боту (отключиться: Ctrl+A, D)"
    echo "  status     - Проверить запущен ли бот"
    echo "  logs       - Показать логи"
    echo ""
    echo "ℹ️  Использует screen для работы в фоне"
    echo "   (автозапуск при перезагрузке НЕ работает)"
    echo ""
    echo "Для автозапуска используйте systemd:"
    echo "  sudo bash scripts/setup_service.sh"
    echo ""
}

check_screen() {
    if ! command -v screen &> /dev/null; then
        echo "❌ screen не установлен!"
        echo ""
        echo "Установите screen:"
        echo "  sudo apt-get install screen"
        echo ""
        echo "Или используйте systemd сервис:"
        echo "  sudo bash scripts/setup_service.sh"
        echo ""
        exit 1
    fi
}

is_running() {
    screen -list | grep -q "$SESSION_NAME"
}

case "$1" in
    start)
        check_screen

        if is_running; then
            echo "⚠️  Бот уже запущен!"
            echo ""
            echo "Подключиться: ./scripts/run_background.sh attach"
            echo "Остановить: ./scripts/run_background.sh stop"
            exit 1
        fi

        echo "🚀 Запускаю бота в фоне..."
        cd "$PROJECT_DIR"
        screen -dmS "$SESSION_NAME" bash -c "source .venv/bin/activate && python src/main.py"
        sleep 2

        if is_running; then
            echo "✅ Бот запущен в фоне!"
            echo ""
            echo "📋 Команды управления:"
            echo "  Подключиться к боту:  ./scripts/run_background.sh attach"
            echo "  Проверить статус:     ./scripts/run_background.sh status"
            echo "  Остановить бота:      ./scripts/run_background.sh stop"
            echo "  Показать логи:        ./scripts/run_background.sh logs"
            echo ""
            echo "ℹ️  Чтобы отключиться от бота не останавливая его:"
            echo "   Нажмите: Ctrl+A, затем D"
        else
            echo "❌ Не удалось запустить бота"
            echo ""
            echo "Попробуйте запустить вручную:"
            echo "  source .venv/bin/activate && python src/main.py"
        fi
        ;;

    stop)
        check_screen

        if ! is_running; then
            echo "ℹ️  Бот не запущен"
            exit 0
        fi

        echo "⏹️  Останавливаю бота..."
        screen -S "$SESSION_NAME" -X quit
        sleep 1

        if ! is_running; then
            echo "✅ Бот остановлен"
        else
            echo "⚠️  Не удалось остановить бота"
            echo ""
            echo "Попробуйте вручную:"
            echo "  screen -S $SESSION_NAME -X quit"
        fi
        ;;

    attach)
        check_screen

        if ! is_running; then
            echo "❌ Бот не запущен!"
            echo ""
            echo "Запустите бота:"
            echo "  ./scripts/run_background.sh start"
            exit 1
        fi

        echo "🔗 Подключаюсь к боту..."
        echo ""
        echo "ℹ️  Чтобы отключиться НЕ останавливая бота:"
        echo "   Нажмите: Ctrl+A, затем D"
        echo ""
        sleep 2
        screen -r "$SESSION_NAME"
        ;;

    status)
        check_screen

        if is_running; then
            echo "✅ Бот запущен"
            echo ""
            screen -list | grep "$SESSION_NAME"
            echo ""
            echo "Подключиться: ./scripts/run_background.sh attach"
        else
            echo "⏹️  Бот остановлен"
            echo ""
            echo "Запустить: ./scripts/run_background.sh start"
        fi
        ;;

    logs)
        LATEST_LOG=$(ls -t $PROJECT_DIR/logs/bot_*.log 2>/dev/null | head -1)
        if [ -z "$LATEST_LOG" ]; then
            echo "❌ Логи не найдены в $PROJECT_DIR/logs/"
        else
            echo "📋 Логи бота (Ctrl+C для выхода):"
            echo "   $LATEST_LOG"
            echo ""
            tail -f "$LATEST_LOG"
        fi
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
