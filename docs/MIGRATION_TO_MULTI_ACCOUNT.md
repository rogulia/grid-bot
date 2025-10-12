# План Миграции на Multi-Account Архитектуру

**Дата начала:** 2025-10-10
**Статус:** 🚧 В процессе
**Цель:** Переделать бот для работы с несколькими независимыми пользователями/аккаунтами с полной изоляцией данных и эффективным WebSocket sharing.

---

## 🎯 Общая Концепция

### Ключевые Изменения
- ✅ Один аккаунт = один пользователь/клиент
- ✅ Все настройки (включая риск-лимиты) - индивидуальные для каждого аккаунта
- ✅ WebSocket sharing по ключу `(symbol, demo: bool)`
- ✅ Полная изоляция файлов, позиций, метрик
- ✅ Готовность к SaaS модели (веб-интерфейс в будущем)

### WebSocket Sharing Логика
```
Ключ WebSocket: (symbol, demo: bool)

Примеры:
- 3 аккаунта торгуют SOLUSDT в demo → 1 WebSocket
- 2 аккаунта SOLUSDT demo + 1 аккаунт SOLUSDT prod → 2 WebSocket
- Account1: SOL+DOGE (demo), Account2: SOL (prod) → 3 WebSocket
```

---

## 📋 Чек-лист Выполнения

### Фаза 1: Создание Новых Компонентов

#### 1.1 TradingAccount Class
- [x] Создать `src/core/trading_account.py`
- [ ] Реализовать `__init__()` с параметрами:
  - [ ] `name: str` - имя аккаунта/пользователя
  - [ ] `api_key`, `api_secret` - уникальные credentials
  - [ ] `demo: bool` - среда (demo/prod)
  - [ ] `dry_run: bool` - режим симуляции
  - [ ] `strategies_config: List[Dict]` - стратегии пользователя
  - [ ] `risk_config: Dict` - **ИНДИВИДУАЛЬНЫЕ** риск-лимиты
- [ ] Реализовать `async initialize()`:
  - [ ] **КРИТИЧНО:** Проверить emergency stop файл `data/.emergency_stop_{account_name}`
  - [ ] Если файл существует → показать ошибку, не запускать аккаунт
  - [ ] Получение баланса из exchange
  - [ ] Создание `MetricsTracker` с префиксом имени аккаунта
  - [ ] Создание `GridStrategy` для каждого символа (**передать `account_name`!**)
  - [ ] Создание `StateManager` для каждого символа
  - [ ] Настройка leverage и hedge mode
  - [ ] Начальная синхронизация с exchange
- [ ] Реализовать `process_price(symbol, price)`:
  - [ ] **Проверка:** если `strategy.is_stopped()` → пропустить
  - [ ] Вызов `strategy.on_price_update()`
  - [ ] Периодическая синхронизация (каждые 60 сек)
  - [ ] Логирование PnL
  - [ ] Запись метрик
- [ ] Реализовать `get_symbols() -> List[str]`
- [ ] Реализовать `is_stopped() -> bool` - проверка emergency stop для всех стратегий
- [ ] Реализовать `async shutdown()`
- [ ] Добавить логирование с префиксом `[{account_name}][{symbol}]`

**Тесты:**
- [ ] Unit test для `TradingAccount.__init__()`
- [ ] Unit test для `process_price()`
- [ ] Unit test для emergency stop detection при старте
- [ ] Integration test с mock BybitClient

**⚠️ КРИТИЧНО - Emergency Stop:**
- Emergency stop файл теперь per-account: `data/.emergency_stop_{account_name}`
- При инициализации проверять наличие файла
- Если файл существует - НЕ запускать аккаунт, показать ошибку с инструкциями
- Пользователь должен вручную удалить файл после исправления проблемы

---

#### 1.2 MultiAccountBot Orchestrator
- [ ] Создать `src/core/multi_account_bot.py`
- [ ] Реализовать `__init__()`:
  - [ ] `price_feeds: dict[tuple[str, bool], BybitWebSocket]`
  - [ ] `subscribers: dict[tuple[str, bool], List[TradingAccount]]`
  - [ ] `accounts: List[TradingAccount]`
- [ ] Реализовать `register_account(account: TradingAccount)`:
  - [ ] Добавление аккаунта в список
  - [ ] Регистрация подписок на символы
  - [ ] Создание WebSocket если еще нет
- [ ] Реализовать `_create_websocket(symbol, demo)`:
  - [ ] Создание BybitWebSocket с callback
  - [ ] Сохранение в `price_feeds`
  - [ ] Логирование создания
