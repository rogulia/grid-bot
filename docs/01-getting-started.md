# 🚀 Быстрый старт

Руководство по установке и первому запуску SOL-Trader.

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

## 📋 Требования

### Системные требования:
- **OS:** Linux (Ubuntu 20.04+, Debian 11+, CentOS 8+)
- **Python:** 3.9 или выше
- **RAM:** Минимум 512MB, рекомендуется 1GB+
- **Disk:** 1GB свободного места
- **Network:** Стабильное подключение к интернету

### Аккаунт Bybit:
- Демо аккаунт: https://testnet.bybit.com (для тестирования)
- Прод аккаунт: https://bybit.com (для реальной торговли)

---

## 🔧 Установка

### 1. Клонирование репозитория

```bash
git clone <repository-url>
cd sol-trader
```

### 2. Создание виртуального окружения

```bash
# Установка UV (если еще не установлен)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Создание виртуального окружения
~/.local/bin/uv venv

# Активация
source .venv/bin/activate
```

### 3. Установка зависимостей

```bash
~/.local/bin/uv pip install -r requirements.txt
```

---

## 🔑 Настройка API ключей

### 1. Получение API ключей

**Для демо:**
1. Зарегистрируйтесь на https://testnet.bybit.com
2. Перейдите в API Management
3. Создайте новый API ключ
4. **Важно:** Включите разрешения для Perpetual Trading

**Для прод:**
1. Войдите на https://bybit.com
2. API Management → Create New Key
3. Включите только необходимые разрешения (Trading)
4. Сохраните API Key и API Secret в безопасном месте

### 2. Создание .env файла

```bash
# Скопируйте шаблон
cp config/.env.example .env

# Отредактируйте файл
nano .env
```

### 3. Формат .env файла

```bash
# Account 1 (Demo)
1_BYBIT_API_KEY=your_demo_api_key_here
1_BYBIT_API_SECRET=your_demo_api_secret_here

# Account 2 (Production) - опционально
# 2_BYBIT_API_KEY=your_prod_api_key_here
# 2_BYBIT_API_SECRET=your_prod_api_secret_here
```

**Важно:**
- ID в `.env` должен совпадать с `id` в `config.yaml`
- Никогда не коммитьте `.env` в git!
- Храните credentials в безопасном месте

---

## ⚙️ Настройка конфигурации

### 1. Откройте config.yaml

```bash
nano config/config.yaml
```

### 2. Минимальная конфигурация для начала

```yaml
accounts:
  - id: 1                                    # Уникальный ID (1-999)
    name: "Demo Account"                     # Название аккаунта
    api_key_env: "1_BYBIT_API_KEY"          # Ссылка на .env
    api_secret_env: "1_BYBIT_API_SECRET"    # Ссылка на .env
    demo_trading: true                       # true = demo, false = prod
    dry_run: false                           # false = реальные API вызовы

    risk_management:
      mm_rate_threshold: 90.0                # Emergency close при MM Rate >= 90%

    strategies:                              # ОБЯЗАТЕЛЬНО хотя бы одна стратегия!
      - symbol: "DOGEUSDT"                   # Торговая пара
        category: "linear"                   # Тип контракта
        leverage: 75                         # Плечо (ВЫСОКИЙ РИСК!)
        initial_position_size_usd: 1.0       # Начальная маржа в USD
        grid_step_percent: 1.0               # Шаг сетки (%)
        averaging_multiplier: 2.0            # Мартингейл множитель
        take_profit_percent: 1.0             # Take Profit (%)
        max_grid_levels_per_side: 10         # Максимум усреднений
```

**Параметры объяснены в [03-configuration.md](03-configuration.md)**

---

## 🚀 Первый запуск

### Режим 1: Терминал (для тестирования)

```bash
# Активируйте виртуальное окружение (если еще не активировано)
source .venv/bin/activate

# Запустите бота
python src/main.py
```

**Что должно произойти:**
```
[INFO] Starting SOL-Trader Multi-Account Bot
[INFO] [001][DOGEUSDT] Initializing strategy...
[INFO] [001][DOGEUSDT] Setting leverage to 75x
[INFO] [001][DOGEUSDT] Waiting for first price from WebSocket...
💵 First price: $0.20587
[INFO] [001][DOGEUSDT] Opening initial LONG position
[INFO] [001][DOGEUSDT] Opening initial SHORT position
✅ [001][DOGEUSDT] Initial sync completed
```

**Остановка:**
- Нажмите `Ctrl+C` для graceful shutdown
- Бот закроет все WebSocket соединения
- Состояние сохранится в `data/001_bot_state.json`

### Режим 2: Фоновый сервис (для production)

См. подробную инструкцию в [05-operations.md](05-operations.md)

```bash
# Установка сервиса (один раз)
sudo bash scripts/setup_service.sh

# Запуск
sudo systemctl start sol-trader

# Проверка статуса
sudo systemctl status sol-trader

# Просмотр логов
sudo journalctl -u sol-trader -f
```

