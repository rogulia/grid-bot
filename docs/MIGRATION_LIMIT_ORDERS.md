# Migration: Market to Limit Orders

## Краткая сводка

Бот успешно мигрирован с маркет-ордеров на лимитные с автоматическим retry механизмом и фоллбэком.

## Что изменилось

### 1. Новые файлы

#### `src/utils/limit_order_manager.py`
Новый менеджер для управления лимитными ордерами:
- Автоматическое размещение с правильным offset
- Tracking через WebSocket
- Таймеры и retry логика
- Fallback на market orders

#### `tests/test_limit_order_manager.py`
Полный набор unit tests (9 тестов, 100% pass rate)

#### `docs/LIMIT_ORDERS.md`
Полная документация по новой системе

### 2. Измененные файлы

#### `config/constants.py`
Добавлены новые константы:
```python
LIMIT_ORDER_PRICE_OFFSET_PERCENT = 0.03
LIMIT_ORDER_TIMEOUT_SEC = 10
LIMIT_ORDER_MAX_RETRIES = 3
```

#### `src/strategy/grid_strategy.py`
**Изменения:**

1. **Импорты** (строка 14):
   ```python
   from ..utils.limit_order_manager import LimitOrderManager
   ```

2. **Инициализация** (строки 82-89):
   ```python
   self.limit_order_manager = LimitOrderManager(...)
   ```

3. **_open_initial_position()** (строки 318-354):
   - Заменен `client.place_order(..., order_type="Market")` на `limit_order_manager.place_limit_order()`
   - Добавлен fallback на market при ошибке

4. **sync_with_exchange()** (строки 945-982):
   - Аналогичная замена для initial position при синхронизации

5. **_execute_grid_order()** (строки 1641-1668):
   - Аналогичная замена для grid averaging orders

6. **on_order_update()** (строки 2058-2060):
   - Добавлена передача обновлений в LimitOrderManager

7. **on_price_update()** (строки 1480-1485):
   - Добавлено обновление текущей цены для tracked orders

### 3. Без изменений

Следующие компоненты **не изменились**:
- `BybitClient` - уже поддерживал limit orders
- TP orders - продолжают работать как limit orders с `reduce_only=True`
- Emergency close - продолжает использовать market orders (правильно!)
- Position tracking - без изменений
- WebSocket subscriptions - без изменений

## Как это работает

### Flow для нового ордера

```
1. Стратегия вызывает limit_order_manager.place_limit_order()
   ↓
2. Manager рассчитывает limit price (market ± 0.03%)
   ↓
3. Размещает ордер через BybitClient
   ↓
4. Запускает 10-секундный таймер
   ↓
5. WebSocket получает статус → передает в manager
   ↓
6a. Filled → success! ✅
6b. Timeout → retry (до 3 раз)
6c. После 3 попыток → market order fallback
```

### Пример: Grid Level Averaging

**До (market order):**
```python
response = self.client.place_order(
    symbol=self.symbol,
    side=side,
    qty=new_size,
    order_type="Market",  # 0.055% комиссия
    category=self.category
)
```

**После (limit order):**
```python
order_id = self.limit_order_manager.place_limit_order(
    side=side,
    qty=new_size,
    current_price=current_price,
    reason=f"Grid level {grid_level}"
)

if not order_id:
    # Fallback на market при ошибке
    response = self.client.place_order(..., order_type="Market")
```

## Тестирование

### Unit Tests
```bash
python3 -m unittest tests.test_limit_order_manager -v
```
**Результат:** ✅ 9/9 passed

### Integration Testing План

1. **Dry Run Mode**
   ```bash
   # В config.yaml установить dry_run: true
   # Запустить бота и проверить логи
   ```

2. **Demo Account**
   ```bash
   # demo_trading: true
   # Проверить реальное исполнение limit orders
   # Мониторить fill rate и fallback rate
   ```

3. **Live Testing** (осторожно!)
   ```bash
   # Начать с маленькой initial_position_size_usd
   # Мониторить первые 24 часа
   # Проверить экономию на комиссиях
   ```

## Метрики успеха

### Целевые показатели

| Метрика | Целевое значение | Как измерить |
|---------|------------------|--------------|
| Limit orders filled | >80% | `grep "Limit order FILLED" logs/*.log` |
| Average fill time | <10 sec | Анализ timestamp между placement и fill |
| Market fallbacks | <20% | `grep "falling back to MARKET" logs/*.log` |
| Fee savings | ~64% | Сравнение с историческими данными |