- [ ] Реализовать `_on_price_update(symbol, demo, price)`:
  - [ ] Получение списка подписчиков по ключу `(symbol, demo)`
  - [ ] Broadcast цены всем подписчикам
  - [ ] Error handling (ошибка одного аккаунта не должна влиять на других)
- [ ] Реализовать `get_stats() -> Dict`:
  - [ ] Количество аккаунтов
  - [ ] Количество WebSocket
  - [ ] Распределение подписчиков по WebSocket
- [ ] Реализовать `async shutdown()`:
  - [ ] Остановка всех аккаунтов
  - [ ] Остановка всех WebSocket

**Тесты:**
- [ ] Unit test для WebSocket sharing (одинаковые (symbol, demo) → один WS)
- [ ] Unit test для разделения demo/prod (разные WebSocket)
- [ ] Integration test с несколькими TradingAccount

---

### Фаза 2: Обновление Существующих Компонентов

#### 2.1 Config Updates
- [ ] Обновить `config/config.yaml`:
  - [ ] Удалить глобальные секции `strategies` и `risk_management`
  - [ ] Создать секцию `accounts: []`
  - [ ] Для каждого аккаунта добавить:
    - [ ] `name`
    - [ ] `api_key_env`, `api_secret_env`
    - [ ] `demo_trading`
    - [ ] `dry_run`
    - [ ] `risk_management` (индивидуальные!)
    - [ ] `strategies` (индивидуальные!)
  - [ ] Оставить только `bot.log_level` как глобальную настройку
- [ ] Обновить `config/.env.example`:
  - [ ] Показать примеры для нескольких аккаунтов
  - [ ] `BYBIT_API_KEY_USER1`, `BYBIT_API_SECRET_USER1`
  - [ ] `BYBIT_API_KEY_USER2`, `BYBIT_API_SECRET_USER2`
  - [ ] Добавить комментарии о demo/prod

**Тесты:**
- [ ] Валидация нового формата config
- [ ] Проверка backward compatibility error (старый формат → понятная ошибка)

---

#### 2.2 ConfigLoader Updates
- [ ] Обновить `src/utils/config_loader.py`:
  - [ ] Добавить `get_accounts_config() -> List[Dict]`
    - [ ] Возврат списка аккаунтов
    - [ ] Валидация: accounts существует и не пустой
    - [ ] Понятная ошибка если старый формат
  - [ ] Добавить `get_account_credentials(api_key_env, api_secret_env) -> tuple`
    - [ ] Получение credentials по имени env переменной
    - [ ] Валидация: переменные существуют
    - [ ] Понятная ошибка если credentials не найдены
  - [ ] Добавить `validate_account_config(account_config: Dict)`
    - [ ] Проверка обязательных полей: name, api_key_env, api_secret_env, demo_trading, strategies
    - [ ] Проверка наличия `risk_management` секции
    - [ ] Проверка что strategies не пустой
    - [ ] Валидация формата risk_management полей
  - [ ] Пометить `get_strategy_config()` как deprecated (для backward compatibility)

**Тесты:**
- [ ] Unit test для `get_accounts_config()`
- [ ] Unit test для `get_account_credentials()`
- [ ] Unit test для `validate_account_config()`
- [ ] Test error handling (missing fields, empty accounts)

---

#### 2.3 StateManager Updates
- [ ] Обновить `src/core/state_manager.py`:
  - [ ] Добавить параметр `account_name: str` в `__init__()`
  - [ ] Auto-generate filename: `data/bot_state_{account_name}.json`
  - [ ] Обновить logger name: `sol-trader.state.{account_name}`
  - [ ] Обновить все log messages с префиксом `[{account_name}]`

**Тесты:**
- [ ] Unit test для генерации имени файла
- [ ] Integration test: несколько StateManager с разными account_name → разные файлы

---

#### 2.4 MetricsTracker Updates
- [ ] Проверить `src/analytics/metrics_tracker.py`:
  - [ ] ✅ Уже поддерживает `file_prefix` - изменений не требуется!
  - [ ] Документировать использование: `file_prefix=f"{account_name}_"`

**Результат файлов:**
```
data/
├── user_john_bot_state.json
├── user_john_trades_history.csv
├── user_john_performance_metrics.csv
├── user_john_summary_report.json
├── user_alice_bot_state.json
├── user_alice_trades_history.csv
...
```

---

