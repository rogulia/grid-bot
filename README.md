# 🤖 SOL-Trader

**Автоматический торговый бот для фьючерсов Bybit** с grid-стратегией двустороннего хеджирования и 7-фазной системой управления рисками v3.1.

Поддерживает **несколько изолированных аккаунтов** для SaaS-модели с полной изоляцией данных, WebSocket-first архитектурой и адаптивным управлением позициями.

---

## ⚠️ КРИТИЧЕСКОЕ ПРЕДУПРЕЖДЕНИЕ

**ВЫСОКИЙ РИСК!** Стратегия использует:
- **Cross Margin режим** (весь баланс как обеспечение)
- **Высокое плечо** (75-100x)
- **Агрессивный мартингейл** (2x multiplier)

**Критические риски:**
- Односторонний тренд +10% без откатов → потеря 70% депозита (без защиты v3.1)
- Асимметрия мартингейла нарушает симметрию хеджа
- Каждая сторона (LONG/SHORT) может ликвидироваться независимо

**Система защиты v3.1 снижает риски**, но не устраняет их полностью.

**⚠️ ВСЕГДА тестируйте на demo минимум 2-4 недели перед использованием реальных средств!**

---

## 📚 Документация

### 🚀 Начало работы
1. **[Быстрый старт](docs/01-getting-started.md)** - Установка и первый запуск
2. **[Торговая стратегия](docs/02-strategy.md)** - Описание стратегии и механики
3. **[Конфигурация](docs/03-configuration.md)** - Настройка параметров

### 🛡️ Риск-менеджмент
4. **[Управление рисками v3.1](docs/04-risk-management.md)** - 7-фазная система защиты

### 📊 Эксплуатация
5. **[Запуск 24/7](docs/05-operations.md)** - Настройка фонового сервиса
6. **[Аналитика](docs/06-analytics.md)** - Анализ результатов
7. **[Мультивалютная торговля](docs/07-multi-symbol.md)** - Торговля несколькими монетами

### 🔧 Для разработчиков
8. **[Архитектура](docs/08-architecture.md)** - Техническая архитектура + глоссарий
9. **[Тестирование](docs/09-development.md)** - Unit и integration тесты
10. **[CLAUDE.md](CLAUDE.md)** - Техническая спецификация для AI

### 📖 Дополнительно
11. **[TP Distance Analysis](docs/11-tp-distance.md)** - Таблица расстояний до TP
12. **[FAQ](docs/10-faq.md)** - Частые вопросы

---

## 🚀 Быстрые команды

### Запуск и остановка

```bash
# Терминал (для тестирования)
python src/main.py                          # Запуск в терминале
# Ctrl+C для остановки

# Фоновый сервис (для production)
sudo systemctl start sol-trader             # Запуск
sudo systemctl stop sol-trader              # Остановка
sudo systemctl restart sol-trader           # Перезапуск
sudo systemctl status sol-trader            # Статус
```

### Мониторинг

```bash
# Логи
sudo journalctl -u sol-trader -f                # Системные логи
sudo journalctl -u sol-trader -f | grep "\[001\]"  # Только account 001
tail -f logs/001_bot_$(date +%Y-%m-%d).log  # Файловые логи

# Баланс
python scripts/check_balance.py             # Проверка баланса

# Анализ
python scripts/analyze.py --plot            # Анализ с графиками
```

### Управление

```bash
# Emergency stop
ls -la data/.001_emergency_stop             # Проверить flag
cat data/.001_emergency_stop                 # Причина
rm data/.001_emergency_stop                  # Снять (после анализа)

# Тесты
pytest tests/ -v                             # Все тесты (172)
pytest tests/test_grid_strategy.py -v        # Конкретный файл
```

---

## 📊 Основные возможности

### Архитектура
- ✅ **Multi-account SaaS-ready** - Полная изоляция аккаунтов
- ✅ **WebSocket-first** - Минимум REST API, real-time данные
- ✅ **Thread-safe** - Безопасная многопоточность
- ✅ **Fail-fast** - Нет fallback значений для критических данных

### Стратегия
- ✅ **Dual-sided hedge** - Одновременные LONG + SHORT позиции
- ✅ **Grid averaging** - Усреднение по сетке с мартингейлом
- ✅ **Adaptive reopen** - Размер переоткрытия адаптируется к дисбалансу
- ✅ **Fee-adjusted TP** - Расчет TP с учетом комиссий

