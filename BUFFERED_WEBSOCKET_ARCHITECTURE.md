# Buffered WebSocket Architecture

## Концепция

Вместо блокировки WebSocket событий флагом `_is_syncing`, накапливаем их в буфере и обрабатываем ПОСЛЕ restore.

## Архитектура

### Фаза 1: Инициализация с буфером (0-5 секунд)

```python
class GridStrategy:
    def __init__(self, ...):
        # Event buffer (накапливает события до готовности)
        self._event_buffer = []
        self._buffering_mode = True  # True = накапливать, False = обрабатывать
        self._buffer_lock = threading.Lock()
```

### Фаза 2: Запуск WebSocket (буферизация активна)

```python
async def initialize(self):
    # 1. Start Private WebSocket FIRST (buffering mode)
    self.private_ws.start()  # События идут в буфер
    
    # 2. Start Position WebSocket (buffering mode)
    self.position_ws.start()  # События идут в буфер
    
    # В это время WebSocket может получить:
    # - execution events от открытия позиций
    # - position updates
    # - order updates
    # Все складываются в self._event_buffer
```

### Фаза 3: Restore через REST (5-10 секунд)

```python
async def initialize(self):
    # 3. Get current price via REST
    current_price = self.client.get_market_price(symbol)
    
    # 4. Restore state from exchange
    self.restore_state_from_exchange(current_price)
    # Теперь мы знаем ПОЛНУЮ картину:
    # - Все позиции из биржи
    # - Все order_id активных ордеров
    # - TP ордера созданы
```

### Фаза 4: Обработка буфера (1-2 секунды)

```python
async def initialize(self):
    # 5. Process buffered events
    self._process_buffered_events()
    
    # 6. Switch to real-time mode
    with self._buffer_lock:
        self._buffering_mode = False
    
    # Теперь новые события обрабатываются сразу
```

## Реализация

### 1. Event Buffer в GridStrategy

```python
class GridStrategy:
    def __init__(self, ...):
        # Event buffering
        self._event_buffer = []
        self._buffering_mode = True
        self._buffer_lock = threading.Lock()
        
        # Tracking processed order_ids (для фильтрации дубликатов)
        self._processed_order_ids = set()

    def on_execution(self, exec_data: dict):
        """Handle execution event"""
        with self._buffer_lock:
            if self._buffering_mode:
                # Накапливаем в буфер
                self._event_buffer.append({
                    'type': 'execution',
                    'data': exec_data,
                    'timestamp': time.time()
                })
                self.logger.debug(
                    f"[{self.symbol}] Buffered execution event "
                    f"(buffer size: {len(self._event_buffer)})"
                )
                return
        
        # Real-time обработка (после restore)
        self._handle_execution(exec_data)
    
    def on_position_update(self, position_data: dict):
        """Handle position update"""
        with self._buffer_lock:
            if self._buffering_mode:
                # Накапливаем в буфер
                self._event_buffer.append({
                    'type': 'position',
                    'data': position_data,
                    'timestamp': time.time()
                })
                return
        
        # Real-time обработка
        self._handle_position_update(position_data)
    
    def on_order_update(self, order_data: dict):
        """Handle order update"""
        with self._buffer_lock:
            if self._buffering_mode:
                # Накапливаем в буфер
                self._event_buffer.append({
                    'type': 'order',
                    'data': order_data,
                    'timestamp': time.time()
                })
                return
        
        # Real-time обработка
        self._handle_order_update(order_data)
```

### 2. Обработка буфера после restore

```python
def _process_buffered_events(self):
    """
    Process events accumulated during restore
    
    Фильтрует дубликаты по order_id и обрабатывает только новые события.
    """
    with self._buffer_lock:
        events_to_process = list(self._event_buffer)
        self._event_buffer = []  # Очистить буфер
    
    if not events_to_process:
        self.logger.info(f"[{self.symbol}] No buffered events to process")
        return
    
    self.logger.info(
        f"[{self.symbol}] Processing {len(events_to_process)} buffered events..."
    )
    
    # Собрать order_ids из текущего состояния (после restore)
    current_order_ids = set()
    for pos in self.pm.long_positions:
        if pos.order_id:
            current_order_ids.add(pos.order_id)
    for pos in self.pm.short_positions:
        if pos.order_id:
            current_order_ids.add(pos.order_id)
    
    # TP order IDs
    if self.pm.long_tp_order_id:
        current_order_ids.add(self.pm.long_tp_order_id)
    if self.pm.short_tp_order_id:
        current_order_ids.add(self.pm.short_tp_order_id)
    
    # Обработать события
    processed_count = 0
    skipped_count = 0
    
    for event in events_to_process:
        event_type = event['type']
        event_data = event['data']
        
        # Извлечь order_id
        order_id = None
        if event_type == 'execution':
            order_id = event_data.get('orderId')
        elif event_type == 'order':
            order_id = event_data.get('orderId')
        elif event_type == 'position':
            # Position updates не имеют order_id, всегда обрабатываем
            pass
        
        # Фильтрация дубликатов
        if order_id and order_id in current_order_ids:
            # Этот ордер уже обработан в restore
            skipped_count += 1
            self.logger.debug(
                f"[{self.symbol}] Skipped buffered {event_type} "
                f"(order_id {order_id} already in state)"
            )
            continue
        
        # Обработать событие
        if event_type == 'execution':
            self._handle_execution(event_data)
        elif event_type == 'position':
            self._handle_position_update(event_data)
        elif event_type == 'order':
            self._handle_order_update(event_data)
        
        processed_count += 1
    
    self.logger.info(
        f"✅ [{self.symbol}] Buffered events processed: "
        f"{processed_count} processed, {skipped_count} skipped (duplicates)"
    )
```

