# ⚙️ Конфигурация

Подробное руководство по настройке SOL-Trader.

---

## 📄 Структура конфигурации

```
config/
├── config.yaml       # Основная конфигурация
├── constants.py      # Константы (лимиты, fees, intervals)
├── .env             # API credentials (НЕ коммитить!)
└── .env.example     # Шаблон для .env
```

---

## 🔑 API Credentials (.env)

### Формат файла

```bash
# Account 1 (Demo)
1_BYBIT_API_KEY=your_demo_api_key_here
1_BYBIT_API_SECRET=your_demo_api_secret_here

# Account 2 (Production) - опционально
2_BYBIT_API_KEY=your_prod_api_key_here
2_BYBIT_API_SECRET=your_prod_api_secret_here
```

### Правила

- **ID формат:** Префикс `{ID}_` должен совпадать с `id` в `config.yaml`
- **Безопасность:** Файл `.env` добавлен в `.gitignore` - никогда не коммитьте его!
- **Хранение:** Храните backup credentials в безопасном месте (password manager)

### Получение API ключей

**Demo (testnet.bybit.com):**
1. Зарегистрируйтесь на https://testnet.bybit.com
2. API Management → Create New Key
3. Permissions: Включите **Perpetual Trading** (Contract Trading)
4. IP Whitelist: Оставьте пустым для тестирования или укажите свой IP

**Production (bybit.com):**
1. Войдите на https://bybit.com
2. API Management → Create New Key
3. Permissions: **Только Trading** (минимальные права!)
4. IP Whitelist: **Обязательно** укажите IP сервера
5. **Важно:** Сохраните Secret - показывается только один раз!

---

## 📋 config.yaml - Основная конфигурация

### Структура файла

```yaml
accounts:  # Список аккаунтов (можно несколько)
  - id: 1  # Уникальный ID (1-999)
    name: "Account Name"
    api_key_env: "1_BYBIT_API_KEY"
    api_secret_env: "1_BYBIT_API_SECRET"
    demo_trading: true
    dry_run: false

    risk_management:
      mm_rate_threshold: 90.0

    strategies:  # Список стратегий (минимум 1!)
      - symbol: "DOGEUSDT"
        # ... параметры стратегии
```

---

## 🏦 Параметры аккаунта

### id (ОБЯЗАТЕЛЬНО)

```yaml
id: 1  # Уникальный ID (1-999)
```

- **Формат:** Целое число от 1 до 999
- **Использование:**
  - Префикс для всех файлов: `001_bot_state.json`, `001_trades_history.csv`
  - Префикс в логах: `[001]`
  - Ссылка на credentials в `.env`: `{ID}_BYBIT_API_KEY`
- **Уникальность:** Каждый аккаунт должен иметь уникальный ID

### name (ОБЯЗАТЕЛЬНО)

```yaml
name: "Main Demo Account"
```

- **Назначение:** Человеко-читаемое название аккаунта
- **Использование:** В логах и отчетах
- **Рекомендация:** Используйте описательные названия ("Demo DOGE", "Prod SOL Hedge")

### api_key_env, api_secret_env (ОБЯЗАТЕЛЬНО)

```yaml
api_key_env: "1_BYBIT_API_KEY"
api_secret_env: "1_BYBIT_API_SECRET"
```

- **Назначение:** Ссылка на переменные в `.env` файле
- **Формат:** Должен соответствовать именам переменных в `.env`
- **Безопасность:** Ключи НЕ хранятся в `config.yaml`, только ссылки!

### demo_trading (ОБЯЗАТЕЛЬНО)

```yaml
demo_trading: true   # true = testnet, false = production
```

- **true:** Использует Bybit Testnet (testnet.bybit.com)
- **false:** Использует Bybit Production (bybit.com)
- **Рекомендация:** Всегда начинайте с `true` (demo режим)

### dry_run (ОБЯЗАТЕЛЬНО)

```yaml
dry_run: false   # false = реальные API вызовы, true = симуляция
```

- **true:** Симуляция (логи без реальных API вызовов)
- **false:** Реальные API вызовы (demo или prod в зависимости от `demo_trading`)
- **Использование:** `dry_run: true` полезен для тестирования логики без подключения к exchange

