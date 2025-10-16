# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SOL-Trader is a **multi-account** automated Bybit futures trading bot implementing a dual-sided grid strategy (simultaneous LONG + SHORT positions) for perpetual futures. The bot supports multiple isolated accounts (for SaaS model), each with independent API credentials, strategies, risk limits, and data files. Each account uses position averaging with a multiplier on each grid level and takes profit when prices reverse by a configured percentage.

**Language:** Python 3.9+ (minimum required)
**Exchange:** Bybit (Demo and Production)
**Package Manager:** UV (Universal Virtualenv)
**Architecture:** Multi-account SaaS-ready with complete data isolation
**Testing:** 169 comprehensive unit and integration tests

## Core Architecture

### Multi-Account Layer Design

1. **Account Orchestration Layer** (`src/core/`)
   - `multi_account_bot.py`: Orchestrates multiple isolated accounts, manages WebSocket sharing by (symbol, environment)
   - `trading_account.py`: Represents one isolated user account with own credentials, strategies, risk limits, and data files
   - `state_manager.py`: Persists account state to JSON (e.g., `data/001_bot_state.json`) per account

2. **Exchange Layer** (`src/exchange/`)
   - `bybit_client.py`: HTTP API wrapper using pybit for **commands** (order execution, leverage setting) and **position queries** (get_active_position at startup)
   - `bybit_websocket.py`: **REST API + WebSocket Hybrid** - Position sync via REST API, real-time updates via WebSocket:
     - **Public Stream:** Real-time price updates (ticker)
     - **Private Streams:** Execution updates (real-time TP/closes), wallet updates (balance/MM rate), order updates (TP tracking)
     - **Reliability Features:** Auto-reconnect with exponential backoff, heartbeat monitoring for silent disconnects
     - **Thread-Safe:** All callbacks run in separate threads with proper locking

3. **Strategy Layer** (`src/strategy/`)
   - `position_manager.py`: Tracks separate LONG and SHORT position lists, calculates weighted average entry prices, unrealized PnL
   - `grid_strategy.py`: Core trading logic - determines when to add positions (averaging), when to take profit, and enforces risk limits (including configurable MM Rate threshold)

4. **Utilities Layer** (`src/utils/`)
   - `balance_manager.py`: **WebSocket-first** balance/MM rate management. REST API only at startup, then real-time updates via Wallet WebSocket. Thread-safe.
   - `timestamp_converter.py`: Bybit timestamp to Helsinki timezone conversion
   - `emergency_stop_manager.py`: Emergency stop flag file management
   - Other utilities: `logger.py`, `timezone.py`, `config_loader.py`

5. **Configuration** (`config/`)
   - `constants.py`: All magic numbers, validation limits, trading constants
   - `config.yaml`: Bot configuration (accounts, strategies, risk limits)

6. **Analytics Layer** (`src/analytics/`)
   - `metrics_tracker.py`: Per-account performance tracking, CSV logging with account ID prefix (e.g., `001_trades_history.csv`)

### Strategy Logic Flow

The bot operates via **WebSocket callback system** (no REST API polling):

**Price Updates (Public WebSocket):**
1. Public WebSocket receives price update
2. `main.py::on_price_update()` calls `grid_strategy.on_price_update()`
3. Strategy checks in order:
   - Risk limits (MM Rate from Wallet WebSocket)
   - Grid entry triggers (price moved grid_step_percent against position)
   - Take profit triggers (price moved tp_percent in favor of position)
4. Position manager tracks all positions with grid levels and calculates PnL

**Position Restoration (REST API at startup):**
- `get_active_position()` fetches real position state from exchange on bot start
- Compares exchange vs local with strict tolerance (0.001)
- Restores positions if exchange has them but local tracking is empty
- Fail-fast on unexplained mismatches

**Execution Updates (Private WebSocket during operation):**
- Execution WebSocket reports real-time closes with PnL
- Detects TP fills, manual closes, liquidations
- Automatic reopening after position closes

**Wallet Updates (Private WebSocket):**
- Real-time balance and MM Rate updates → cached in balance_manager
- No REST API polling for balance

**Order Updates (Private WebSocket):**
- Tracks TP order IDs automatically (New, Filled, Cancelled)
- No REST API polling for order status

