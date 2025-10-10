# 🎯 Мультивалютная торговля (Multi-Symbol Trading)

## Обзор

Бот теперь поддерживает **одновременную торговлю несколькими монетами**! Каждая монета работает независимо со своими параметрами.

---

## ⚡ Быстрый старт

### 1. Откройте конфигурацию:
```bash
nano config/config.yaml
```

### 2. Добавьте монеты в список `strategies`:

```yaml
strategies:
  # Стратегия #1: SOLUSDT
  - symbol: "SOLUSDT"
    category: "linear"
    leverage: 100
    initial_position_size_usd: 1.0
    grid_step_percent: 1.0
    averaging_multiplier: 2.0
    take_profit_percent: 1.0
    max_grid_levels_per_side: 10

  # Стратегия #2: DOGEUSDT
  - symbol: "DOGEUSDT"
    category: "linear"
    leverage: 75                   # Другое плечо!
    initial_position_size_usd: 1.0
    grid_step_percent: 0.5         # Другие параметры!
    averaging_multiplier: 1.5
    take_profit_percent: 0.5
    max_grid_levels_per_side: 15

  # Стратегия #3: XRPUSDT (можно добавить сколько угодно)
  - symbol: "XRPUSDT"
    category: "linear"
    leverage: 50
    initial_position_size_usd: 2.0
    grid_step_percent: 1.0
    averaging_multiplier: 2.0
    take_profit_percent: 1.0
    max_grid_levels_per_side: 10
```

### 3. Сохраните и запустите:
```bash
sudo systemctl restart sol-trader
sudo journalctl -u sol-trader -f
```

**ВСЁ!** Бот будет торговать все символы одновременно! 🎉

---

## 🏗️ Архитектура

### Как это работает:

```
┌─────────────────────────────────────────────────────────┐
│                    TradingBot                           │
│                                                         │
│  Общие компоненты (1 на весь бот):                     │
│    • BybitClient (API)                                  │
│    • MetricsTracker (аналитика)                         │
│                                                         │
│  Для каждого символа отдельно:                         │
│    ┌─────────────┬─────────────┬─────────────┐         │
│    │  SOLUSDT    │  DOGEUSDT   │  XRPUSDT    │         │
│    ├─────────────┼─────────────┼─────────────┤         │
│    │ GridStrategy│ GridStrategy│ GridStrategy│         │
│    │ PositionMgr │ PositionMgr │ PositionMgr │         │
│    │ WebSocket   │ WebSocket   │ WebSocket   │         │
│    │ StateManager│ StateManager│ StateManager│         │
│    └─────────────┴─────────────┴─────────────┘         │
└─────────────────────────────────────────────────────────┘
```

**Ключевые особенности:**
- ✅ **Изоляция** - каждая монета не влияет на другую
- ✅ **Гибкость** - разные параметры для каждой монеты
- ✅ **Масштабируемость** - легко добавить новую монету
- ✅ **Надежность** - падение одного символа не крашит бота

---

## 📊 Хранение данных

### data/bot_state.json (мультисимвольный формат):
```json
{
  "SOLUSDT": {
    "timestamp": "2025-10-09T22:30:00",
    "long_positions": [...],
    "short_positions": [...],
    "long_tp_order_id": "abc123",
    "short_tp_order_id": "def456"
  },
  "DOGEUSDT": {
    "timestamp": "2025-10-09T22:30:00",
    "long_positions": [...],
    "short_positions": [...],
    "long_tp_order_id": "ghi789",
    "short_tp_order_id": "jkl012"
  }
}
```

### data/performance_metrics.csv (с колонкой symbol):
```csv
timestamp,symbol,price,long_positions,short_positions,long_qty,short_qty,long_pnl,short_pnl,total_pnl,total_trades,balance
2025-10-09 22:30:00,SOLUSDT,220.50,1,1,0.5,0.5,10.0,-10.0,0.0,2,1000.50
2025-10-09 22:30:00,DOGEUSDT,0.25,2,2,400,400,5.0,-5.0,0.0,4,1000.50
2025-10-09 22:30:00,XRPUSDT,2.50,1,1,40,40,8.0,-8.0,0.0,2,1000.50
```