---

## 🛡️ risk_management - Управление рисками

### mm_rate_threshold (ОБЯЗАТЕЛЬНО)

```yaml
risk_management:
  mm_rate_threshold: 90.0  # Emergency close при MM Rate >= 90%
```

- **Диапазон:** 0-100 (%)
- **По умолчанию:** 90.0
- **Назначение:** Порог Account Maintenance Margin Rate для emergency close
- **Действие:** При `MM Rate >= threshold` → немедленное закрытие ВСЕХ позиций + emergency stop

**Рекомендации:**
- **Агрессивный:** 90-95% (максимальное использование leverage)
- **Сбалансированный:** 70-85% (рекомендуется)
- **Консервативный:** 50-65% (для начинающих)

**Важно:** Более низкий threshold = раньше закрываются позиции = меньше риск ликвидации, но больше ложных срабатываний.

---

## 📈 strategies - Торговые стратегии

### Минимальная конфигурация

```yaml
strategies:  # ОБЯЗАТЕЛЬНО минимум одна стратегия!
  - symbol: "DOGEUSDT"  # ОБЯЗАТЕЛЬНО
    category: "linear"
    leverage: 75
    initial_position_size_usd: 1.0
    grid_step_percent: 1.0
    averaging_multiplier: 2.0
    take_profit_percent: 1.0
    max_grid_levels_per_side: 10
```

### symbol (ОБЯЗАТЕЛЬНО)

```yaml
symbol: "DOGEUSDT"
```

- **Формат:** Торговая пара на Bybit
- **Примеры:** `DOGEUSDT`, `SOLUSDT`, `BTCUSDT`, `ETHUSDT`
- **Проверка:** Убедитесь что символ существует на Bybit:
  - Demo: https://testnet.bybit.com/trade/usdt/
  - Prod: https://www.bybit.com/trade/usdt/

### category

```yaml
category: "linear"
```

- **Значение:** `"linear"` (USDT perpetual futures)
- **Не изменяйте:** Бот поддерживает только linear контракты

### leverage (ОБЯЗАТЕЛЬНО)

```yaml
leverage: 75  # Плечо (ВЫСОКИЙ РИСК!)
```

- **Диапазон:** 1-200 (зависит от символа и Bybit лимитов)
- **Агрессивный:** 75-100x (высокий риск, высокая прибыльность)
- **Сбалансированный:** 25-50x (средний риск)
- **Консервативный:** 10-20x (для начинающих)

**Формула:** `position_size = margin × leverage`

**Пример:**
```
initial_position_size_usd: 1.0
leverage: 75
→ Начальная позиция: $1 × 75 = $75
```

**⚠️ РИСК:** Высокое плечо = высокий риск ликвидации!

### initial_position_size_usd (ОБЯЗАТЕЛЬНО)

```yaml
initial_position_size_usd: 1.0  # Начальная маржа в USD
```

- **Диапазон:** $0.1 - $100,000
- **Назначение:** Маржа (не размер позиции!) для начального уровня (level 0)
- **Формула:** Размер позиции = `initial_position_size_usd × leverage`

**Рекомендации:**
- **Demo:** $1-5 (для тестирования)
- **Prod (начинающие):** $0.5-2 (минимальный риск)
- **Prod (опытные):** $5-20 (зависит от баланса)

**Расчет от баланса:**
```
Баланс: $1000
Агрессивно: 0.5% = $5
Консервативно: 0.1% = $1
```

### grid_step_percent (ОБЯЗАТЕЛЬНО)

```yaml
grid_step_percent: 1.0  # Шаг сетки (%)
```

- **Диапазон:** 0.01% - 100%
- **Назначение:** Процентное изменение цены для триггера усреднения
- **Частота усреднений:**
  - 0.5%: Очень частые усреднения
  - 1.0%: Стандарт (рекомендуется)
  - 2.5%: Редкие усреднения (консервативно)