#### 2.5 GridStrategy Updates
- [ ] Обновить `src/strategy/grid_strategy.py`:
  - [ ] Добавить параметр `account_name: str` в `__init__()`
  - [ ] Сохранить `self.account_name = account_name`
  - [ ] Обновить logger: `logging.getLogger(f"sol-trader.strategy.{account_name}")`
  - [ ] Обновить ВСЕ log messages:
    - [ ] Заменить `f"[{self.symbol}]"` на `f"[{self.account_name}][{self.symbol}]"`
    - [ ] Проверить все методы: `_check_grid_entries`, `_check_take_profit`, `_check_risk_limits`
  - [ ] Передавать `account_name` в StateManager при создании

**Файлы для проверки логов:**
- [ ] `_check_grid_entries()` - логи добавления позиций
- [ ] `_check_take_profit()` - логи закрытия позиций
- [ ] `_check_risk_limits()` - логи MM rate, exposure
- [ ] `sync_with_exchange()` - логи синхронизации
- [ ] `_update_tp_order()` - логи TP ордеров

**Тесты:**
- [ ] Unit test для логирования с account_name
- [ ] Integration test: два GridStrategy с разными account_name → разные логи

---

#### 2.6 PositionManager Updates
- [ ] Обновить `src/strategy/position_manager.py`:
  - [ ] Добавить параметр `account_name: str` в `__init__()`
  - [ ] Обновить logger: `logging.getLogger(f"sol-trader.position.{account_name}")`
  - [ ] Обновить log messages с префиксом `[{account_name}][{symbol}]`

**Тесты:**
- [ ] Unit test для логирования с account_name

---

#### 2.7 Main.py Complete Rewrite
- [ ] УДАЛИТЬ старый класс `TradingBot`
- [ ] Создать новый класс `MultiAccountOrchestrator`:
  - [ ] `__init__()`:
    - [ ] `self.config = ConfigLoader()`
    - [ ] `self.logger = setup_logger()`
    - [ ] `self.bot = MultiAccountBot()`
  - [ ] `async initialize()`:
    - [ ] Загрузка `accounts_config = config.get_accounts_config()`
    - [ ] Логирование статистики (количество аккаунтов)
    - [ ] Для каждого аккаунта:
      - [ ] Валидация конфигурации
      - [ ] Получение credentials
      - [ ] Создание `TradingAccount`
      - [ ] Вызов `await account.initialize()`
      - [ ] Регистрация `bot.register_account(account)`
    - [ ] Логирование итоговой статистики (WebSocket count, distribution)
    - [ ] Error handling: если один аккаунт упал → логировать и прервать (fail-fast)
  - [ ] `async run()`:
    - [ ] `await self.initialize()`
    - [ ] Main loop: `while self.running: await asyncio.sleep(1)`
  - [ ] `async shutdown()`:
    - [ ] `await self.bot.shutdown()`
- [ ] Обновить `main()`:
  - [ ] Создать `MultiAccountOrchestrator`
  - [ ] Setup signal handlers
  - [ ] `asyncio.run(orchestrator.run())`

**Тесты:**
- [ ] Integration test: запуск с 2 demo аккаунтами
- [ ] Integration test: проверка WebSocket sharing
- [ ] Integration test: проверка изоляции файлов

---

### Фаза 3: Тестирование и Валидация

#### 3.1 Unit Tests
- [ ] Тесты для `TradingAccount`:
  - [ ] Инициализация
  - [ ] `process_price()`
  - [ ] `get_symbols()`
  - [ ] Изоляция данных
- [ ] Тесты для `MultiAccountBot`:
  - [ ] WebSocket sharing (одинаковые (symbol, demo) → 1 WS)
  - [ ] Разделение demo/prod (разные WebSocket)
  - [ ] Broadcast цен
  - [ ] Error handling
- [ ] Тесты для `ConfigLoader`:
  - [ ] `get_accounts_config()`
  - [ ] `validate_account_config()`
  - [ ] Backward compatibility errors
- [ ] Тесты для обновленных компонентов:
  - [ ] `StateManager` с `account_name`
  - [ ] `GridStrategy` с `account_name`
  - [ ] Логирование с префиксами

**Команда:**
```bash
pytest tests/ -v -k "multi_account or trading_account"
```

---

#### 3.2 Integration Tests

**Test 1: Два Demo Аккаунта, Один Символ**
- [ ] Создать config с 2 аккаунтами (demo, SOLUSDT)
- [ ] Запустить бот
- [ ] Проверить:
  - [ ] Создан только 1 WebSocket
  - [ ] Оба аккаунта получают цены
  - [ ] Оба открывают позиции независимо
  - [ ] Файлы разделены: `user1_*.csv`, `user2_*.csv`
  - [ ] State файлы: `bot_state_user1.json`, `bot_state_user2.json`