### data/trades_history.csv (с колонкой symbol):
```csv
timestamp,symbol,side,action,price,quantity,reason,pnl
2025-10-09 22:30:00,SOLUSDT,Buy,OPEN,220.50,0.5,Initial position,
2025-10-09 22:30:00,DOGEUSDT,Buy,OPEN,0.25,400,Initial position,
2025-10-09 22:31:00,SOLUSDT,Sell,CLOSE,222.00,0.5,Take Profit,15.0
```

---

## 🎯 Рекомендуемые комбинации монет

### Диверсификация по волатильности:

**Вариант 1: Агрессивный (высокая волатильность)**
```yaml
strategies:
  - symbol: "DOGEUSDT"   # Очень высокая волатильность
    leverage: 50
    grid_step_percent: 0.5

  - symbol: "SHIBUSDT"   # Высокая волатильность
    leverage: 50
    grid_step_percent: 0.5

  - symbol: "PEPEUSDT"   # Мем-коины движутся часто
    leverage: 50
    grid_step_percent: 0.5
```

**Вариант 2: Сбалансированный**
```yaml
strategies:
  - symbol: "DOGEUSDT"   # Высокая волатильность
    leverage: 75
    grid_step_percent: 0.5

  - symbol: "XRPUSDT"    # Средняя волатильность
    leverage: 50
    grid_step_percent: 1.0

  - symbol: "ADAUSDT"    # Средняя волатильность
    leverage: 50
    grid_step_percent: 1.0
```

**Вариант 3: Консервативный**
```yaml
strategies:
  - symbol: "BTCUSDT"    # Низкая волатильность, но стабильность
    leverage: 25
    grid_step_percent: 2.0
    initial_position_size_usd: 10.0  # Больше капитал

  - symbol: "ETHUSDT"    # Средняя волатильность
    leverage: 50
    grid_step_percent: 1.5
    initial_position_size_usd: 5.0
```

---

## ⚙️ Настройка параметров для каждой монеты

### Факторы для учета:

| Параметр | Высокая волатильность (DOGE) | Низкая волатильность (BTC) |
|----------|------------------------------|----------------------------|
| **leverage** | 50-75x | 25-50x |
| **grid_step_percent** | 0.5-1.0% | 1.5-2.5% |
| **averaging_multiplier** | 1.5-2.0x | 2.0-2.5x |
| **take_profit_percent** | 0.5-1.0% | 1.5-2.5% |
| **max_grid_levels** | 15-20 | 5-10 |
| **initial_position_size_usd** | $1-2 | $5-10 |

### Примеры расчета:

**DOGE @ $0.25:**
- Маржа: $1
- Плечо: 75x
- Позиция: $75
- Количество: 300 DOGE

**BTC @ $100,000:**
- Маржа: $10
- Плечо: 25x
- Позиция: $250
- Количество: 0.0025 BTC

---

## 📈 Мониторинг

### Просмотр логов по символу:
```bash
# Все логи
sudo journalctl -u sol-trader -f

# Только SOLUSDT
sudo journalctl -u sol-trader -f | grep SOLUSDT

# Только DOGEUSDT
sudo journalctl -u sol-trader -f | grep DOGEUSDT
```

### Анализ метрик по символу:
```bash
# Все метрики
cat data/performance_metrics.csv

# Только SOLUSDT
cat data/performance_metrics.csv | grep SOLUSDT

# Только DOGEUSDT
cat data/performance_metrics.csv | grep DOGEUSDT
```

### Просмотр состояния:
```bash
cat data/bot_state.json | python3 -m json.tool
```

---

## 🔧 Управление символами

### Добавить новую монету (без остановки):
1. Остановите бота: `sudo systemctl stop sol-trader`
2. Добавьте новую стратегию в `config/config.yaml`
3. Запустите: `sudo systemctl start sol-trader`

### Удалить монету:
1. Остановите бота: `sudo systemctl stop sol-trader`
2. Удалите/закомментируйте стратегию из `config/config.yaml`
3. Запустите: `sudo systemctl start sol-trader`

**ВАЖНО:** Перед удалением монеты убедитесь, что все позиции закрыты!

### Изменить параметры для существующей монеты:
1. Остановите бота
2. Измените параметры в `config/config.yaml`
3. Запустите бота

---

## 💰 Управление капиталом

### Расчет необходимого баланса:

**Формула:**
```
Минимальный баланс = Σ (initial_position_size_usd × max_grid_levels × averaging_multiplier^max_grid_levels)
```