### Защита v3.1
- ✅ **7-фазная система** - От превентивной блокировки до emergency close
- ✅ **ATR-based safety** - Адаптация к волатильности
- ✅ **Early Freeze** - Блокировка усреднений при low available
- ✅ **Panic Mode** - Intelligent TP + position balancing
- ✅ **Emergency Close** - При MM Rate >= threshold (default 90%)

### Тестирование
- ✅ **172 comprehensive тестов** - Unit + integration
- ✅ **100% core coverage** - Все критические компоненты
- ✅ **Mock Bybit API** - Безопасное тестирование

---

## 📈 Как работает стратегия

### Основная идея

```
Начальное состояние:
  LONG:  $75 @ $0.100 (маржа $1, leverage 75x)
  SHORT: $75 @ $0.100 (маржа $1, leverage 75x)

Цена падает до $0.099 (-1.0%):
  LONG усреднение #1: $150 @ $0.099 (маржа $2)
  Total LONG: $225 @ avg $0.09933

Цена растет до $0.101 (+1.0%):
  SHORT усреднение #1: $150 @ $0.101 (маржа $2)
  Total SHORT: $225 @ avg $0.10067

Take Profit триггеры:
  LONG TP:  $0.09933 × 1.01 = $0.10032
  SHORT TP: $0.10067 × 0.99 = $0.09966
```

### Мартингейл-прогрессия (multiplier 2.0)

| Level | Margin | Position @ 75x | Cumulative Margin | Cumulative Position |
|-------|--------|----------------|-------------------|---------------------|
| 0     | $1     | $75            | $1                | $75                 |
| 1     | $2     | $150           | $3                | $225                |
| 2     | $4     | $300           | $7                | $525                |
| 3     | $8     | $600           | $15               | $1,125              |
| 5     | $32    | $2,400         | $63               | $4,725              |
| 7     | $128   | $9,600         | $255              | $19,125             |
| 10    | $1,024 | $76,800        | **$2,047**        | **$153,525**        |

**Вывод:** 10 уровней требуют 2,047× начальной маржи!

### 7-фазная защита

```
Level 1: ПРЕВЕНТИВНАЯ → Early Freeze (available < next × 1.5)
              ↓
Level 2: КРИТИЧЕСКАЯ → Panic Mode (available < next × 3)
              ↓
Level 3: КАТАСТРОФА → Emergency Close (MM Rate ≥ 90%)
```

Подробности в [docs/04-risk-management.md](docs/04-risk-management.md)

---

## ⚙️ Быстрая настройка

### 1. Установка

```bash
git clone <repository-url>
cd sol-trader
~/.local/bin/uv venv
source .venv/bin/activate
~/.local/bin/uv pip install -r requirements.txt
```

### 2. API ключи (.env)

```bash
# Account 1 (Demo)
1_BYBIT_API_KEY=your_demo_api_key
1_BYBIT_API_SECRET=your_demo_api_secret
```

Получить demo keys: https://testnet.bybit.com

### 3. Конфигурация (config/config.yaml)

```yaml
accounts:
  - id: 1
    name: "Demo Account"
    api_key_env: "1_BYBIT_API_KEY"
    api_secret_env: "1_BYBIT_API_SECRET"
    demo_trading: true                       # Demo режим
    dry_run: false                           # Реальные API вызовы

    risk_management:
      mm_rate_threshold: 90.0                # Emergency close при MM Rate >= 90%

    strategies:                              # ОБЯЗАТЕЛЬНО хотя бы одна!
      - symbol: "DOGEUSDT"
        leverage: 75                         # ВЫСОКИЙ РИСК!
        initial_position_size_usd: 1.0       # Начальная маржа
        grid_step_percent: 1.0               # Шаг сетки
        averaging_multiplier: 2.0            # Мартингейл
        take_profit_percent: 1.0             # Take Profit
        max_grid_levels_per_side: 10         # Максимум усреднений
```

### 4. Запуск

```bash
python src/main.py
```

См. полную инструкцию в [docs/01-getting-started.md](docs/01-getting-started.md)

---

## 💡 Рекомендации

### Консервативные параметры (для начинающих)

```yaml
leverage: 15                    # Вместо 75
grid_step_percent: 2.5         # Вместо 1.0
averaging_multiplier: 1.4      # Вместо 2.0
take_profit_percent: 1.5       # Вместо 1.0
max_grid_levels_per_side: 6    # Вместо 10
mm_rate_threshold: 60.0        # Вместо 90.0
```

**Эффект:** Риск снижен в ~50× при снижении прибыльности на ~20-30%.

