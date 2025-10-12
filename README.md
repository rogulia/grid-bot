# 🤖 SOL-Trader

**Мульти-аккаунтный** автоматический торговый бот для фьючерсов Bybit с grid-стратегией двустороннего хеджирования (LONG + SHORT одновременно). Поддерживает несколько изолированных аккаунтов для SaaS модели.

## 📋 Описание стратегии

Каждый аккаунт открывает одновременно LONG и SHORT позиции и:
- **Усредняет** позицию при движении цены на заданный % (по умолчанию 1%)
- **Увеличивает** размер каждой новой позиции используя классический мартингейл (1 → 2 → 4 → 8 → 16...)
- **Фиксирует прибыль** при отскоке цены на заданный % в обратную сторону
- **Защищает от ликвидации** используя Account Maintenance Margin Rate с биржи (настраивается!)
- **Изолирует данные** - каждый аккаунт имеет свои файлы, логи, риск-лимиты

## ⚠️ Предупреждение

**ВЫСОКИЙ РИСК!** Использование высокого плеча (75-100x) крайне опасно:
- На Cross Margin ликвидация учитывает весь баланс аккаунта
- Каждая позиция (LONG/SHORT) ликвидируется отдельно несмотря на хеджирование
- Рекомендуется начать с плеча 25-50x для тестирования
- **ВСЕГДА** тестируйте на demo счете перед использованием реальных средств!

## 🚀 Быстрый старт

### 1. Установка

```bash
# Клонировать репозиторий (если есть git)
git clone <your-repo-url>
cd sol-trader

# Или если уже в директории проекта:
# Активировать виртуальное окружение
source .venv/bin/activate
```

### 2. Настройка API ключей