**Periodic Sync (Every 60 seconds):**
- `sync_with_exchange()` verifies positions with REST API `get_active_position()`
- Checks if TP orders exist for open positions
- Detects discrepancies and fail-fast if found
- Metrics tracker logs snapshots

**Key Insight:** Each side (LONG/SHORT) is managed independently. Positions are added when price moves against the side (averaging down/up), and closed when price reverses favorably. **Position restoration uses REST API (startup), real-time updates use Execution WebSocket (operation).**

### Critical Architecture Decisions

**1. Fail-Fast Principle (NO FALLBACKS):**
The bot NEVER uses fallback or default values for critical data. If data cannot be fetched from exchange, bot stops with error.
- No fake liquidation calculations - always uses real `liqPrice` from Bybit
- No default balance values - fetches real balance or raises RuntimeError
- No placeholder instrument info - fetches from exchange or raises error
- No estimated PnL after close - fetches real closed PnL from exchange

**Why:** Fallbacks mask problems and lead to incorrect trading decisions. Better to stop than trade with wrong data.

**2. Real Liquidation Price from Exchange:**
- Uses `liqPrice` field from Bybit `get_active_position()` response
- `liqPrice` correctly accounts for entire account balance in Cross Margin mode
- Emergency close triggers when distance to `liqPrice` < `liquidation_buffer` %
- NEVER calculates liquidation locally - exchange knows best

**3. Classic Martingale Formula:**
Each new position size = last position size × multiplier
- Sequence: 1 → 2 → 4 → 8 → 16 → 32...
- NOT: 1 → 1 → 2 → 4 → 8... (old incorrect formula)
- Formula: `new_margin = last_position_margin * multiplier`

**4. Symbol Prefixes in All Logs:**
Every log message includes `[{self.symbol}]` prefix for multi-symbol trading clarity.

**5. Reference Qty for Perfect Hedging:**
The bot uses **Reference Qty Per Level** system to ensure LONG and SHORT have identical quantities on each grid level.
- When first side opens level N → saves qty as reference: `_reference_qty_per_level[N] = qty`
- When second side opens level N → uses saved reference qty (not calculated from margin)
- **Result:** Perfect qty symmetry regardless of price difference
- **Example:** LONG @ $0.21 = 357 coins, SHORT @ $0.20 = 357 coins ✅ (not 375!)
- **Benefit:** True hedging - equal P&L on price moves, weighted average TP works correctly
- **Persistence:** Reference restored from position history on bot restart

**Why:** In dual-sided hedge strategy, having different quantities on each side means imperfect hedging. A $0.01 price move would affect sides differently, breaking the hedge. Reference qty ensures mathematical symmetry.

### Position Lifecycle

- **Entry**: Initial positions opened in `main.py::initialize()` at startup
- **Averaging**: New positions added at grid levels when price moves grid_step_percent against side
- **Sizing**: Each new position is sized using `current_total_qty * (multiplier - 1)`
- **Exit**: All positions on a side close together when weighted average entry price shows take_profit_percent gain
- **Emergency Close**: Positions close if Account MM Rate >= threshold (configurable per account, default 90%)
- **Balance Check**: Before each averaging, bot checks `totalAvailableBalance` from exchange to ensure sufficient margin

### Critical Risk Management

**High leverage warning (75-100x typical):** At high leverage, liquidation occurs from small price movements. The bot uses **Account Maintenance Margin Rate from Bybit** (not calculated locally) to monitor liquidation risk. Emergency close triggers when `Account MM Rate >= mm_rate_threshold` (configurable per account, default 90%). This accounts for entire account balance in Cross Margin mode and is the most accurate liquidation indicator.

### Multi-Account Architecture

The bot supports multiple isolated accounts (for SaaS model), each representing a different user/client:

**Per-Account Isolation (complete data separation):**
- **API Credentials**: Each account has own `{ID}_BYBIT_API_KEY` and `{ID}_BYBIT_API_SECRET` in `.env`
- **BybitClient**: Independent API client with unique credentials per account
- **Strategies**: Per-account strategies configuration (can trade different symbols)
- **Risk Limits**: Per-account `mm_rate_threshold` (e.g., account 1 = 90%, account 2 = 50%)
- **Data Files**: All files prefixed with account ID (e.g., `001_bot_state.json`, `001_trades_history.csv`)
- **Log Files**: 3 separate log files per account (`{ID}_bot_{date}.log`, `{ID}_trades_{date}.log`, `{ID}_positions_{date}.log`)
- **Emergency Stop**: Per-account flag (`.{ID}_emergency_stop`)