**Test 2: Demo + Prod, Один Символ**
- [ ] 1 аккаунт demo SOLUSDT, 1 аккаунт prod SOLUSDT
- [ ] Проверить:
  - [ ] Создано **2 WebSocket** (разные среды!)
  - [ ] Demo позиции на testnet.bybit.com
  - [ ] Prod позиции на bybit.com
  - [ ] Независимые балансы

**Test 3: Разные Риск-Лимиты**
- [ ] User1: max_exposure=500, liquidation_buffer=0.8
- [ ] User2: max_exposure=2000, liquidation_buffer=0.5
- [ ] Проверить:
  - [ ] User1 останавливается при $500 exposure
  - [ ] User2 продолжает до $2000 exposure
  - [ ] Независимые emergency close triggers

**Test 4: Логирование и Фильтрация**
- [ ] Запустить с 3 аккаунтами
- [ ] Проверить фильтрацию:
  - [ ] `grep "\[user_john\]" logs/bot_*.log`
  - [ ] `grep "\[user_alice\]\[SOLUSDT\]" logs/bot_*.log`
  - [ ] Каждый аккаунт имеет свои логи

**Test 5: Ошибка в Одном Аккаунте**
- [ ] User1: правильные credentials
- [ ] User2: неправильные credentials (ошибка при инициализации)
- [ ] Проверить:
  - [ ] Бот падает с понятной ошибкой (fail-fast)
  - [ ] Указывает какой аккаунт упал
  - [ ] НЕ запускается частично

---

#### 3.3 Manual Testing with Real Demo Accounts

**Подготовка:**
- [ ] Создать 2 demo аккаунта на testnet.bybit.com
- [ ] Получить API ключи для обоих
- [ ] Добавить в `.env`:
  ```bash
  BYBIT_API_KEY_DEMO1=...
  BYBIT_API_SECRET_DEMO1=...
  BYBIT_API_KEY_DEMO2=...
  BYBIT_API_SECRET_DEMO2=...
  ```

**Config:**
```yaml
accounts:
  - name: "demo_user1"
    api_key_env: "BYBIT_API_KEY_DEMO1"
    api_secret_env: "BYBIT_API_SECRET_DEMO1"
    demo_trading: true
    dry_run: false
    risk_management:
      max_total_exposure: 500.0
      liquidation_buffer: 0.8
      emergency_stop_loss: -250.0
    strategies:
      - symbol: "SOLUSDT"
        leverage: 75
        initial_position_size_usd: 1.0
        grid_step_percent: 1.0
        averaging_multiplier: 2.0
        take_profit_percent: 1.0
        max_grid_levels_per_side: 8

  - name: "demo_user2"
    api_key_env: "BYBIT_API_KEY_DEMO2"
    api_secret_env: "BYBIT_API_SECRET_DEMO2"
    demo_trading: true
    dry_run: false
    risk_management:
      max_total_exposure: 300.0
      liquidation_buffer: 1.0
      emergency_stop_loss: -150.0
    strategies:
      - symbol: "SOLUSDT"
        leverage: 50
        initial_position_size_usd: 0.5
        grid_step_percent: 1.5
        averaging_multiplier: 1.8
        take_profit_percent: 1.2
        max_grid_levels_per_side: 6
```

**Тестовый сценарий:**
- [ ] Запустить бот: `python src/main.py`
- [ ] Проверить логи:
  - [ ] Оба аккаунта инициализированы
  - [ ] Создан 1 WebSocket (SOLUSDT, demo)
  - [ ] Оба получают цены
- [ ] Дождаться первого усреднения:
  - [ ] Проверить что каждый аккаунт усредняется по своим настройкам
  - [ ] User1: multiplier 2.0
  - [ ] User2: multiplier 1.8
- [ ] Проверить файлы:
  - [ ] `data/demo_user1_trades_history.csv` - только сделки user1
  - [ ] `data/demo_user2_trades_history.csv` - только сделки user2
  - [ ] Разные `bot_state_*.json`
- [ ] Проверить на бирже:
  - [ ] Аккаунт 1: свои позиции (leverage 75x)
  - [ ] Аккаунт 2: свои позиции (leverage 50x)
- [ ] Остановить бот (Ctrl+C)
- [ ] Проверить graceful shutdown:
  - [ ] Оба аккаунта сохранили state
  - [ ] WebSocket остановлен
  - [ ] Summary reports созданы для каждого

---

### Фаза 4: Документация

#### 4.1 Code Documentation
- [ ] Добавить docstrings во все новые классы
- [ ] Добавить примеры использования в docstrings
- [ ] Обновить комментарии в сложных местах (WebSocket sharing логика)