1. Получите API ключи от Bybit Demo Trading:
   - Перейдите на [https://testnet.bybit.com](https://testnet.bybit.com)
   - Войдите через GitHub или создайте аккаунт
   - Перейдите в API Management
   - Создайте новый API ключ с разрешениями на торговлю

2. Создайте файл `.env`:

```bash
cp config/.env.example .env
```

3. Заполните `.env` своими ключами (**формат с ID префиксом!**):

```bash
# Account 1 (Demo)
1_BYBIT_API_KEY=your_api_key_here
1_BYBIT_API_SECRET=your_api_secret_here

# Account 2 (опционально)
# 2_BYBIT_API_KEY=your_api_key_here
# 2_BYBIT_API_SECRET=your_api_secret_here
```

**Важно:** ID в `.env` должен совпадать с `id` в `config.yaml`!

### 3. Настройка стратегии

Отредактируйте `config/config.yaml`:

```yaml
accounts:
  # Аккаунт 1 (Demo)
  - id: 1                                  # Уникальный ID (совпадает с .env!)
    name: "My Demo Account"                 # Название для логов
    api_key_env: "1_BYBIT_API_KEY"         # Переменная из .env
    api_secret_env: "1_BYBIT_API_SECRET"
    demo_trading: true                      # true = demo, false = PRODUCTION!
    dry_run: false                          # false = реальные API вызовы

    # Риск-менеджмент (для каждого аккаунта отдельно!)
    risk_management:
      mm_rate_threshold: 90.0              # Закрывать при Account MM Rate >= 90%

    # Стратегии (можно торговать несколько монет!)
    strategies:
      - symbol: "DOGEUSDT"                 # Рекомендуется для тестирования
        category: "linear"                  # USDT Perpetual
        leverage: 75                        # Плечо 75x
        initial_position_size_usd: 1.0     # МАРЖА в USD (не размер позиции!)
        grid_step_percent: 1.0             # Шаг усреднения в %
        averaging_multiplier: 2.0          # Мартингейл (1→2→4→8→16)
        take_profit_percent: 1.0           # Take profit в %
        max_grid_levels_per_side: 10       # Максимум grid уровней

      # Можно добавить ещё монеты:
      # - symbol: "TONUSDT"
      #   leverage: 50
      #   initial_position_size_usd: 1.0
      #   grid_step_percent: 1.0
      #   averaging_multiplier: 2.0
      #   take_profit_percent: 1.0

  # Аккаунт 2 (опционально) - раскомментируйте для добавления:
  # - id: 2
  #   name: "Second Account"
  #   api_key_env: "2_BYBIT_API_KEY"
  #   api_secret_env: "2_BYBIT_API_SECRET"
  #   demo_trading: true
  #   dry_run: false
  #   risk_management:
  #     mm_rate_threshold: 50.0             # Более консервативный порог
  #   strategies:
  #     - symbol: "BTCUSDT"
  #       leverage: 25
  #       ...

bot:
  log_level: "INFO"                        # DEBUG | INFO | WARNING | ERROR
```

**ВАЖНО:**
- `initial_position_size_usd` — это **МАРЖА**, не размер позиции! (С плечом 75x: маржа $1 = позиция $75)
- `mm_rate_threshold` — порог Account MM Rate для emergency close (90% = агрессивно, 50% = консервативно)
- Каждый аккаунт изолирован: свои ключи, лимиты, файлы

### 4. Запуск

#### Вариант A: Запуск в терминале (для тестирования)

```bash
# Активировать venv (если еще не активирован)
source .venv/bin/activate

# Запустить бота
python src/main.py
```

Для остановки нажмите `Ctrl+C`.

#### Вариант B: Запуск как systemd service (для постоянной работы)

```bash
# Установить сервис (один раз)
sudo bash scripts/setup_service.sh

# Запустить бота
sudo systemctl start sol-trader

# Смотреть логи
sudo journalctl -u sol-trader -f
```

Подробнее см. [BACKGROUND_SERVICE_GUIDE.md](docs/BACKGROUND_SERVICE_GUIDE.md)

## 📊 Режимы работы

### Dry Run (Симуляция)
```yaml
bot:
  dry_run: true
```
- Ордера НЕ выставляются на биржу
- Все действия только в логах
- Безопасно для тестирования логики

### Demo Trading (рекомендуется для начала)
```yaml
bot:
  dry_run: false

exchange:
  demo_trading: true
```
- Реальные API вызовы к demo серверу
- Виртуальные деньги (50,000 USDT автоматически)
- Полная имитация реальной торговли

### Production (НЕ РЕКОМЕНДУЕТСЯ без опыта!)
```yaml
bot:
  dry_run: false

exchange:
  demo_trading: false
```
- Реальные деньги в риске!
- Используйте только после тщательного тестирования!

## 📁 Структура проекта

```
sol-trader/
├── config/
│   ├── config.yaml          # Конфигурация стратегии
│   └── .env                 # API ключи (НЕ коммитить!)
├── src/
│   ├── exchange/            # Интеграция с Bybit
│   │   ├── bybit_client.py      # HTTP API
│   │   └── bybit_websocket.py   # WebSocket (real-time цены)
│   ├── strategy/            # Логика стратегии
│   │   ├── grid_strategy.py     # Grid стратегия
│   │   └── position_manager.py  # Управление LONG/SHORT
│   ├── core/                # Ядро системы
│   │   └── state_manager.py     # Сохранение состояния
│   ├── analytics/           # Аналитика
│   │   └── metrics_tracker.py   # Метрики и отчеты
│   ├── utils/               # Утилиты
│   │   ├── logger.py            # Логирование
│   │   ├── config_loader.py     # Загрузка конфига
│   │   └── timezone.py          # Работа с timezone
│   └── main.py              # Точка входа
├── tests/                   # 113 тестов
├── logs/                    # Логи бота (создаются автоматически)
├── data/                    # Данные и метрики
│   ├── bot_state.json           # Состояние позиций
│   ├── performance_metrics.csv  # Метрики каждые 60 сек
│   ├── trades_history.csv       # История сделок
│   └── summary_report.json/txt  # Финальный отчет
├── scripts/                 # Скрипты управления
│   ├── analyze.py               # Анализ результатов
│   ├── setup_service.sh         # Установка systemd
│   └── bot_control.sh           # Управление ботом
├── CLAUDE.md                # Инструкции для Claude Code
├── README.md                # Этот файл
└── docs/                    # Документация
    ├── MULTI_SYMBOL.md          # Гайд по мультивалютной торговле
    ├── BACKGROUND_SERVICE_GUIDE.md  # Гайд по запуску в фоне
    ├── TESTING.md               # Гайд по тестированию
    └── ANALYTICS_GUIDE.md       # Гайд по аналитике
```

## 📈 Мониторинг

### Логи (per-account с ID префиксом)

Бот создает отдельные логи для каждого аккаунта:

1. **Основной лог аккаунта** (`logs/001_bot_YYYY-MM-DD.log`):
   - Все события аккаунта 001 с префиксом `[SYMBOL]`
   - Ошибки и предупреждения
   - Состояние позиций каждые 60 секунд

2. **Лог сделок** (`logs/001_trades_YYYY-MM-DD.log`):
   - Каждая открытая/закрытая позиция
   - Цена, размер, причина, PnL

3. **Системный лог** (`logs/main_YYYY-MM-DD.log`):
   - Координация между аккаунтами
   - Статистика WebSocket'ов

4. **Системные логи** (если используется systemd):
   ```bash
   sudo journalctl -u sol-trader -f

   # Только для аккаунта 001:
   sudo journalctl -u sol-trader -f | grep "\[001\]"

   # Только для монеты DOGEUSDT аккаунта 001:
   sudo journalctl -u sol-trader -f | grep "\[001\]" | grep "DOGEUSDT"
   ```

### Данные (per-account)

- **data/001_bot_state.json** - текущие позиции аккаунта 001
- **data/001_performance_metrics.csv** - снэпшоты каждые 60 сек
- **data/001_trades_history.csv** - все сделки с PnL
- **data/.001_emergency_stop** - флаг emergency stop (если есть)

### Анализ результатов

```bash
# Простой отчет
python scripts/analyze.py

# С графиками
python scripts/analyze.py --plot

# За последние 24 часа
python scripts/analyze.py --plot --period 24h
```

## ⚙️ Параметры конфигурации

### Account (для каждого аккаунта)

| Параметр | Описание | Пример |
|----------|----------|--------|
| `id` | Уникальный ID (1-999) | 1 |
| `name` | Название аккаунта | "My Demo" |
| `api_key_env` | Переменная .env с ключом | "1_BYBIT_API_KEY" |
| `api_secret_env` | Переменная .env с секретом | "1_BYBIT_API_SECRET" |
| `demo_trading` | Demo или Production | true |
| `dry_run` | Симуляция или реальные API | false |

### Risk Management (для каждого аккаунта)

| Параметр | Описание | По умолчанию |
|----------|----------|--------------|
| `mm_rate_threshold` | Emergency close при Account MM Rate >= X% | 90.0 |

### Strategy (для каждой монеты в аккаунте)

| Параметр | Описание | Пример |
|----------|----------|--------|
| `symbol` | Торговая пара (ОБЯЗАТЕЛЬНО!) | DOGEUSDT |
| `category` | Тип контракта | linear |
| `leverage` | Плечо | 75 |
| `initial_position_size_usd` | Начальная маржа (USDT) | 1.0 |
| `grid_step_percent` | Шаг grid в % | 1.0 |
| `averaging_multiplier` | Классический мартингейл | 2.0 |
| `take_profit_percent` | Take profit в % | 1.0 |
| `max_grid_levels_per_side` | Макс усреднений | 10 |

**DEPRECATED параметры** (больше не используются):
- `max_total_exposure` → Теперь используется `totalAvailableBalance` с биржи
- `liquidation_buffer` → Теперь используется Account MM Rate с биржи
- `emergency_stop_loss` → Не реализовано (MM Rate защита лучше)

## 🏢 Multi-Account Architecture

Бот поддерживает несколько **полностью изолированных аккаунтов**:
- Каждый аккаунт = отдельный пользователь/клиент
- Свои API ключи, риск-лимиты, стратегии
- Свои файлы данных и логи с ID префиксом
- WebSocket'ы шарятся для эффективности (один WebSocket на (symbol, environment) для всех аккаунтов)

Подробнее см. [MIGRATION_TO_MULTI_ACCOUNT.md](docs/MIGRATION_TO_MULTI_ACCOUNT.md)

## 🛠️ Разработка

### Установка для разработки

```bash
# Создать venv
~/.local/bin/uv venv

# Активировать
source .venv/bin/activate

# Установить зависимости
~/.local/bin/uv pip install -r requirements.txt

# Запустить тесты
pytest tests/

# С подробным выводом
pytest tests/ -v
```

### Запуск тестов

```bash
# Все 113 тестов
pytest tests/

# Только для конкретного компонента
pytest tests/test_position_manager.py -v
pytest tests/test_grid_strategy.py -v
pytest tests/test_integration.py -v
```

Подробнее см. [TESTING.md](docs/TESTING.md)

### Зависимости

- `pybit` - Официальная библиотека Bybit
- `python-dotenv` - Управление переменными окружения
- `pyyaml` - Парсинг конфигурации
- `pandas` - Анализ данных
- `matplotlib` - Графики
- `pytest` - Тестирование
- `pytz` - Timezone support

## 🏗️ Архитектура

### Ключевые особенности реализации

1. **Fail-Fast принцип** - бот останавливается при любой проблеме с данными, вместо использования fallback значений
2. **Реальная ликвидация** - использует Account Maintenance Margin Rate (accountMMRate) от Bybit API для hedged позиций
3. **Классический мартингейл** - каждая новая позиция = предыдущая × 2 (1→2→4→8→16...)
4. **Symbol префикс** - все логи помечены `[SYMBOL]` для multi-symbol торговли
5. **State persistence** - позиции сохраняются в JSON после каждого изменения
6. **Sync с биржей** - каждые 60 сек проверяет реальное состояние на бирже

### Три слоя

1. **Exchange Layer** (`src/exchange/`)
   - Взаимодействие с Bybit API
   - WebSocket для real-time цен

2. **Strategy Layer** (`src/strategy/`)
   - Grid логика
   - Управление позициями
   - Risk management

3. **Analytics Layer** (`src/analytics/`)
   - Метрики и отчеты
   - CSV логирование

## 📚 Полезные ссылки

- [Bybit Demo Trading](https://testnet.bybit.com)
- [Bybit API Документация](https://bybit-exchange.github.io/docs/)
- [pybit GitHub](https://github.com/bybit-exchange/pybit)
- [Мультивалютная торговля](docs/MULTI_SYMBOL.md)
- [Запуск в фоне](docs/BACKGROUND_SERVICE_GUIDE.md)
- [Тестирование](docs/TESTING.md)
- [Аналитика](docs/ANALYTICS_GUIDE.md)

## ⚖️ Лицензия

MIT

## 🤝 Поддержка

Если у вас возникли проблемы:
1. Проверьте логи в `logs/` или через `sudo journalctl -u sol-trader -f`
2. Убедитесь, что API ключи верны
3. Проверьте, что используется demo режим
4. Проверьте что `symbol` указан в конфиге (нет дефолтного значения!)
5. Создайте issue с описанием проблемы

## 🚀 Быстрые команды

```bash
# Запуск в терминале (для теста)
python src/main.py

# Запуск как сервис
sudo systemctl start sol-trader
sudo journalctl -u sol-trader -f

# Остановка
sudo systemctl stop sol-trader

# Перезапуск (после изменения конфига)
sudo systemctl restart sol-trader

# Анализ результатов
python scripts/analyze.py --plot

# Проверка баланса
python scripts/check_balance.py

# Запуск тестов
pytest tests/ -v
```

---

**⚠️ ДИСКЛЕЙМЕР:** Этот бот создан исключительно в образовательных целях. Автор не несет ответственности за любые финансовые потери. Торговля криптовалютой сопряжена с высокими рисками. Используйте на свой страх и риск!
