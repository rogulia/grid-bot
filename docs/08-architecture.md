# 🏗️ Архитектура

Техническая архитектура SOL-Trader.

---

## 📊 Общая архитектура

```
┌────────────────────────────────────────────────────────────┐
│                  Multi-Account Bot                          │
│                (Orchestrator Layer)                         │
│                                                             │
│  • WebSocket Sharing по ключу (symbol, demo: bool)         │
│  • Routing price updates к подписчикам                      │
│  • Lifecycle management для accounts                        │
└────────────────────────────────────────────────────────────┘
                           │
      ┌────────────────────┼────────────────────┐
      ▼                    ▼                    ▼
┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
│  Account 001     │ │  Account 002     │ │  Account 003     │
│  (Isolated)      │ │  (Isolated)      │ │  (Isolated)      │
│                  │ │                  │ │                  │
│  • Credentials   │ │  • Credentials   │ │  • Credentials   │
│  • Strategies    │ │  • Strategies    │ │  • Strategies    │
│  • Risk limits   │ │  • Risk limits   │ │  • Risk limits   │
│  • Data files    │ │  • Data files    │ │  • Data files    │
└──────────────────┘ └──────────────────┘ └──────────────────┘
```

---

## 🏛️ Слои архитектуры

### 1. Account Orchestration Layer (`src/core/`)

**multi_account_bot.py:**
- Управление множеством изолированных аккаунтов
- WebSocket sharing по (symbol, environment) ключу
- Broadcast price updates всем подписчикам

**trading_account.py:**
- Один изолированный user account
- Собственные credentials, strategies, risk limits
- Собственные data files с префиксом {ID}

**state_manager.py:**
- Персистентность состояния в JSON
- Per-account: `data/001_bot_state.json`

### 2. Exchange Layer (`src/exchange/`)

**bybit_client.py:**
- HTTP API wrapper (pybit)
- Команды: place_order, cancel_order, close_position
- Query: get_active_position (startup only)

**bybit_websocket.py:**
- WebSocket-First архитектура
- **Public Stream:** Real-time price (ticker)
- **Private Streams:**
  - Position WebSocket: Snapshot + updates
  - Wallet WebSocket: Balance + MM Rate real-time
  - Order WebSocket: TP order tracking
  - Execution WebSocket: Trade closes с PnL
- Auto-reconnect с exponential backoff
- Thread-safe callbacks

### 3. Strategy Layer (`src/strategy/`)

**position_manager.py:**
- Separate LONG/SHORT position lists
- Weighted average entry price calculation
- Unrealized PnL calculation

**grid_strategy.py:**
- Core trading logic
- Grid entry triggers (price movement detection)
- Take profit triggers (profit >= TP%)
- Risk limit enforcement

### 4. Utilities Layer (`src/utils/`)

**balance_manager.py:**
- WebSocket-first balance management
- REST API only at startup
- Real-time updates via Wallet WebSocket
- Thread-safe with locks

**timestamp_converter.py:**
- Bybit millisecond timestamp → Helsinki timezone
- Static methods, no instance needed

**emergency_stop_manager.py:**
- Per-account emergency flag management
- File: `data/.{ID}_emergency_stop`

**Другие:**
- `logger.py`: Logging setup
- `timezone.py`: Timezone utilities
- `config_loader.py`: YAML config loading

### 5. Configuration (`config/`)

**constants.py:**
- Trading constants (intervals, limits, fees)
- Validation limits (min/max values)
- Magic numbers centralization

**config.yaml:**
- Accounts configuration
- Strategies per account
- Risk limits per account

### 6. Analytics Layer (`src/analytics/`)

**metrics_tracker.py:**
- Per-account performance tracking
- CSV logging: `001_trades_history.csv`, `001_performance_metrics.csv`
- Account ID prefix for all files

---

## 🔄 Data Flow

### WebSocket → Strategy → Order Execution

```
1. WebSocket: Price update received
   ↓
2. Orchestrator: Broadcast to all subscribers
   ↓
3. TradingAccount: Route to strategy
   ↓
4. GridStrategy: Check triggers
   - Grid entry? → _execute_grid_order()
   - Take profit? → _close_position()
   - Risk limit? → Emergency close
   ↓
5. BybitClient: Execute order via REST API
   ↓
6. Execution WebSocket: Confirmation callback
   ↓
7. PositionManager: Update local state
   ↓
8. StateManager: Persist to JSON
```

### Position Restoration Flow (Startup)

```
1. Bot starts → TradingAccount.initialize()
   ↓
2. Position WebSocket connects → Snapshot received
   ↓
3. GridStrategy.on_position_update()
   - Exchange position exists?
   - Local tracking empty?
   ↓ YES (restore case)
4. Restore position from WebSocket data
   - avgPrice, size from exchange
   - Create TP order
   - Log as "RESTORE"
   ↓
5. State file auto-updates
```

---

## 🔒 Thread Safety

**Shared resources protected with locks:**

```python
# BalanceManager
self._lock = threading.Lock()
with self._lock:
    self._cached_balance = balance
    self._cached_mm_rate = mm_rate

# GridStrategy
self._tp_orders_lock = threading.Lock()
with self._tp_orders_lock:
    self._tp_orders[side] = order_id

# PositionManager
self._positions_lock = threading.Lock()
with self._positions_lock:
    self.long_positions.append(position)
```

**Why:** pybit WebSocket runs callbacks in separate threads.

---

## 🗂️ File Structure per Account

### Account 001 Example:

```
data/
├── 001_bot_state.json           # State persistence
├── 001_performance_metrics.csv  # Metrics (every 60s)
├── 001_trades_history.csv       # Trade history
└── .001_emergency_stop          # Emergency flag (if exists)

logs/
├── 001_bot_2025-10-14.log       # Main events
├── 001_trades_2025-10-14.log    # Trade executions
├── 001_positions_2025-10-14.log # Position snapshots
└── main_2025-10-14.log          # System events
```

**File naming convention:**
- **Prefix format:** `{ID}_filename` (e.g., `001_bot_state.json`)
- **Zero-padded IDs:** 001, 002, ..., 999
- **Hidden emergency files:** `.{ID}_emergency_stop`

---

## 📡 WebSocket Sharing Logic

```python
# Key: (symbol, demo: bool)
ws_key = ("DOGEUSDT", True)  # Demo
ws_key = ("DOGEUSDT", False) # Production

# Sharing example:
Account1: SOLUSDT (demo) → WebSocket A
Account2: SOLUSDT (demo) → WebSocket A (shared!)
Account3: SOLUSDT (prod) → WebSocket B (different env)
Account4: DOGEUSDT (demo) → WebSocket C (different symbol)
```

**Efficiency:** N accounts × M symbols → K WebSockets (K < N×M)

---

## 🧩 Глоссарий терминов

### Торговые термины

**Hedge Mode:**
- Одновременное удержание LONG и SHORT позиций на один актив
- Каждая позиция имеет свой `positionIdx`: 1=LONG, 2=SHORT

**Cross Margin (Portfolio Margin):**
- Весь баланс аккаунта используется как обеспечение
- Ликвидация происходит по всему аккаунту сразу
- Account MM Rate учитывает все открытые позиции

**Grid Averaging:**
- Усреднение позиций по сетке (grid) при движении цены против позиции
- Trigger: цена двигается на `grid_step_percent` против стороны

**Martingale:**
- Прогрессивное увеличение размера позиций
- Formula: `new_margin = last_margin × multiplier`
- Progression: 1 → 2 → 4 → 8 → 16 → 32...

**Take Profit (TP):**
- Автоматическое закрытие при достижении целевой прибыли
- Учитывает комиссии (fee-adjusted TP)

**Account MM Rate (Maintenance Margin Rate):**
- Ключевой показатель риска ликвидации
- Formula: (Required MM / Account Balance) × 100%
- Emergency close trigger: MM Rate >= threshold (default 90%)

### Архитектурные термины

**WebSocket-First:**
- Архитектурный подход: все мониторинг данные через WebSocket
- REST API только для commands (place_order, close_position)
- Zero REST API polling

**Fail-Fast:**
- Принцип: нет fallback значений для критических данных
- Если данные недоступны → бот останавливается с ошибкой
- Примеры: реальный liqPrice, реальный accountMMRate

**Adaptive:**
- Адаптивное поведение бота к условиям рынка
- Adaptive reopen: размер зависит от margin ratio
- Adaptive safety factor: зависит от ATR волатильности

**Multi-Account SaaS-Ready:**
- Поддержка множества изолированных аккаунтов
- Полная изоляция данных, credentials, risk limits
- Готовность к SaaS модели с веб-интерфейсом

**Thread-Safe:**
- Безопасная работа в многопоточной среде
- Все shared resources защищены locks
- WebSocket callbacks выполняются в отдельных threads

### Риск-менеджмент термины

**Early Freeze:**
- Превентивная блокировка усреднений
- Trigger: `available < next_worst_case × 1.5`
- TP продолжают работать

**Panic Mode:**
- Критический режим при низком available balance
- Triggers: LOW_IM или HIGH_IMBALANCE
- Actions: freeze averaging + intelligent TP management + balancing

**Emergency Close:**
- Немедленное закрытие ВСЕХ позиций
- Trigger: Account MM Rate >= threshold
- Создание emergency stop flag (бот не перезапустится)

**ATR (Average True Range):**
- Индикатор волатильности
- Используется для адаптации safety factor
- Расчет за последние 20 периодов

**Safety Factor:**
- Множитель для safety reserve
- ATR-based: 1.17-1.25
- Buffer components: base (0.10) + gap (0.02-0.10) + tier (0.05)

---

## 🧪 Testing Architecture

### Test Structure (172 тестов)

```
tests/
├── conftest.py                  # Fixtures и моки
├── test_position_manager.py     # 20 тестов
├── test_grid_strategy.py        # 47 тестов
├── test_integration.py          # 14 тестов
├── test_bybit_client.py         # 24 тестов
├── test_timezone.py             # 12 тестов
├── test_balance_manager.py      # 17 тестов
├── test_timestamp_converter.py  # 26 тестов
└── test_emergency_stop_manager.py # 21 тестов
```

**Test Coverage:**
- Core logic: 100%
- Utilities: 100%
- Config validation: 100%
- Edge cases: Comprehensive

---

## 🚀 Deployment

### Development

```bash
python src/main.py  # Терминал (Ctrl+C to stop)
```

### Production

```bash
sudo systemctl start sol-trader   # Systemd service
sudo journalctl -u sol-trader -f  # Logs
```

См. подробнее: [05-operations.md](05-operations.md)

---

## 🔮 Future Architecture

### Planned Features

**Web UI (SaaS):**
- Real-time dashboard per account
- Configuration management via UI
- Performance charts и metrics
- Multi-user support с authentication

**Notifications:**
- Telegram bot integration
- Email alerts on critical events
- Webhook support для custom integrations

**Advanced Analytics:**
- Sharpe ratio calculation
- Maximum drawdown tracking
- Win rate statistics
- Risk-adjusted returns

**Database Layer:**
- Migration from CSV/JSON to PostgreSQL
- Historical data для backtesting
- Query optimization для analytics

---

**Готово!** Архитектура и глоссарий покрывают все ключевые концепции.
