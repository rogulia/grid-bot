# Архитектурные изменения: Восстановление состояния при запуске

**Дата**: 2025-10-15  
**Изменения**: Переработана логика восстановления позиций и ордеров при запуске бота

## Проблема

Предыдущая архитектура:
1. ✅ Запускались WebSocket'ы сразу при `initialize()`
2. ⏳ Ждали первую цену от WebSocket
3. 🔄 Только тогда вызывали `sync_with_exchange()` для восстановления
4. 🐛 **Race condition**: WebSocket мог прислать обновления до восстановления состояния

## Решение

Новая архитектура (логичная последовательность):
1. 📊 **Получаем состояние с биржи** через REST API (source of truth)
2. ✅ **Восстанавливаем локальное состояние** (позиции, TP ордера)
3. 🚀 **Запускаем WebSocket'ы** для real-time обновлений
4. 📈 **WebSocket обновляет** уже известное состояние

## Изменённые файлы

### 1. `src/exchange/bybit_client.py`

**Добавлено:**
- `get_market_price(symbol, category)` - удобный метод для получения текущей цены через REST API
- Использует существующий `get_ticker()` и извлекает `lastPrice`
- Fail-fast: выбрасывает `RuntimeError` если не удалось получить цену

### 2. `src/strategy/grid_strategy.py`

**Разделён `sync_with_exchange()` на две функции:**

#### `restore_state_from_exchange(current_price)` - НОВЫЙ метод
- Вызывается **ОДИН РАЗ** при запуске, **ДО запуска WebSocket'ов**
- Получает цену через параметр (из REST API `get_market_price()`)
- Восстанавливает позиции с биржи
- Открывает начальные позиции если нужно
- Создаёт TP ордера
- **Fail-fast** при критических ошибках (останавливает бота)

#### `sync_with_exchange(current_price)` - УПРОЩЁННЫЙ
- Вызывается **периодически** (каждые 60 сек) во время работы
- Проверяет TP ордера (создаёт если отсутствуют)
- Обрабатывает untracked closes (пропущенные закрытия)
- **НЕ открывает** начальные позиции (это уже сделано!)
- **НЕ делает** full restore (это делается при запуске!)

### 3. `src/core/trading_account.py`

**Изменён порядок в `initialize()`:**

**БЫЛО:**
```python
1. Создать компоненты
2. Запустить Position WebSocket  ❌
3. Запустить Private WebSocket   ❌
4. Ждать первую цену
5. Вызвать sync_with_exchange() при первой цене
```

**СТАЛО:**
```python
1. Создать компоненты (StateManager, PositionManager, GridStrategy)
2. Запустить Private WebSocket (execution stream)           ⭐ ДО restore!
3. Получить цену через REST API (get_market_price)         ⭐ NEW
4. Вызвать restore_state_from_exchange(current_price)       ⭐ NEW
5. Запустить Position WebSocket                             ✅ ПОСЛЕ восстановления
```

**ВАЖНО:** Private WebSocket запускается **ДО** restore, чтобы не пропустить execution events от позиций, открытых во время restore!

**Удалено:**
- Флаг `_initial_sync_done` (больше не нужен!)
- Логика начальной синхронизации в `process_price()` (WebSocket теперь только обновляет состояние)

**Добавлено:**
- Вызов `client.get_market_price(symbol)` для получения текущей цены
- Вызов `strategy.restore_state_from_exchange(current_price)` для восстановления
- Логирование каждого шага восстановления

## Преимущества новой архитектуры

### ✅ 1. Нет race conditions
- К моменту запуска WebSocket состояние уже известно
- Любое обновление от WebSocket - это изменение **известной** позиции

### ✅ 2. Fail-fast при старте
- Если не можем восстановить состояние → останавливаемся **ДО** WebSocket'ов
- Не тратим ресурсы на подключение WebSocket'ов если всё равно упадём

### ✅ 3. Упрощение кода
- WebSocket callbacks проще - они только **обновляют** состояние, не восстанавливают
- Вся логика восстановления в одном месте (`restore_state_from_exchange`)
- Не нужен флаг `_is_syncing` для блокировки WebSocket обновлений при старте

### ✅ 4. Логичная последовательность
```
Узнать что есть (REST) → Восстановить локально → Подписаться на изменения (WebSocket)
```

