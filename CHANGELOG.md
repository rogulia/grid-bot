# Changelog

All notable changes to the SOL-Trader project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [3.1.0] - 2025-10-13

### Added - Advanced Risk Management System v3.1

This release introduces a comprehensive, multi-layered risk management system that provides intelligent protection mechanisms beyond simple MM Rate monitoring. The system uses dynamic calculations, preventive measures, and adaptive strategies to protect account equity during high-risk trading scenarios.

#### Phase 1: Dynamic Safety Factor Based on ATR
- **`GridStrategy.calculate_atr_percent()`** - Calculates Average True Range as percentage with 60s caching
- **`TradingAccount.calculate_safety_factor()`** - Dynamic safety factor (1.17-1.25) based on market volatility:
  - Low volatility (ATR < 1.0%): factor = 1.17 (base + 2% gap + tier)
  - Medium volatility (1.0-2.0%): factor = 1.20 (base + 5% gap + tier)
  - High volatility (> 2.0%): factor = 1.25 (base + 10% gap + tier)
- **Updated `calculate_account_safety_reserve()`** - Now uses worst-case (max) ATR across all symbols

#### Phase 2: Early Freeze Mechanism
- **Preventive averaging block** triggers when `available < next_worst_case × 1.5`
- **`check_early_freeze_trigger()`** - Monitors available margin vs upcoming averaging needs across ALL symbols
- **`freeze_all_averaging()` / `unfreeze_all_averaging()`** - Account-level averaging control
- **Automatic recovery** - Unfreezes when conditions improve (if not in panic)
- **TP orders continue working** during freeze, allowing natural exits
- Integrated into `check_reserve_before_averaging()` - blocks averaging when frozen

#### Phase 3: Panic Mode Implementation
- **Critical state activation** when:
  - PRIMARY: `available < next_worst_case × 3` (LOW_IM trigger)
  - SECONDARY: `imbalance_ratio > 10 AND available < 30%` (HIGH_IMBALANCE trigger)
- **`check_panic_trigger_low_im()` / `check_panic_trigger_high_imbalance()`** - Dual panic triggers
- **`enter_panic_mode()` / `exit_panic_mode()`** - Panic state management with timestamps
- **Integrated actions on panic entry**:
  1. Freeze all averaging (via Early Freeze)
  2. Cancel TP orders intelligently (trend side only - Phase 4)
  3. Attempt adaptive position balancing (Phase 7)
- **Natural exit** - Via counter-trend TP closure → IM recovers → Early Freeze clears → panic triggers stop firing

#### Phase 4: Intelligent TP Management
- **`GridStrategy.determine_trend_side()`** - Identifies trend based on grid levels (side that averaged MORE)
- **`TradingAccount.cancel_tp_intelligently()`** - Removes TP from TREND side, keeps counter-trend TP
- **`TradingAccount._restore_tp_orders_after_panic()`** - Restores TP orders when exiting panic mode
- **Smart reasoning**:
  - Trend side = side that averaged more (price moved against it)
  - Counter-trend side = side that averaged less (closer to profit)
  - Keeping counter-trend TP provides natural panic exit without forcing close
- Integrated into `enter_panic_mode()` - executes after freezing averaging
- Integrated into `exit_panic_mode()` - restores all TP orders after panic recovery

#### Phase 5: Adaptive Reopen by Margin Ratio
- **`GridStrategy.get_total_margin(side)`** - Calculates total margin used by position side
- **`GridStrategy.calculate_reopen_size()`** - Adapts reopen size based on margin imbalance:
  - `ratio ≥ 16` → 100% of opposite margin (large imbalance)
  - `ratio ≥ 8` → 50% (medium)
  - `ratio ≥ 4` → 25% (moderate)
  - `ratio < 4` → initial size (small)
- **Updated `_open_initial_position()`** - Accepts optional `custom_margin_usd` parameter for backward compatibility
- **Integrated into WebSocket callbacks**:
  - `on_execution()` - When execution stream detects position close
  - `on_position_update()` - When position stream reports size < 0.001