---

## 📊 Проверка работы

### 1. Логи

**Структура логов (для Account 001):**
```
logs/
├── 001_bot_YYYY-MM-DD.log       # Основные события
├── 001_trades_YYYY-MM-DD.log    # Все сделки
├── 001_positions_YYYY-MM-DD.log # Снимки позиций
└── main_YYYY-MM-DD.log          # Системные события
```

### 2. Данные

**Структура данных (для Account 001):**
```
data/
├── 001_bot_state.json           # Текущее состояние позиций
├── 001_performance_metrics.csv  # Метрики каждые 60s
├── 001_trades_history.csv       # История сделок
└── .001_emergency_stop          # Emergency flag (если есть)
```

### 3. Ключевые метрики в логах

```
Balance: $998.40              - Текущий баланс
Account MM Rate: 0.03%        - Риск ликвидации (ключевая метрика!)
Available: $950.23 (95.2%)    - Доступная маржа
Used IM: $48.17               - Занятая маржа
LONG PnL: $-5.23              - Unrealized PnL LONG
SHORT PnL: $+8.45             - Unrealized PnL SHORT
```

### 4. Проверка баланса

```bash
python scripts/check_balance.py
```

---

## ✅ Чек-лист первого запуска

- [ ] Python 3.9+ установлен
- [ ] Виртуальное окружение создано и активировано
- [ ] Зависимости установлены (`pip list` показывает pybit, pyyaml, etc.)
- [ ] `.env` файл создан с корректными API ключами
- [ ] `config.yaml` настроен (demo_trading: true, хотя бы одна стратегия)
- [ ] ID в `.env` совпадает с ID в `config.yaml`
- [ ] Бот запускается без ошибок (`python src/main.py`)
- [ ] В логах видно "Initial sync completed"
- [ ] Позиции видны в Bybit Testnet UI (для demo)
- [ ] Файлы в `data/` и `logs/` создаются автоматически

---

## 🔧 Устранение проблем

### Проблема: "Module not found"

```bash
# Убедитесь что виртуальное окружение активировано
source .venv/bin/activate

# Переустановите зависимости
~/.local/bin/uv pip install -r requirements.txt
```

### Проблема: "API key invalid"

- Проверьте правильность копирования ключей (без пробелов)
- Убедитесь что используете demo ключи с demo_trading: true
- Проверьте что ID в .env совпадает с config.yaml

### Проблема: "No strategy configured"

```yaml
# В config.yaml ОБЯЗАТЕЛЬНО должна быть секция strategies:
accounts:
  - id: 1
    strategies:  # <-- Это обязательно!
      - symbol: "DOGEUSDT"
        # ... остальные параметры
```

### Проблема: "Insufficient balance"

- Проверьте баланс: `python scripts/check_balance.py`
- Для demo получите тестовые USDT на https://testnet.bybit.com
- Убедитесь что `initial_position_size_usd` не слишком большой

### Проблема: "Emergency stop active"

```bash
# Проверьте наличие файла
ls -la data/.001_emergency_stop

# Прочитайте причину
cat data/.001_emergency_stop

# Удалите после анализа проблемы
rm data/.001_emergency_stop
```

---

## 📚 Следующие шаги

После успешного запуска:

1. **Изучите стратегию:** [02-strategy.md](02-strategy.md)
2. **Настройте параметры:** [03-configuration.md](03-configuration.md)
3. **Разберитесь с рисками:** [04-risk-management.md](04-risk-management.md)
4. **Настройте 24/7 работу:** [05-operations.md](05-operations.md)
5. **Анализируйте результаты:** [06-analytics.md](06-analytics.md)

---

## 💡 Рекомендации для начинающих

### Для первого запуска:

1. ✅ Используйте **demo аккаунт** (testnet.bybit.com)
2. ✅ Используйте **консервативные параметры:**
   ```yaml
   leverage: 15                    # Вместо 75
   grid_step_percent: 2.5         # Вместо 1.0
   averaging_multiplier: 1.4      # Вместо 2.0
   take_profit_percent: 1.5       # Вместо 1.0
   max_grid_levels_per_side: 6    # Вместо 10
   mm_rate_threshold: 60.0        # Вместо 90.0
   ```
3. ✅ Запускайте в **терминале** первые несколько дней
4. ✅ Мониторьте логи **постоянно**
5. ✅ Тестируйте минимум **2-4 недели** на demo

### Перед переходом на production:

1. ⚠️ Убедитесь что понимаете **все риски**
2. ⚠️ Протестировали на demo **минимум месяц**
3. ⚠️ Видели как бот ведет себя в **разных рыночных условиях**
4. ⚠️ Знаете как **остановить бота** в критической ситуации
5. ⚠️ Начинайте с **минимальной суммы** ($50-$100)
6. ⚠️ **НИКОГДА** не вкладывайте деньги, которые не можете потерять

---

**Готово!** Теперь вы можете запустить бота в demo режиме. 🎉