### ✅ 5. bot_state.json актуализируется до торговли
- При вызове `pm.add_position()` в restore → автоматически сохраняется в `bot_state.json`
- Файл содержит актуальное состояние **до** начала WebSocket обновлений

## Backward Compatibility

Изменения **обратно совместимы**:
- ✅ Старая логика `sync_with_exchange()` сохранена для периодических проверок
- ✅ WebSocket callbacks не изменились
- ✅ Формат `bot_state.json` не изменился
- ✅ Логика восстановления grid-позиций не изменилась (используется тот же `_restore_position_from_exchange()`)

## Тестирование

### Сценарии для проверки:

1. **Холодный старт (нет позиций на бирже)**
   - Должны открыться начальные LONG и SHORT позиции
   - Должны создаться TP ордера
   - WebSocket'ы запустятся после открытия

2. **Перезапуск с существующими позициями**
   - Позиции должны восстановиться из биржи
   - Grid-структура должна реконструироваться
   - TP ордера должны создаться
   - WebSocket'ы запустятся после восстановления

3. **Emergency stop перед запуском**
   - Бот должен остановиться **ДО** запуска WebSocket'ов
   - Ошибка должна быть понятной

4. **Ошибка REST API при получении цены**
   - Бот должен остановиться с RuntimeError
   - Не должны запуститься WebSocket'ы

5. **Нормальная работа после запуска**
   - WebSocket обновляет позиции
   - `sync_with_exchange()` работает каждые 60 сек для проверки TP

## Потенциальные проблемы

### ❓ REST API медленнее WebSocket
**Решение**: Это происходит только **один раз** при запуске. Дополнительные 1-2 секунды задержки допустимы для чистоты архитектуры.

### ❓ Состояние изменилось между REST и WebSocket
**Решение**: Крайне маловероятно (кто торгует в момент перезапуска?). Если произойдёт - WebSocket пришлёт обновление, расхождение будет залогировано в `sync_with_exchange()`.

### ❓ Dry run режим
**Решение**: Для dry run используется placeholder price (100.0). Всё работает корректно.

## Логи при запуске (пример)

```
2025-10-15 10:30:00 - INFO - 🔧 Initializing Account 001: Main Trading
2025-10-15 10:30:01 - INFO - 💰 Account Balance: $5000.00
2025-10-15 10:30:02 - INFO - 🔐 Starting Private WebSocket for execution stream...
2025-10-15 10:30:03 - INFO - ✅ Private WebSocket started successfully
2025-10-15 10:30:03 - INFO - 📊 Setting up SOLUSDT (leverage: 75x)
2025-10-15 10:30:04 - INFO - 🔄 [SOLUSDT] Restoring state from exchange...
2025-10-15 10:30:04 - INFO - 💵 [SOLUSDT] Current market price: $145.23
2025-10-15 10:30:05 - INFO - 📥 [SOLUSDT] Position found on exchange for Buy: exchange=500.0, local=0.0 - RESTORING
2025-10-15 10:30:06 - INFO - 📥 [SOLUSDT] Restored Buy position: 5 grid levels, avg_price=$140.12
2025-10-15 10:30:06 - INFO - 🎯 [SOLUSDT] Created TP order for Buy side
2025-10-15 10:30:07 - INFO - 🆕 [SOLUSDT] No Sell position exists - opening initial position
2025-10-15 10:30:08 - INFO - ✅ [SOLUSDT] State restored successfully from exchange
2025-10-15 10:30:09 - INFO - 🔐 [SOLUSDT] Starting Position WebSocket...
2025-10-15 10:30:10 - INFO - ✅ [SOLUSDT] Position WebSocket started
2025-10-15 10:30:10 - INFO - ✅ Account 001 fully initialized with 1 symbol(s)
2025-10-15 10:30:10 - INFO - 🚀 Bot is running.
```

**Обратите внимание:** Private WebSocket теперь запускается **РАНЬШЕ** restore для каждого символа!

## Заключение

Эти изменения делают архитектуру более логичной, безопасной и простой для понимания. 

**Главный принцип**: "Сначала узнать что есть, потом подписаться на изменения" вместо "Подписаться на изменения и надеяться что они придут до того как мы начнём торговать".