**Shared Components (efficiency):**
- **WebSocket Sharing**: One WebSocket per unique (symbol, environment) pair, broadcast to all accounts trading that pair
- **Main Orchestrator**: `MultiAccountBot` coordinates all accounts and manages WebSocket sharing

**Key Design Points:**
- Complete isolation: one account's failure doesn't affect others
- Zero-padded IDs (001, 002, ..., 999) for file sorting
- File naming: `{ID}_filename` (ID as prefix, not suffix)
- Each account can be in different environment (demo/production)
- SaaS-ready: easy to add/remove accounts via config

## Development Commands

### Environment Setup
```bash
# Create virtual environment
~/.local/bin/uv venv

# Activate
source .venv/bin/activate

# Install dependencies
~/.local/bin/uv pip install -r requirements.txt
```

### Running the Bot
```bash
# Main bot execution (foreground)
python src/main.py

# Analytics (requires bot to have run first)
python scripts/analyze.py
python scripts/analyze.py --plot --period 24h

# Utility scripts
python scripts/check_balance.py        # Check demo account balance
python scripts/pre_launch_check.py     # Pre-launch validation
python scripts/check_hedge_mode.py     # Verify hedge mode configuration
```

### Background/Service Mode
```bash
# Setup bot as systemd service (one-time setup, requires sudo)
sudo bash scripts/setup_service.sh

# Control service using bot_control.sh
# Note: Ensure script is executable: chmod +x scripts/bot_control.sh
bash scripts/bot_control.sh start      # Start bot service
bash scripts/bot_control.sh stop       # Stop bot service
bash scripts/bot_control.sh restart    # Restart bot service
bash scripts/bot_control.sh status     # Check service status
bash scripts/bot_control.sh logs       # View systemd logs (real-time)
bash scripts/bot_control.sh logs-bot   # View bot file logs

# Alternative: Direct systemd commands
sudo systemctl start sol-trader
sudo systemctl stop sol-trader
sudo systemctl status sol-trader
sudo journalctl -u sol-trader -f

# Alternative: Run in screen session (simpler, no auto-restart)
bash scripts/run_background.sh
```

### Testing
```bash
# Run all tests (169 total)
pytest tests/ -v

# Run specific test file
pytest tests/test_position_manager.py -v
pytest tests/test_grid_strategy.py -v
pytest tests/test_integration.py -v

# Run specific test
pytest tests/test_position_manager.py::TestPositionManager::test_get_liquidation_distance_long -v

# Run tests matching pattern
pytest tests/ -k "liquidation" -v

# Dry run mode (logs only, no API calls)
# Set in config/config.yaml: bot.dry_run = true
python src/main.py
```

## Configuration

All configuration in `config/config.yaml`:

**Multi-account structure:**
Bot supports multiple isolated accounts. Each account represents one user/client with full isolation.

**IMPORTANT:** Each account MUST have at least one strategy configured. The bot will fail to start without a strategy.

```yaml
accounts:
  - id: 1                                  # Unique ID (1-999)
    name: "Account Name"                    # Display name
    api_key_env: "1_BYBIT_API_KEY"         # Env variable for API key
    api_secret_env: "1_BYBIT_API_SECRET"   # Env variable for API secret
    demo_trading: true                      # Demo (true) or Production (false)
    dry_run: false                          # Dry run (true) or Live (false)

    risk_management:                        # Per-account risk limits
      mm_rate_threshold: 90.0              # Emergency close when Account MM Rate >= this %

    strategies:                             # Per-account strategies (REQUIRED - at least one!)
      - symbol: "DOGEUSDT"                 # Trading pair (REQUIRED!)
        category: "linear"                  # Contract type
        leverage: 75                        # Trading leverage (HIGH RISK)
        initial_position_size_usd: 1.0     # Starting margin in USD
        grid_step_percent: 1.0             # Price movement % to trigger averaging
        averaging_multiplier: 2.0          # Martingale multiplier (1→2→4→8)
        take_profit_percent: 1.0           # Profit % to close positions
        max_grid_levels_per_side: 10       # Maximum averaging levels
```