- **Logs "ADAPTIVE REOPEN"** with margin amount to metrics tracker
- **Uses margin ratio** (adapts to price changes) instead of level_diff (static)

#### Phase 6: Dynamic IM Monitoring
- **`TradingAccount.monitor_initial_margin()`** - Returns comprehensive metrics dict:
  - `total_balance` - totalAvailableBalance from exchange
  - `total_initial_margin` - Total IM used across all positions
  - `total_maintenance_margin` - Total MM across all positions
  - `account_mm_rate` - Account-level MM Rate percentage
  - `safety_reserve` - Dynamic safety reserve for rebalancing
  - `available_for_trading` - Balance available after IM and reserve
  - `available_percent` - Percentage of equity available
- **`TradingAccount.log_im_status()`** - Intelligent logging with warning levels:
  - INFO: Periodic status (every 60s in `process_price()`)
  - WARNING: available < 30% (yellow flag)
  - ERROR: available < 15% (red flag)
  - CRITICAL: available < 0 (reserve breached!)
- **Integrated into `process_price()`** - Account-level logging once per 60s (not per symbol)
- **Uses WebSocket-cached data** from BalanceManager (zero REST API calls)

#### Phase 7: Position Balancing in Panic Mode
- **`TradingAccount.balance_all_positions_adaptive()`** - Multi-symbol position balancing:
  - Collects imbalance info from ALL symbols (long_qty, short_qty, lagging_side, margin_needed)
  - Calculates total_margin_needed across all symbols
  - **Three balancing strategies**:
    - **Full balancing**: `available ≥ total_needed` → balance all symbols 100%
    - **Partial balancing**: `0 < available < total_needed` → proportional distribution (scale_factor)
    - **Critical state**: `available < $1.00` → skip balancing, log critical
- **Uses ALL available margin** in panic (ignores safety reserve - desperate state)
- **Executes market orders** on lagging side to balance LONG/SHORT
- **Updates position manager** and creates TP orders after balancing
- **Logs "BALANCE" action** to metrics tracker with scale factor
- **Integrated into `enter_panic_mode()`** - Executes after intelligent TP cancellation
- **Returns bool** indicating if balancing was attempted

### Changed
- **Safety reserve calculation** now dynamic based on ATR (was static 1.20 multiplier)
- **Reserve checking** now accounts for Early Freeze state (blocks if frozen)
- **Position reopening** now adaptive based on margin ratio (was always initial size)
- **IM monitoring** moved from manual calculation to centralized `monitor_initial_margin()` method
- **Panic mode handling** now includes position balancing and intelligent TP management

### Technical Details

**Thread Safety:**
- All account-level operations use `_account_lock` to prevent race conditions
- Balance manager updates are atomic
- Position manager operations are thread-safe

**WebSocket-First Architecture:**
- All balance/IM data from Wallet WebSocket (zero REST API calls after startup)
- BalanceManager caches with 5s TTL
- Real-time updates without polling

**Hierarchical Protection:**
- **Early Freeze** (preventive) - `available < next × 1.5`
- **Panic Mode** (critical) - `available < next × 3` OR `ratio > 10 AND available < 30%`
- **Emergency Close** (catastrophic) - `MM Rate ≥ threshold` (default 90%)

**Fail-Safe Design:**
- Block averaging on errors
- No defaults for critical data
- Force refresh only when necessary
- Validate all inputs

### Testing
- **172 existing tests pass** - Zero regressions ✅
- **28 new tests added** for Advanced Risk Management features
  - Phase 1: ATR calculation and safety factor tests
  - Phase 2: Early Freeze trigger and behavior tests
  - Phase 3: Panic Mode activation and workflow tests
  - Phase 4: Intelligent TP and trend detection tests (including TP restoration after panic exit)
  - Phase 5: Adaptive reopen and margin ratio tests
  - Phase 6: IM monitoring and warning threshold tests
  - Phase 7: Position balancing strategies tests
  - Integration: Full workflow tests
- Comprehensive coverage of all new features

