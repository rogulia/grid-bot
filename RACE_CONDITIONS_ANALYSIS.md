# Race Conditions Analysis

## Статус: ⚠️ ЧАСТИЧНО РЕШЕНО

Основная race condition решена, но обнаружены **3 новые потенциальные проблемы**.

---

## ✅ Решённая проблема

**Было:**
- WebSocket запускался до восстановления состояния
- Обновления приходили в пустое состояние
- Флаг `_is_syncing` был костылём

**Стало:**
- Состояние восстанавливается ДО запуска WebSocket
- WebSocket обновляет уже известное состояние
- Чистая архитектура

---

## ❌ Новые проблемы

### 1. КРИТИЧЕСКАЯ: Private WebSocket запускается слишком поздно

**Код:**
```python
for symbol in symbols:
    restore_state_from_exchange()  # Может открыть позицию
    start Position WebSocket
# ПОТОМ:
start Private WebSocket  # ❌ СЛИШКОМ ПОЗДНО!
```

**Проблема:**
- `restore_state_from_exchange()` открывает позицию через REST API (limit order)
- Позиция может исполниться пока Private WS не запущен
- Execution event будет **пропущен**
- Отслеживание fill events нарушено

**Сценарий:**
```
T0: restore открывает LONG @ $100 (limit order)
T1: Limit order исполняется на бирже
T2: Bybit sends execution event
T3: [Private WS не запущен] → event lost ❌
T4: Private WS запускается (через 5-10 сек)
T5: Execution event потерян навсегда
```

**Impact:** 
- Средний - REST API подтвердит ордер синхронно
- Но execution stream критичен для fee tracking, fill prices
- Может привести к расхождениям в realized PnL

**Fix:** Запустить Private WS **ДО** restore или **между** получением позиций и открытием новых.

---

### 2. Окно между restore и Position WebSocket

**Код:**
```python
restore_state_from_exchange()  # Создает TP @ $105
[ВРЕМЕННОЕ ОКНО 1-2 сек]
start Position WebSocket       # Подписка на updates
```

**Проблема:**
- restore создает TP ордер
- Цена может резко двинуться
- TP исполняется
- Position WS еще не получает события

**Сценарий:**
```
T0: restore создает TP @ $105
T1: [ОКНО]
T2: Цена скачет до $105 → TP fills
T3: [Position WS не запущен] → event lost
T4: Position WS запускается
T5: Локальное состояние не обновлено
```

**Impact:**
- Низкий - очень маловероятно что TP исполнится за 1-2 сек
- Но периодический sync_with_exchange() поймает расхождение

**Fix:** Запустить Position WS **перед** созданием TP ордеров.

---

### 3. Multi-symbol sequential restore (задержка)

**Код:**
```python
for symbol in [SOLUSDT, DOGEUSDT, ETHUSDT]:
    restore_state_from_exchange()  # 5 сек на символ
    start Position WebSocket
```

**Проблема:**
- DOGEUSDT начнет торговать через 10 сек после SOLUSDT
- ETHUSDT через 15 сек
- Задержка накапливается

**Impact:**
- Низкий - задержка приемлема
- Но можно оптимизировать

**Fix:** Параллельный restore для всех символов (async/await).

---

## 🔧 Рекомендуемые фиксы

### Fix 1: Изменить порядок запуска WebSocket'ов

**Вариант A (консервативный):** Запустить Private WS первым
```python
# ДО любого restore:
start Private WebSocket  

for symbol in symbols:
    restore_state_from_exchange()
    start Position WebSocket
```

**Плюсы:** Execution stream работает с самого начала  
**Минусы:** Private WS может получить execution events до восстановления состояния

**Вариант B (оптимальный):** Разделить restore на две фазы
```python
# Фаза 1: Узнать что есть (без изменений)
for symbol in symbols:
    check_positions_from_exchange()  # Только чтение
    restore_existing_positions()     # Только локальное состояние

# Фаза 2: Запустить WebSocket'ы
start Private WebSocket
for symbol in symbols:
    start Position WebSocket

# Фаза 3: Открыть недостающие позиции (если нужно)
for symbol in symbols:
    if need_initial_position:
        open_initial_position()  # Теперь Private WS уже работает
        create_tp_order()
```

**Плюсы:** 
- Execution events не пропускаются
- Чистая изоляция фаз
- WebSocket'ы работают до любых изменений

**Минусы:** 
- Более сложная логика
- Нужно рефакторить restore_state_from_exchange()

---

### Fix 2: Добавить проверку после запуска WebSocket

```python
for symbol in symbols:
    restore_state_from_exchange()
    start Position WebSocket
    
    # ⭐ НОВОЕ: Пере-проверить что ничего не изменилось
    verify_state_after_ws_start()  # Quick REST check
```

Это "defensive programming" - проверяем что за время запуска WS ничего не произошло.

---

### Fix 3: Параллельный restore (опционально)

```python
import asyncio

async def restore_all_symbols():
    tasks = [
        restore_symbol_async(symbol)
        for symbol in symbols
    ]
    await asyncio.gather(*tasks)
```

Сократит общее время запуска с `N * 5 сек` до `5 сек`.

---

## Рекомендация

**Критичность:** ⚠️ СРЕДНЯЯ

**Минимальный фикс (сделать сейчас):**
1. Переместить запуск Private WebSocket **перед** циклом restore
2. Добавить комментарий о потенциальном окне

**Оптимальный фикс (сделать позже):**
1. Разделить restore на read-only и write фазы
2. Запускать WebSocket'ы между фазами

**Текущий статус:**
- Проблемы не критичные (маловероятные сценарии)
- Периодический sync_with_exchange() поймает большинство расхождений
- Но лучше зафиксить для production readiness