**Strategy parameters (per symbol):**
- `symbol`: Trading pair (REQUIRED!)
- `leverage`: Trading leverage (75-100x typical - HIGH RISK)
- `initial_position_size_usd`: Starting margin in USD (leverage applied automatically!)
- `grid_step_percent`: Price movement % to trigger averaging
- `averaging_multiplier`: Classic martingale multiplier (2.0 = 1→2→4→8→16)
- `take_profit_percent`: Profit % to close positions
- `max_grid_levels_per_side`: Maximum averaging levels

**Risk parameters (per account):**
- `mm_rate_threshold`: Emergency close when Account MM Rate >= this % (default: 90)

**DEPRECATED parameters (no longer used):**
- `max_total_exposure` → Uses `totalAvailableBalance` from exchange
- `liquidation_buffer` → Uses Account MM Rate from exchange
- `emergency_stop_loss` → Not implemented (MM Rate protection is better)

**Account modes (per account):**
- `demo_trading`: true = demo environment, false = production
- `dry_run`: true = simulation (no API calls), false = real execution

### API Credentials

Stored in `.env` (not committed), with **ID prefix format**:
```bash
# Account 1
1_BYBIT_API_KEY=your_key_here
1_BYBIT_API_SECRET=your_secret_here

# Account 2
2_BYBIT_API_KEY=your_key_here
2_BYBIT_API_SECRET=your_secret_here
```

**Important:** ID in `.env` must match `id` in `config.yaml` accounts section.

Get demo API keys from https://testnet.bybit.com

## Data and Logs

**Per-account generated files (ID prefix format):**

Account 001 example:
- `logs/001_bot_YYYY-MM-DD.log`: Main bot events and state changes
- `logs/001_trades_YYYY-MM-DD.log`: Every trade execution
- `logs/001_positions_YYYY-MM-DD.log`: Position state snapshots
- `data/001_performance_metrics.csv`: Time-series snapshots (every 60s)
- `data/001_trades_history.csv`: All trades with PnL
- `data/001_bot_state.json`: Persisted position state (for recovery after restarts)

**System logs:**
- `logs/main_YYYY-MM-DD.log`: Orchestrator events (multi-account coordination)

**Emergency stop files:**
- `data/.001_emergency_stop`: Per-account emergency flag (prevents restart until removed)

**File naming convention:**
- **ID as prefix:** `{ID}_filename` (e.g., `001_bot_state.json`, NOT `bot_state_001.json`)
- **Zero-padded IDs:** 001, 002, ..., 999 for proper file sorting
- **Hidden emergency files:** `.{ID}_emergency_stop` (dot prefix)

## Core Utilities

The bot includes several centralized utility modules to eliminate code duplication and improve maintainability:

### BalanceManager (`src/utils/balance_manager.py`)
Centralizes balance and MM Rate retrieval from exchange with intelligent caching.

**Features:**
- **Caching**: 5-second TTL cache to reduce API calls
- **Dual Data**: Fetches both `totalAvailableBalance` and `accountMMRate` in one call
- **Force Refresh**: Optional `force_refresh` parameter bypasses cache
- **Thread-Safe**: Uses timestamp-based cache expiration

**Usage:**
```python
from src.utils.balance_manager import BalanceManager

balance_manager = BalanceManager(client, cache_ttl_seconds=5.0)

# Get balance (cached for 5 seconds)
available_balance = balance_manager.get_available_balance()

# Get MM Rate (shares same cache)
mm_rate = balance_manager.get_mm_rate()

# Force refresh (bypass cache)
fresh_balance = balance_manager.get_available_balance(force_refresh=True)
```

**Why:** Eliminates 3 critical code duplications (balance retrieval in grid_strategy.py at 3 locations). Reduces API calls by ~80% through intelligent caching.

### TimestampConverter (`src/utils/timestamp_converter.py`)
Converts Bybit millisecond timestamps to Helsinki timezone strings.

**Features:**
- **Static Methods**: No instance needed
- **Validation**: Checks timestamp range (2020-2050)
- **Format**: Returns "YYYY-MM-DD HH:MM:SS" format
- **Handles Edge Cases**: Returns None for 0 or invalid timestamps