**Пример для 2 монет:**
```yaml
# SOLUSDT
initial_position_size_usd: 1.0
max_grid_levels: 10
averaging_multiplier: 2.0
# Максимум: 1 × 10 × 2^10 ≈ $10,240

# DOGEUSDT
initial_position_size_usd: 1.0
max_grid_levels: 15
averaging_multiplier: 1.5
# Максимум: 1 × 15 × 1.5^15 ≈ $4,377

# ИТОГО: ~$14,617 (в худшем случае)
```

**Рекомендация:** Держите баланс в 2-3 раза больше расчетного максимума для безопасности.

---

## ⚠️ Важные ограничения

### Bybit API лимиты:
- **Rate limits:** 50 запросов/сек
- **WebSocket:** До 10 подключений одновременно
- **Одновременные ордера:** До 500 активных

### Рекомендации:
- ✅ **1-3 монеты** - оптимально
- ⚠️ **4-5 монет** - требует больше капитала
- ❌ **6+ монет** - может упереться в лимиты API

---

## 🆘 Устранение неполадок

### "No strategies configured!"
**Проблема:** Пустой или неправильный `strategies:` блок в config.yaml

**Решение:**
```yaml
strategies:  # ← Множественное число!
  - symbol: "SOLUSDT"
    # ... параметры
```

### WebSocket ошибки для одного символа
**Проблема:** Один символ не подключается к WebSocket

**Решение:**
1. Проверьте что символ существует на Bybit
2. Проверьте `category: "linear"` (должен быть linear для perpetual)
3. Перезапустите бота

### Позиции не открываются для одной монеты
**Проблема:** Недостаточно баланса или минимум Bybit

**Решение:**
```bash
# Проверьте логи
sudo journalctl -u sol-trader -f | grep "ERROR"

# Увеличьте initial_position_size_usd
initial_position_size_usd: 5.0  # Вместо 1.0
```

### State.json конфликты
**Проблема:** Старый формат state.json (без символов)

**Решение:**
```bash
# Сделайте бэкап
cp data/bot_state.json data/bot_state.json.backup

# Удалите старый state
rm data/bot_state.json

# Перезапустите бота
sudo systemctl restart sol-trader
```

---

## 📝 Примеры конфигураций

### Минимальная (1 монета):
```yaml
strategies:
  - symbol: "DOGEUSDT"
    category: "linear"
    leverage: 50
    initial_position_size_usd: 1.0
    grid_step_percent: 0.5
    averaging_multiplier: 1.5
    take_profit_percent: 0.5
    max_grid_levels_per_side: 15
```

### Стандартная (2 монеты):
```yaml
strategies:
  - symbol: "DOGEUSDT"
    category: "linear"
    leverage: 75
    initial_position_size_usd: 1.0
    grid_step_percent: 0.5
    averaging_multiplier: 1.5
    take_profit_percent: 0.5
    max_grid_levels_per_side: 15

  - symbol: "XRPUSDT"
    category: "linear"
    leverage: 50
    initial_position_size_usd: 1.0
    grid_step_percent: 1.0
    averaging_multiplier: 2.0
    take_profit_percent: 1.0
    max_grid_levels_per_side: 10
```

### Продвинутая (3 монеты с разными стратегиями):
```yaml
strategies:
  # Агрессивная: частые усреднения
  - symbol: "DOGEUSDT"
    leverage: 75
    initial_position_size_usd: 2.0
    grid_step_percent: 0.3
    averaging_multiplier: 1.3
    take_profit_percent: 0.3
    max_grid_levels_per_side: 20

  # Сбалансированная: золотая середина
  - symbol: "XRPUSDT"
    leverage: 50
    initial_position_size_usd: 2.0
    grid_step_percent: 1.0
    averaging_multiplier: 2.0
    take_profit_percent: 1.0
    max_grid_levels_per_side: 10

  # Консервативная: редкие но крупные
  - symbol: "BTCUSDT"
    leverage: 25
    initial_position_size_usd: 10.0
    grid_step_percent: 2.0
    averaging_multiplier: 2.5
    take_profit_percent: 2.0
    max_grid_levels_per_side: 5
```

---

## 🔄 Быстрая смена монеты (для торговли только одной)

Если хотите торговать только одну монету - просто оставьте одну стратегию в конфиге:

### 1. Остановите бота:
```bash
sudo systemctl stop sol-trader
```

### 2. Откройте конфиг:
```bash
nano config/config.yaml
```

### 3. Оставьте только одну монету:
```yaml
strategies:
  - symbol: "DOGEUSDT"              # ← ИЗМЕНИТЕ НА ЛЮБУЮ МОНЕТУ
    category: "linear"
    leverage: 75
    initial_position_size_usd: 1.0  # ← МАРЖА В USD (плечо применяется автоматически!)
    grid_step_percent: 1.0
    averaging_multiplier: 2.0
    take_profit_percent: 1.0
    max_grid_levels_per_side: 10
```

**ВАЖНО:** `initial_position_size_usd` — это **МАРЖА**, не размер позиции!
- С плечом 75x: маржа $1 = позиция $75
- Пример: $1 × 75x = $75 позиция = 300 DOGE @ $0.25

### 4. Сохраните (Ctrl+O, Enter, Ctrl+X) и запустите:
```bash
sudo systemctl start sol-trader
```

**ВСЁ!** Бот сам узнает минимумы и шаги с Bybit API! 🎉

---

### 📊 Популярные монеты для одиночной торговли

**С маржой $1 и плечом 75x:**

| Монета | Symbol | Позиция ($1×75x) | Маржа | Волатильность | Рекомендация |
|--------|--------|------------------|-------|---------------|--------------|
| **DOGE** | DOGEUSDT | **$75** (300 DOGE) | **$1.00** | ⭐⭐⭐⭐⭐ Очень высокая | 🏆 **ЛУЧШИЙ ДЛЯ ГРИДА** |
| **XRP** | XRPUSDT | $75 (30 XRP) | $1.00 | ⭐⭐⭐⭐ Высокая | ✅ Отлично |
| **ADA** | ADAUSDT | $75 (90 ADA) | $1.00 | ⭐⭐⭐⭐ Высокая | ✅ Хорошо |
| **DOT** | DOTUSDT | $75 (180 DOT) | $1.00 | ⭐⭐⭐ Средняя | ⚠️ Средне |
| SOL | SOLUSDT | $75 (0.35 SOL) | $1.00 | ⭐⭐ Низкая | ❌ Мало движений |
| ETH | ETHUSDT | $75 (0.025 ETH) | $1.00 | ⭐⭐ Средняя | ❌ Дорого |
| BTC | BTCUSDT | $75 (0.0008 BTC) | $1.00 | ⭐ Очень низкая | ❌ Очень дорого |

**Примечание:** Минимальные ограничения Bybit могут потребовать большую маржу для некоторых монет.

---

### 🆘 Проблемы при смене монеты

#### "The number of contracts exceeds minimum limit"
**Проблема:** `initial_position_size_usd` слишком мал для этой монеты

**Решение:** Увеличьте до минимума:
```yaml
# Посмотрите в логах "Instrument limits - Min: X"
# Умножьте Min на текущую цену
initial_position_size_usd: 25.0  # Например, для дорогих монет
```

#### Бот округлил $1 → $22
**Это нормально** для монет с высоким минимумом (SOL, ETH, BTC).

**Решение:** Используйте более дешевые монеты (DOGE, XRP, ADA)

#### Нет усреднений / TP
**Проблема:** Монета недостаточно волатильна

**Решение:**
1. Уменьшите `grid_step_percent` (1.0 → 0.5%)
2. Смените на более волатильную монету (DOGE, XRP)

---

## 🚀 Готовые команды

```bash
# Остановить бота
sudo systemctl stop sol-trader

# Изменить конфиг (добавить монеты)
nano config/config.yaml

# Запустить бота
sudo systemctl start sol-trader

# Смотреть логи всех монет
sudo journalctl -u sol-trader -f

# Смотреть логи конкретной монеты
sudo journalctl -u sol-trader -f | grep DOGEUSDT

# Проверить статус
sudo systemctl status sol-trader

# Посмотреть текущие позиции всех монет
cat data/bot_state.json | python3 -m json.tool

# Метрики по всем монетам
tail -n 50 data/performance_metrics.csv

# Трейды по конкретной монете
cat data/trades_history.csv | grep DOGEUSDT
```

---

**Готово! Теперь вы можете торговать несколько монет одновременно! 🎉**