**Примеры:**
```
Цена: $0.100
grid_step_percent: 1.0

LONG усреднение триггеры:
  Level 0: $0.100 (начальная)
  Level 1: $0.099 (-1.0%)
  Level 2: $0.098 (-1.0% от $0.099)

SHORT усреднение триггеры:
  Level 0: $0.100 (начальная)
  Level 1: $0.101 (+1.0%)
  Level 2: $0.102 (+1.0% от $0.101)
```

### averaging_multiplier (ОБЯЗАТЕЛЬНО)

```yaml
averaging_multiplier: 2.0  # Мартингейл множитель
```

- **Диапазон:** >1.0 - 10.0
- **Назначение:** Множитель для размера каждой новой позиции
- **Формула:** `new_margin = last_position_margin × multiplier`

**Прогрессии:**

**Multiplier 2.0 (агрессивный):**
```
Level 0: $1
Level 1: $2   (= $1 × 2)
Level 2: $4   (= $2 × 2)
Level 3: $8
Level 10: $1,024
Cumulative: $2,047
```

**Multiplier 1.5 (средний):**
```
Level 0: $1
Level 1: $1.5
Level 2: $2.25
Level 3: $3.38
Level 10: $57.67
Cumulative: $113.33
```

**Multiplier 1.2 (консервативный):**
```
Level 0: $1
Level 1: $1.2
Level 2: $1.44
Level 3: $1.73
Level 10: $6.19
Cumulative: $25.96
```

**Вывод:** Меньший multiplier = меньше капитала требуется, но медленнее усреднение.

### take_profit_percent (ОБЯЗАТЕЛЬНО)

```yaml
take_profit_percent: 1.0  # Take Profit (%)
```

- **Диапазон:** 0.01% - 100%
- **Назначение:** Процент прибыли для закрытия позиций
- **Расчет:** TP учитывает комиссии автоматически

**Формула:**
```
TP_LONG  = avg_entry_price × (1 + TP% + fees%)
TP_SHORT = avg_entry_price × (1 - TP% - fees%)

fees% = (num_entries × 0.055% + 0.020%) / 100
```

**Рекомендации:**
- **Агрессивный:** 0.5-1.0% (частые закрытия)
- **Сбалансированный:** 1.0-1.5% (рекомендуется)
- **Консервативный:** 2.0-3.0% (редкие закрытия, больше прибыль)

### max_grid_levels_per_side (ОБЯЗАТЕЛЬНО)

```yaml
max_grid_levels_per_side: 10  # Максимум усреднений
```

- **Диапазон:** 1 - 50
- **Назначение:** Максимальное количество усреднений на одну сторону (LONG или SHORT)
- **Защита:** Бот НЕ будет усредняться глубже этого уровня

**Капитал requirements (multiplier 2.0):**
```
Levels:  1 → Cumulative: $1
Levels:  5 → Cumulative: $31
Levels: 10 → Cumulative: $2,047
Levels: 15 → Cumulative: $65,535
```

**Рекомендации:**
- **Начинающие:** 5-8 levels
- **Стандарт:** 10 levels (default)
- **Опытные:** 12-15 levels (только с большим балансом!)

---

## 📊 Примеры конфигураций

### Консервативная (для начинающих)

```yaml
accounts:
  - id: 1
    name: "Conservative Demo"
    api_key_env: "1_BYBIT_API_KEY"
    api_secret_env: "1_BYBIT_API_SECRET"
    demo_trading: true
    dry_run: false

    risk_management:
      mm_rate_threshold: 60.0  # Ранний emergency close

    strategies:
      - symbol: "DOGEUSDT"
        category: "linear"
        leverage: 15                    # Низкое плечо
        initial_position_size_usd: 1.0
        grid_step_percent: 2.5         # Редкие усреднения
        averaging_multiplier: 1.4      # Медленная прогрессия
        take_profit_percent: 1.5       # Больший TP
        max_grid_levels_per_side: 6    # Меньше уровней
```

**Эффект:** Риск снижен в ~50×, прибыльность на ~20-30% ниже.

### Агрессивная (для опытных)