**Usage:**
```python
from src.utils.timestamp_converter import TimestampConverter

# Convert Bybit timestamp (ms) to Helsinki time
timestamp_ms = 1736939400000
helsinki_time = TimestampConverter.exchange_ms_to_helsinki(timestamp_ms)
# Returns: "2025-01-15 12:30:00"

# Validate timestamp
is_valid = TimestampConverter.is_valid_timestamp_ms(timestamp_ms)
```

**Why:** Eliminates duplicate timestamp conversion code (2 locations in grid_strategy.py). Ensures consistent timezone handling across the bot.

### EmergencyStopManager (`src/utils/emergency_stop_manager.py`)
Centralized management of emergency stop flag files.

**Features:**
- **File Creation**: Creates hidden flag files with timestamp and reason
- **Validation**: Checks for emergency flags during startup
- **Data Retrieval**: Reads flag data for diagnostics
- **Static Methods**: Can be used without creating instance

**Usage:**
```python
from src.utils.emergency_stop_manager import EmergencyStopManager

# Check if emergency stop exists
if EmergencyStopManager.exists(account_id=1):
    data = EmergencyStopManager.get_data(account_id=1)
    print(f"Reason: {data['reason']}")

# Create emergency stop flag
manager = EmergencyStopManager(logger=logger)
manager.create(
    account_id=1,
    symbol="DOGEUSDT",
    reason="MM Rate exceeded 90%",
    additional_data={"mm_rate": 95.5}
)

# Validate during startup (raises RuntimeError if exists)
EmergencyStopManager.validate_and_raise(
    account_id=1,
    account_name="Main Account"
)
```

**Why:** Eliminates duplicate emergency stop handling code across 3 files (grid_strategy.py, trading_account.py, main.py). Provides consistent error messages and file format.

### Constants (`config/constants.py`)
Centralizes all magic numbers and configuration values.

**Classes:**
- **TradingConstants**: Intervals, limits, fees, position indices
- **ValidationLimits**: Min/max values for leverage, percentages, grid levels
- **LogMessages**: Standard log message templates (for future use)

**Usage:**
```python
from config.constants import TradingConstants, ValidationLimits

# Use trading constants
cache_ttl = TradingConstants.BALANCE_CACHE_TTL_SEC  # 5.0
order_limit = TradingConstants.ORDER_HISTORY_LIMIT  # 50
position_idx = TradingConstants.POSITION_IDX_LONG  # 1

# Use validation limits
if not (ValidationLimits.MIN_LEVERAGE <= leverage <= ValidationLimits.MAX_LEVERAGE):
    raise ValueError(f"Leverage {leverage} out of range")
```

**Why:** Eliminates 10+ magic numbers scattered throughout code. Makes configuration changes easier and reduces errors.

### Input Validation (`grid_strategy.py::_validate_config()`)
Comprehensive validation of all strategy configuration parameters.

**Validates:**
- Leverage: 1-200
- Position size: $0.1-$100,000
- Grid step: 0.01%-100%
- Multiplier: >1.0-10.0
- Take profit: 0.01%-100%
- Max grid levels: 1-50
- MM rate threshold: 0-100%

**Behavior:**
- Runs during GridStrategy initialization
- Collects all validation errors
- Raises ValueError with detailed error message
- Logs validation success/failure

**Why:** Prevents configuration errors that could cause runtime failures or incorrect trading behavior. Fail-fast principle applied to configuration.

## Important Patterns

### Adding New Exchange Methods
When adding new Bybit API functionality, wrap it in `bybit_client.py` with proper error handling and logging. Follow the pattern:
```python
def new_method(self, ...):
    try:
        response = self.session.api_method(...)
        self.logger.info(f"Success message")
        return response
    except Exception as e:
        self.logger.error(f"Failed: {e}")
        raise
```

### Modifying Strategy Logic
Grid logic lives in `grid_strategy.py`. The three check methods (`_check_grid_entries`, `_check_take_profit`, `_check_risk_limits`) run on every price update. Keep them efficient - they execute multiple times per second.

### Position Tracking
Never modify `position_manager.py` position lists directly. Always use:
- `add_position()` to add
- `remove_all_positions()` to close
These methods maintain consistency with `last_long_entry` and `last_short_entry` tracking.

### State Persistence and Sync

**REST API + WebSocket Hybrid Architecture:**
The bot uses REST API for position restoration at startup, and WebSocket for real-time updates during operation.