### 3. Обновление initialize() в TradingAccount

```python
async def initialize(self):
    # ... (get balance, create components)
    
    # ⭐ Start WebSockets FIRST (buffering mode)
    if not self.dry_run:
        self.logger.info("🔐 Starting Private WebSocket (buffering mode)...")
        self.private_ws = BybitPrivateWebSocket(...)
        self.private_ws.start()
        
        for symbol in symbols:
            self.logger.info(f"🔐 [{symbol}] Starting Position WebSocket (buffering mode)...")
            position_ws = BybitWebSocket(...)
            position_ws.start()
            self.position_websockets[symbol] = position_ws
    
    # ⭐ Restore state from exchange (WebSockets buffer events)
    for symbol in symbols:
        current_price = self.client.get_market_price(symbol)
        self.strategies[symbol].restore_state_from_exchange(current_price)
        
        # ⭐ Process buffered events and switch to real-time
        self.strategies[symbol]._process_buffered_events()
        self.strategies[symbol]._buffering_mode = False
        
        self.logger.info(f"✅ [{symbol}] Ready for real-time trading")
```

## Преимущества

### 1. Нет пропущенных событий
- WebSocket запускается ДО restore
- Все события между запуском WS и завершением restore накапливаются
- Обрабатываются после restore

### 2. Нет дубликатов
- Фильтрация по order_id
- События от ордеров, созданных в restore, игнорируются

### 3. Нет race conditions
- События обрабатываются ПОСЛЕ полного восстановления состояния
- Локальное состояние известно до обработки событий

### 4. Периодический sync можно делать РЕЖЕ
- Раз в 5 минут вместо 1 минуты
- Только для edge cases (WebSocket disconnect не замечен)
- Меньше нагрузка на REST API

## Недостатки

### 1. Немного сложнее код
- Нужен буфер
- Нужна логика фильтрации дубликатов
- Дополнительное состояние (_buffering_mode)

### 2. Задержка обработки событий
- События в буфере не обрабатываются сразу
- Но задержка 5-10 секунд (время restore) допустима при старте

### 3. Потенциальная утечка памяти
- Если restore зависнет → буфер будет расти
- Нужен лимит размера буфера (например, 1000 событий)

## Сравнение подходов

| Критерий | Текущий (sync каждую минуту) | Буферизация событий |
|----------|------------------------------|---------------------|
| Пропущенные события | Обнаруживаются через 60 сек | Обнаруживаются сразу |
| REST API нагрузка | 4-6 calls/min | 2-3 calls/5min |
| Сложность кода | Простая | Средняя |
| Race conditions | Нет (флаг _is_syncing) | Нет (buffering_mode) |
| Дубликаты | Нет | Фильтруются по order_id |
| Production ready | ✅ Да (проверено) | ⚠️ Нужно тестировать |

## Рекомендация

### Для production:
1. **Сейчас:** Оставить sync каждую минуту (работает, проверено)
2. **Оптимизация:** Увеличить интервал до 5 минут (меньше нагрузка)
3. **Долгосрочно:** Реализовать буферизацию (лучше архитектура)

### Приоритет изменений:
1. ✅ **HIGH**: Вернуть sync раз в минуту (сделано)
2. 🔵 **MEDIUM**: Event buffering при старте (оптимизация)
3. 🟢 **LOW**: Увеличить sync до 5 минут (после buffering)

## Лучшие практики

Ваше предложение соответствует industry best practices:

1. **Event Sourcing** - накапливать события и replay
2. **Idempotency** - фильтрация дубликатов по ID
3. **Eventually Consistent** - состояние конвергирует к правильному
4. **Graceful Degradation** - если WebSocket упал, REST API подхватит

Это используется в:
- Apache Kafka (event buffering)
- Redis Streams (replay events)
- Financial trading systems (order reconciliation)