```yaml
accounts:
  - id: 1
    name: "Aggressive Prod"
    api_key_env: "1_BYBIT_API_KEY"
    api_secret_env: "1_BYBIT_API_SECRET"
    demo_trading: false  # PRODUCTION!
    dry_run: false

    risk_management:
      mm_rate_threshold: 90.0  # Максимальное использование leverage

    strategies:
      - symbol: "DOGEUSDT"
        category: "linear"
        leverage: 100                   # Максимальное плечо
        initial_position_size_usd: 5.0  # Большая начальная маржа
        grid_step_percent: 1.0         # Частые усреднения
        averaging_multiplier: 2.0      # Классический мартингейл
        take_profit_percent: 1.0       # Частые TP
        max_grid_levels_per_side: 10
```

**⚠️ РИСК:** Подходит только для опытных трейдеров с пониманием всех рисков!

### Multi-Symbol (несколько монет)

```yaml
accounts:
  - id: 1
    name: "Multi-Symbol Demo"
    api_key_env: "1_BYBIT_API_KEY"
    api_secret_env: "1_BYBIT_API_SECRET"
    demo_trading: true
    dry_run: false

    risk_management:
      mm_rate_threshold: 80.0  # Консервативнее для multi-symbol

    strategies:
      - symbol: "DOGEUSDT"
        leverage: 50
        initial_position_size_usd: 1.0
        grid_step_percent: 1.0
        averaging_multiplier: 2.0
        take_profit_percent: 1.0
        max_grid_levels_per_side: 8

      - symbol: "SOLUSDT"
        leverage: 30
        initial_position_size_usd: 2.0
        grid_step_percent: 1.5
        averaging_multiplier: 1.5
        take_profit_percent: 1.5
        max_grid_levels_per_side: 8
```

См. подробнее: [07-multi-symbol.md](07-multi-symbol.md)

---

## 🔍 Валидация конфигурации

Бот автоматически валидирует все параметры при старте:

```python
# Проверки leverage
assert 1 <= leverage <= 200

# Проверки position size
assert 0.1 <= initial_position_size_usd <= 100_000

# Проверки процентов
assert 0.01 <= grid_step_percent <= 100
assert 0.01 <= take_profit_percent <= 100

# Проверки multiplier
assert 1.0 < averaging_multiplier <= 10.0

# Проверки grid levels
assert 1 <= max_grid_levels_per_side <= 50

# Проверки MM Rate
assert 0 <= mm_rate_threshold <= 100
```

**Ошибка валидации:** Бот остановится с детальным сообщением.

---

## 📝 constants.py - Константы системы

Файл `config/constants.py` содержит системные константы:

```python
class TradingConstants:
    BALANCE_CACHE_TTL_SEC = 5.0          # Cache TTL для balance
    SYNC_INTERVAL_SEC = 60.0             # Периодическая синхронизация
    ORDER_HISTORY_LIMIT = 50             # Лимит истории ордеров
    POSITION_IDX_LONG = 1                # Bybit position index для LONG
    POSITION_IDX_SHORT = 2               # Bybit position index для SHORT

class ValidationLimits:
    MIN_LEVERAGE = 1
    MAX_LEVERAGE = 200
    MIN_POSITION_SIZE_USD = 0.1
    MAX_POSITION_SIZE_USD = 100_000
    MIN_PERCENT = 0.01
    MAX_PERCENT = 100.0
    MIN_MULTIPLIER = 1.0
    MAX_MULTIPLIER = 10.0
    MIN_GRID_LEVELS = 1
    MAX_GRID_LEVELS = 50
```

**Не изменяйте** без понимания последствий!

---

## ✅ Чек-лист конфигурации

Перед запуском проверьте:

- [ ] `.env` файл создан и содержит корректные API ключи
- [ ] ID в `.env` совпадает с ID в `config.yaml`
- [ ] `demo_trading: true` для первого запуска
- [ ] Минимум одна стратегия сконфигурирована (`strategies:` не пустой)
- [ ] `symbol` существует на Bybit (проверили в UI)
- [ ] `leverage` соответствует вашему risk appetite
- [ ] `initial_position_size_usd` адекватен балансу (0.1-1% от баланса)
- [ ] `mm_rate_threshold` установлен (рекомендуется 60-80% для начинающих)
- [ ] Прочитали [02-strategy.md](02-strategy.md) (понимание рисков)

**Готово!** См. [01-getting-started.md](01-getting-started.md) для запуска.