#### 4.2 User Documentation
- [ ] Обновить `README.md`:
  - [ ] Секция "Multi-Account Support"
  - [ ] Примеры конфигурации
  - [ ] Команды для фильтрации логов по аккаунту
- [ ] Обновить `docs/CLAUDE.md`:
  - [ ] Секция "Multi-Account Architecture"
  - [ ] WebSocket sharing принципы
  - [ ] File isolation схема
  - [ ] Готовность к SaaS
- [ ] Создать `docs/MULTI_ACCOUNT_GUIDE.md`:
  - [ ] Детальный гайд по настройке multiple accounts
  - [ ] Примеры use cases:
    - [ ] Несколько пользователей на одном боте
    - [ ] Разные стратегии для разных аккаунтов
    - [ ] Mixing demo и prod
  - [ ] Best practices
  - [ ] Troubleshooting
- [ ] Создать `docs/SAAS_ROADMAP.md`:
  - [ ] Что уже готово
  - [ ] Что нужно для полноценного SaaS:
    - [ ] Web interface (Flask/FastAPI)
    - [ ] Database-backed config (PostgreSQL)
    - [ ] User authentication
    - [ ] API для управления
    - [ ] Billing/subscription system

#### 4.3 Migration Guide
- [ ] Создать `docs/MIGRATION_FROM_SINGLE_ACCOUNT.md`:
  - [ ] Как мигрировать старый config на новый формат
  - [ ] Script для автоматической миграции config
  - [ ] Checklist для миграции
  - [ ] Common pitfalls

---

### Фаза 5: Deployment и Production Readiness

#### 5.1 Service Configuration
- [ ] Обновить `scripts/setup_service.sh`:
  - [ ] Проверить что работает с новой архитектурой
  - [ ] Обновить описание сервиса (multi-account)
- [ ] Обновить `scripts/bot_control.sh`:
  - [ ] Добавить команду для фильтрации логов по аккаунту
  - [ ] `./bot_control.sh logs-account user_john`

#### 5.2 Monitoring Scripts
- [ ] Создать `scripts/monitor_accounts.sh`:
  - [ ] Показать статус каждого аккаунта
  - [ ] Баланс, позиции, PnL
- [ ] Создать `scripts/analyze_account.py`:
  - [ ] Аналитика для конкретного аккаунта
  - [ ] `python scripts/analyze_account.py --account user_john --plot`

#### 5.3 Pre-Launch Checks
- [ ] Обновить `scripts/pre_launch_check.py`:
  - [ ] Проверка config в multi-account формате
  - [ ] Валидация credentials для всех аккаунтов
  - [ ] Проверка балансов всех аккаунтов
  - [ ] Проверка hedge mode для всех аккаунтов

---

## 🚀 Готовность к Production

### Checklist перед запуском
- [ ] Все unit tests проходят
- [ ] Все integration tests проходят
- [ ] Manual testing на demo прошел успешно
- [ ] Документация обновлена
- [ ] Migration guide готов
- [ ] Monitoring scripts работают
- [ ] Pre-launch check проходит для всех аккаунтов
- [ ] Backup старого кода создан
- [ ] Git commit с четким описанием изменений
- [ ] GitHub push

### Первый Production Deploy (осторожно!)
- [ ] Начать с 1 аккаунта в prod (минимальный размер позиций)
- [ ] Мониторить первые 24 часа
- [ ] Если всё ОК → добавить еще аккаунты постепенно

---

## 📊 Метрики Успеха

- [ ] Бот стабильно работает с 5+ аккаунтами одновременно
- [ ] WebSocket sharing работает корректно (экономия ресурсов)
- [ ] Файлы изолированы (нет смешивания данных)
- [ ] Индивидуальные риск-лимиты работают корректно
- [ ] Ошибка одного аккаунта не влияет на других
- [ ] Логи легко фильтруются по аккаунту
- [ ] Готовность к добавлению веб-интерфейса

---

## 🔮 Следующие Шаги (Post-Migration)

1. **Web Interface (Flask/FastAPI)**
   - API endpoints для управления аккаунтами
   - Dashboard для просмотра позиций
   - Real-time WebSocket для UI updates

2. **Database Backend**
   - PostgreSQL для хранения конфигурации
   - Миграция с YAML на DB
   - User authentication

3. **Billing System**
   - Subscription management
   - Payment integration
   - Usage tracking

4. **Advanced Features**
   - Per-account scheduling (когда торговать)
   - Per-account notifications (Telegram, Email)
   - Per-account backtesting

---

**Последнее обновление:** 2025-10-10
**Следующий шаг:** Начать с Фазы 1.1 - создание TradingAccount class