**Position Restoration Flow (on bot restart):**
1. Bot starts, `sync_with_exchange()` called on first price update
2. **REST API Check:** Fetches position from exchange via `get_active_position()` (source of truth)
3. Compares `exchange_qty` vs `local_qty` with strict tolerance (0.001 for rounding only)
4. **Decision logic:**
   - If `qty_diff <= 0.001`: **SYNCED** → Only verify TP order exists
   - If `exchange_qty > 0` and `local_qty == 0`: **RESTORE** → Restore position from exchange data
   - If `exchange_qty == 0` and `local_qty == 0`: **OPEN** → Open initial position
   - Otherwise: **FAIL-FAST** → Emergency stop (manual intervention required)
5. State file updates automatically via `pm.add_position()`

**Position Close Detection (during operation):**
When Execution WebSocket reports position close:
- Uses real PnL from `execPnl` field (REQUIRED - no fallback!)
- Determines close reason (positive PnL = Take Profit, negative = Loss/Liquidation)
- Logs with full details to bot log and CSV
- Records to metrics_tracker
- Reopens positions if appropriate

**Periodic Sync (Every 60 seconds):**
- `sync_with_exchange()` runs REST API position check
- Verifies TP orders exist for open positions
- Detects any discrepancies and fail-fast if found

**Key Design Decisions:**
1. **REST API at startup:** Exchange is source of truth after bot restart
2. **Fail-Fast on mismatch:** No approximations - strict tolerance of 0.001
3. **WebSocket during operation:** Real-time updates via Execution WebSocket
4. **No Position WebSocket restoration:** Bybit's Position WebSocket snapshot is unreliable

## Testing Strategy

### Runtime Testing Modes

1. **Dry Run Testing** (bot.dry_run = true): Test logic without API calls
2. **Demo Trading** (demo_trading = true, dry_run = false): Real API calls with virtual money
3. **Production** (demo_trading = false, dry_run = false): Real money - USE WITH CAUTION

Always test new features in dry run, then demo, before considering production use.

### Unit Testing

The project has comprehensive test coverage across all components:

**Test Files (169 tests total):**
- `test_bybit_client.py` (24 tests): Bybit API client
- `test_grid_strategy.py` (47 tests): Grid strategy logic + validation
- `test_integration.py` (14 tests): End-to-end strategy tests
- `test_position_manager.py` (20 tests): Position tracking
- `test_timezone.py` (12 tests): Timezone conversion
- `test_balance_manager.py` (17 tests): Balance caching utility
- `test_timestamp_converter.py` (26 tests): Timestamp conversion utility
- `test_emergency_stop_manager.py` (21 tests): Emergency stop management

**Running Tests:**
```bash
# Run all tests (169 total)
pytest tests/ -v

# Run specific test file
pytest tests/test_balance_manager.py -v

# Run specific test
pytest tests/test_grid_strategy.py::TestConfigValidation::test_invalid_leverage_too_low -v

# Run tests matching pattern
pytest tests/ -k "validation" -v
```

**Test Coverage:**
- Core trading logic: 100%
- Utilities: 100%
- Configuration validation: 100%
- Edge cases and error handling: Comprehensive

**Key Test Features:**
- **Fixtures**: Reusable mock clients, position managers, strategies
- **Mocking**: All external API calls mocked for speed and reliability
- **Parametrization**: Testing boundary values and invalid inputs
- **Integration Tests**: End-to-end trading scenarios

## Dependencies

Core libraries:
- `pybit`: Official Bybit API client (HTTP and WebSocket)
- `python-dotenv`: Environment variable management
- `pyyaml`: YAML configuration parsing
- `pandas`: Analytics data processing
- `matplotlib`: Chart generation for analytics
- `pytest`: Testing framework

## Known Limitations

1. **Leverage risk**: High leverage (75-100x) means small price movements cause liquidation. Each side liquidates independently despite hedging. Cross Margin mode means entire account balance backs all positions.
2. **Funding rate**: Holding both LONG and SHORT incurs funding rate on both sides (small net cost).
3. **Commission accumulation**: Frequent averaging can accumulate taker fees (~0.055% on Bybit). Use limit orders where possible (not currently implemented).
4. **No partial closes**: Positions close all-or-nothing per side. No gradual exit strategy.
5. **Balance checks use shared account balance**: All symbols share the same `totalAvailableBalance` from exchange. Each averaging checks if sufficient balance exists before placing order.
6. **Emergency close on data issues**: Bot stops immediately if exchange data cannot be fetched (fail-fast principle).