### Мониторинг в production

```bash
# Проверка за последний час
tail -n 1000 logs/001_DOGEUSDT.log | grep "Limit order"

# Статистика
echo "Placed:"; grep "Limit order placed" logs/*.log | wc -l
echo "Filled:"; grep "Limit order FILLED" logs/*.log | wc -l
echo "Timeouts:"; grep "Limit order timeout" logs/*.log | wc -l
echo "Fallbacks:"; grep "falling back to MARKET" logs/*.log | wc -l
```

## Откат (Rollback)

Если нужно вернуться к market orders:

### Быстрый откат (временный)

В `grid_strategy.py` закомментировать блоки с limit orders и раскомментировать старый код:

```python
# order_id = self.limit_order_manager.place_limit_order(...)
# if not order_id:
response = self.client.place_order(..., order_type="Market")
```

### Полный откат (через git)

```bash
# Посмотреть изменения
git diff HEAD~1

# Откатить файлы
git checkout HEAD~1 -- src/strategy/grid_strategy.py
git checkout HEAD~1 -- config/constants.py

# Удалить новые файлы
rm src/utils/limit_order_manager.py
rm tests/test_limit_order_manager.py
```

## Возможные проблемы

### 1. Limit orders не исполняются

**Симптомы:**
- Много timeout сообщений
- Частые fallback на market
- Fill rate <50%

**Решения:**
- Увеличить `LIMIT_ORDER_PRICE_OFFSET_PERCENT` (0.03 → 0.05)
- Уменьшить `LIMIT_ORDER_TIMEOUT_SEC` (10 → 5)
- Проверить ликвидность символа

### 2. WebSocket disconnects

**Симптомы:**
- Order updates не приходят
- Таймауты даже для filled orders

**Решения:**
- Проверить WebSocket connection в логах
- Увеличить `WEBSOCKET_RECONNECT_DELAY_SEC`
- Добавить REST API fallback для order status

### 3. Слишком медленное исполнение

**Симптомы:**
- Частые fallback на market (>40%)
- Пропущенные grid levels

**Решения:**
- Уменьшить `LIMIT_ORDER_MAX_RETRIES` (3 → 2)
- Уменьшить timeout (10 → 5 сек)
- Использовать market orders для критичных ситуаций

## Риски и митигация

### Высокий риск

❌ **Проблема:** WebSocket отваливается → orders висят без контроля  
✅ **Митигация:** Автоматический timeout + fallback на market

### Средний риск

⚠️ **Проблема:** Быстрый рынок → много fallback → высокие комиссии  
✅ **Митигация:** Адаптивный offset, настройка под ликвидность

### Низкий риск

✅ **Проблема:** Задержка 10-30 сек может быть критична  
✅ **Митигация:** Emergency close всегда использует market

## Чеклист деплоя

- [ ] Unit tests passed (9/9)
- [ ] Dry run tested
- [ ] Demo account tested (24h)
- [ ] Config updated with constants
- [ ] Logs monitoring setup
- [ ] Alert thresholds configured
- [ ] Rollback plan understood
- [ ] Documentation reviewed

## Экономия на комиссиях

### Расчет для примера

**Параметры:**
- Symbol: DOGEUSDT
- Initial size: $1
- Leverage: 75x
- Grid steps: 10 levels
- Средний оборот: $1000/день

**Старая схема (market orders):**
```
Комиссия: $1000 × 0.055% = $0.55/день
Месяц: $0.55 × 30 = $16.50
```

**Новая схема (limit orders, 80% fill rate):**
```
Limit (80%): $800 × 0.020% = $0.16
Market (20%): $200 × 0.055% = $0.11
Total: $0.27/день
Месяц: $0.27 × 30 = $8.10
```

**Экономия: $16.50 - $8.10 = $8.40/месяц (51%)**

Для 3 аккаунтов: **$25.20/месяц экономии**

## Следующие шаги

1. ✅ Реализация завершена
2. ✅ Unit tests написаны
3. ✅ Документация создана
4. ⏳ Тестирование на demo account
5. ⏳ Мониторинг метрик (7 дней)
6. ⏳ Production deployment
7. ⏳ Оптимизация параметров на основе данных

---

**Дата миграции:** Октябрь 2025  
**Статус:** ✅ Ready for testing  
**Тесты:** ✅ 9/9 passed  
**Риск:** 🟢 Low (с fallback механизмом)