### Performance
- **ATR caching** - 60s TTL reduces recalculation overhead
- **BalanceManager caching** - 5s TTL eliminates redundant API calls
- **Efficient WebSocket updates** - Real-time data without polling
- **Minimal computational overhead** - Dynamic calculations only when needed

### Documentation
- Created `docs/IMPLEMENTATION_TODO.md` with full implementation tracking
- All methods documented with comprehensive docstrings
- Added inline comments explaining complex logic
- Updated CLAUDE.md with new risk management features (Phase 9)

### Breaking Changes
**None** - All changes are backward compatible. Existing configurations will continue to work without modification.

### Migration Notes
**No action required** - Bot automatically uses new risk management system. Optional: adjust `mm_rate_threshold` in config.yaml per account if desired (default: 90%).

## [1.5.0] - 2025-01-15

### Changed - REST API Optimization (WebSocket-First Architecture)

**Goal:** Minimize REST API usage by leveraging WebSocket streams for all monitoring data.

#### Eliminated Duplicate Balance Calls
- **Problem:** Multiple REST API calls to `get_wallet_balance()` across different strategy components
- **Solution:** Created shared `BalanceManager` instance per account
  - All strategies share one balance_manager
  - Wallet WebSocket updates cache in real-time
  - Force refresh only on initialization
- **Files Modified:**
  - `src/core/trading_account.py`: Create shared balance_manager (lines 185-206, 247-257)
  - `src/strategy/grid_strategy.py`: Accept optional balance_manager parameter (lines 22-67)
- **Impact:** Eliminated 1+ REST API call per strategy at startup

#### Position Restoration via WebSocket Snapshot
- **Problem:** Using `get_active_position()` REST API in `sync_with_exchange()` to check for positions (~120 calls/hour)
- **Solution:** Position WebSocket restoration
  - Position WebSocket sends snapshot on connect (contains all open positions)
  - Added restoration logic in `on_position_update()`:
    - Detects when position exists on exchange but not tracked locally (`size > 0` and `local_qty == 0`)
    - Restores position from WebSocket data (avgPrice, quantity)
    - Creates TP order automatically
    - Logs as "RESTORE" action to metrics
  - State file auto-updates via `pm.add_position()`
- **Files Modified:**
  - `src/strategy/grid_strategy.py`:
    - Removed `get_active_position()` call from `sync_with_exchange()` (line ~496)
    - Added position restoration in `on_position_update()` (lines 1148-1198)
    - Updated `sync_with_exchange()` docstring to document new flow (lines 310-328)
- **Impact:**
  - Eliminated ~120 REST API calls per hour
  - Position WebSocket is now source of truth (not state file)
  - Simpler, more reliable restoration logic

#### Removed Order History Restoration Method
- **Problem:** Complex 132-line method using `get_order_history()` REST API to reconstruct positions
- **Solution:**
  - Deleted entire `_restore_positions_from_order_history()` method
  - Position WebSocket snapshot provides accurate position data
  - No need to reconstruct from order history
- **Files Modified:**
  - `src/strategy/grid_strategy.py`: Deleted lines 310-441 (132 lines removed)
- **Impact:**
  - Eliminated `get_order_history()` REST API calls
  - Reduced code complexity by 132 lines
  - More reliable restoration (exchange avgPrice > reconstructed price)

### Architecture Changes

**REST API Usage After Optimization:**

**Monitoring Calls (eliminated completely):**
- ❌ ~~get_wallet_balance()~~ → Replaced by Wallet WebSocket updates
- ❌ ~~get_active_position()~~ → Replaced by Position WebSocket snapshot
- ❌ ~~get_order_history()~~ → Replaced by Position WebSocket snapshot

**Command Calls (still needed):**
- ✅ `place_order()` - Place grid orders
- ✅ `cancel_order()` - Cancel TP orders
- ✅ `close_position()` - Close positions
- ✅ `set_leverage()` - Set leverage at startup
- ✅ `set_position_mode()` - Set hedge mode at startup
- ✅ `get_closed_pnl()` - Fetch real PnL after position close
- ✅ `get_instrument_info()` - Fetch trading rules at startup
- ✅ `get_wallet_balance()` - Initial balance fetch (once at startup with force_refresh=True)