### Чек-лист перед запуском

- [ ] Протестировано на **demo минимум 2-4 недели**
- [ ] Понимаете **все риски** (читали docs/02-strategy.md)
- [ ] Видели как бот ведет себя в **разных рыночных условиях**
- [ ] Знаете как **остановить бота** в критической ситуации
- [ ] Для production: начинаете с **минимальной суммы** ($50-$100)
- [ ] **НЕ** вкладываете деньги, которые не можете потерять

---

## 📂 Структура проекта

```
sol-trader/
├── config/
│   ├── config.yaml              # Конфигурация аккаунтов
│   ├── constants.py             # Константы (лимиты, fees)
│   └── .env.example             # Шаблон для API keys
│
├── src/
│   ├── core/                    # Ядро (multi-account bot)
│   ├── exchange/                # Bybit API (HTTP + WebSocket)
│   ├── strategy/                # Grid strategy + position manager
│   ├── utils/                   # Утилиты (balance, logger, etc.)
│   ├── analytics/               # Metrics tracker
│   └── main.py                  # Entry point
│
├── tests/                       # 172 теста
│   ├── test_grid_strategy.py
│   ├── test_position_manager.py
│   └── ...
│
├── scripts/                     # Utility scripts
│   ├── analyze.py               # Анализ результатов
│   ├── check_balance.py         # Проверка баланса
│   └── setup_service.sh         # Установка systemd service
│
├── logs/                        # Логи (auto-created)
│   ├── 001_bot_YYYY-MM-DD.log
│   ├── 001_trades_YYYY-MM-DD.log
│   └── 001_positions_YYYY-MM-DD.log
│
├── data/                        # Данные (auto-created)
│   ├── 001_bot_state.json
│   ├── 001_performance_metrics.csv
│   └── 001_trades_history.csv
│
└── docs/                        # Документация
    ├── 01-getting-started.md
    ├── 02-strategy.md
    ├── 03-configuration.md
    ├── 04-risk-management.md
    ├── 05-operations.md
    ├── 06-analytics.md
    ├── 07-multi-symbol.md
    ├── 08-architecture.md
    ├── 09-development.md
    ├── 10-faq.md
    └── 11-tp-distance.md
```

---

## 🔬 Технологии

- **Python 3.9+** - Язык программирования
- **pybit** - Official Bybit API client (HTTP + WebSocket)
- **python-dotenv** - Environment variables
- **pyyaml** - YAML configuration
- **pandas** - Analytics
- **pytest** - Testing framework (172 тестов)

---

## 📊 Статус разработки

### Завершено ✅
- ✅ Multi-account архитектура с полной изоляцией
- ✅ WebSocket-first (Position + Wallet + Order + Execution)
- ✅ Thread safety (locks для всех shared resources)
- ✅ Advanced Risk Management v3.1 (7-фазная система)
- ✅ Adaptive reopen + intelligent TP management
- ✅ 172 comprehensive тестов
- ✅ Systemd service integration

### В процессе 🚧
- 🚧 WebSocket integration testing (Section 11, 3 задачи)
- 🚧 Phase 2-3 улучшений (medium/low priority)

### Запланировано 📋
- 📋 Веб-интерфейс для SaaS модели
- 📋 Real-time dashboard
- 📋 Telegram notifications
- 📋 Advanced analytics (Sharpe ratio, drawdown, etc.)

---

## 🤝 Вклад в проект

Приветствуются:
- Bug reports
- Feature requests
- Code improvements
- Documentation updates

**Before contributing:**
1. Прочитайте [CLAUDE.md](CLAUDE.md) - техническая спецификация
2. Запустите тесты: `pytest tests/ -v`
3. Убедитесь что все тесты проходят

---

## ⚖️ Лицензия

MIT License

---

## ⚠️ DISCLAIMER

Автор не несет ответственности за финансовые потери. Торговля с высоким плечом сопряжена с **экстремальными рисками** и может привести к **полной потере депозита**.

**Используйте на свой страх и риск.**

Рекомендации:
- ✅ Demo trading для тестирования (минимум 2-4 недели)
- ✅ Консервативные параметры для начинающих
- ✅ Постоянный мониторинг Account MM Rate
- ✅ Доверие системе защиты v3.1
- ❌ НЕ используйте агрессивные параметры без опыта
- ❌ НЕ вкладывайте деньги, которые не можете потерять

---

**Готово к запуску!** См. [docs/01-getting-started.md](docs/01-getting-started.md) для начала работы. 🚀