## Quick Reference

**Start bot in demo mode (foreground):**
1. Set API keys in `.env` with ID prefix format:
   ```
   1_BYBIT_API_KEY=xxx
   1_BYBIT_API_SECRET=yyy
   ```
2. Ensure `config/config.yaml` has:
   - `accounts:` section with at least one account (REQUIRED!)
   - Account `id: 1` matching .env credentials
   - Account has `demo_trading: true` and `dry_run: false`
   - Account has at least one strategy in `strategies:` array
3. Run `python src/main.py`
4. Monitor logs:
   - Per-account: `tail -f logs/001_bot_YYYY-MM-DD.log`
   - System: `tail -f logs/main_YYYY-MM-DD.log`
5. Stop with Ctrl+C (graceful shutdown)

**Start bot as background service:**
1. One-time setup: `sudo bash scripts/setup_service.sh`
2. Start: `sudo systemctl start sol-trader`
3. Check status: `sudo systemctl status sol-trader`
4. View logs: `sudo journalctl -u sol-trader -f`
5. Filter by account: `sudo journalctl -u sol-trader -f | grep "\[001\]"`
6. Stop: `sudo systemctl stop sol-trader`

See `BACKGROUND_SERVICE_GUIDE.md` for detailed instructions on running the bot 24/7.

**Analyze performance:**
```bash
python scripts/analyze.py --plot
```

**Check demo account balance:**
```bash
python scripts/check_balance.py
```

**Run tests:**
```bash
pytest tests/ -v  # All 169 tests
```

## Common Development Workflows

### Adding a New Account

1. Stop the bot: `sudo systemctl stop sol-trader`
2. Add credentials to `.env`:
   ```
   2_BYBIT_API_KEY=xxx
   2_BYBIT_API_SECRET=yyy
   ```
3. Edit `config/config.yaml` - add new account entry under `accounts:`
4. Start bot: `sudo systemctl start sol-trader`
5. Verify in logs: `sudo journalctl -u sol-trader -f | grep "\[002\]"`

### Adding a New Symbol to Account

1. Stop the bot: `sudo systemctl stop sol-trader`
2. Edit `config/config.yaml` - add strategy to account's `strategies:` array
3. Ensure symbol is valid on Bybit (check https://testnet.bybit.com for demo)
4. Start bot: `sudo systemctl start sol-trader`
5. Verify in logs: `sudo journalctl -u sol-trader -f | grep "\[001\]" | grep "NEWSYMBOL"`

### Debugging Position Issues

1. Check account state: `cat data/001_bot_state.json | python3 -m json.tool`
2. View recent trades: `cat data/trades_history.csv | tail -20`
3. Check liquidation warnings: `sudo journalctl -u sol-trader | grep "liquidation"`
4. Verify positions on exchange manually at https://testnet.bybit.com (demo) or https://bybit.com (prod)

### Modifying Grid Strategy Logic

Files to modify:
- `src/strategy/grid_strategy.py`: Entry/exit logic
- `src/strategy/position_manager.py`: Position calculations
- Tests: `tests/test_grid_strategy.py` and `tests/test_integration.py`

Critical: Always update tests when changing strategy logic. Run `pytest tests/ -v` before committing.

### Adding New Risk Checks

1. Add check method in `grid_strategy.py` (e.g., `_check_new_risk()`)
2. Call from `on_price_update()` or `_check_risk_limits()`
3. Ensure check uses real exchange data - NO fallbacks or estimates
4. Add test in `tests/test_grid_strategy.py::TestRiskLimits`
5. Test with dry_run=true first, then demo_trading

### Troubleshooting Bot Crashes

1. Check systemd logs: `sudo journalctl -u sol-trader -n 100`
2. Check bot file logs: `tail -100 logs/bot_$(date +%Y-%m-%d).log`
3. Check for errors: `sudo journalctl -u sol-trader | grep ERROR`
4. Common causes:
   - Missing symbol in config (bot requires at least one strategy)
   - Exchange API timeout (network issue)
   - Invalid API keys (check .env file)
   - Insufficient balance for min order size