**Total REST API Calls Eliminated:** ~125+ calls/hour (balance + position polling)

**New Architecture:** WebSocket-First - ALL monitoring data via WebSocket streams, REST API only for commands

### Documentation Updates

#### CLAUDE.md
- Updated Exchange Layer section to describe WebSocket-First Architecture (lines 23-29)
- Updated Utilities Layer to describe WebSocket-first balance_manager (lines 35-39)
- Updated Strategy Logic Flow with detailed WebSocket stream breakdown (lines 48-79):
  - Price Updates (Public WebSocket)
  - Position Updates (Private WebSocket)
  - Wallet Updates (Private WebSocket)
  - Order Updates (Private WebSocket)
  - Periodic Sync details
- Updated State Persistence and Sync section (lines 461-490):
  - Documented Position WebSocket restoration flow
  - Clarified that Position WebSocket > State File
  - Removed references to old REST API polling approach

#### CODE_QUALITY_IMPROVEMENTS.md
- Added Phase 1.5: REST API Optimization section (lines 550-637)
- Documented all three optimization improvements with details
- Listed REST API usage before/after optimization
- Clarified WebSocket-First architecture approach

### Testing
- All 172 tests passing ✅
- No breaking changes to existing functionality
- Position restoration tested and verified

### Key Design Decisions

1. **Position WebSocket as Source of Truth:**
   - State file can be outdated after crashes/kills
   - Always trust WebSocket snapshot for position restoration
   - State file serves as backup only, auto-updates via `pm.add_position()`

2. **Grid Level Loss Tradeoff:**
   - Restored positions lose grid level detail (all marked as grid_level=0)
   - However, exchange avgPrice is accurate and that's what matters for TP calculation
   - This is acceptable tradeoff for simpler, more reliable restoration

3. **Shared Balance Manager:**
   - One balance_manager instance per account
   - All strategies share same instance to avoid duplicate calls
   - Wallet WebSocket keeps it updated in real-time

## [1.0.0] - 2025-01-12

### Added - Phase 1: Critical Thread Safety Fixes

#### WebSocket Thread Safety
- Added `threading.Lock()` to all shared resources accessed by WebSocket callbacks:
  - `BalanceManager._cached_balance` and `._cached_mm_rate`
  - `GridStrategy._tp_orders` dict
  - `GridStrategy._last_cum_realised_pnl` dict
  - `PositionManager.long_positions` and `.short_positions` lists

#### WebSocket Reconnect Mechanism
- Implemented automatic reconnection with exponential backoff
- Added connection state tracking (`is_connected()` method)
- Heartbeat monitoring for silent disconnect detection
- Max 10 reconnect attempts with 1s to 60s delay

#### WebSocket Connection Management
- Proper connection closing in `stop()` method using `ws.exit()`
- Clean shutdown of heartbeat monitor thread
- Graceful handling of connection errors

### Files Modified (Phase 1)
- `src/utils/balance_manager.py`: Thread safety with locks
- `src/strategy/grid_strategy.py`: Thread safety for dicts
- `src/strategy/position_manager.py`: Thread safety for lists
- `src/exchange/bybit_websocket.py`: Reconnect + heartbeat monitoring

### Testing
- All 172 tests passing
- Unit tests with concurrent access
- Integration tests with real WebSocket callbacks

---

## Release Notes

### v1.5.0 Summary
This release significantly reduces REST API usage by implementing a WebSocket-First architecture. All monitoring data now comes from WebSocket streams in real-time, with REST API used only for commands (order execution, leverage setting, etc.). This improves reliability, reduces latency, and eliminates ~125+ API calls per hour.

**Key Improvements:**
- 🚀 ~125+ REST API calls eliminated per hour
- 📡 Position WebSocket is now source of truth for position restoration
- 🔄 Simpler, more reliable position restoration on bot restart
- 📊 Shared balance manager eliminates duplicate calls
- 🧹 132 lines of complex restoration code removed

**Breaking Changes:** None - all changes are backward compatible

**Migration Notes:** No action required - bot automatically uses new WebSocket-first architecture
