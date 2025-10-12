# Code Quality Improvements Plan

## Overview

This document outlines critical improvements needed to ensure thread safety, reliability, and robustness of the SOL-Trader multi-account bot. The plan focuses on WebSocket-related issues, thread synchronization, and fault tolerance.

**Created:** 2025-10-12
**Status:** Phase 1 in progress
**Priority:** Critical fixes first, then medium, then low priority

---

## Phase 1: Critical Issues (Must Fix Immediately)

### 1. Race Conditions in WebSocket Callbacks ⚠️ CRITICAL

**Problem:**
pybit WebSocket runs callbacks in separate threads. Multiple shared resources are accessed without locks:

- `BalanceManager._cached_balance` and `_cached_mm_rate`
- `GridStrategy._tp_orders` dict
- `GridStrategy._last_cum_realised_pnl` dict
- `PositionManager.long_positions` and `short_positions` lists

**Risk:**
Data corruption, inconsistent state, crashes (especially list IndexError on concurrent modifications).

**Solution:**
Add `threading.Lock()` to all shared resources:

```python
# BalanceManager
class BalanceManager:
    def __init__(self, ...):
        self._lock = threading.Lock()

    def update_from_websocket(self, balance, mm_rate):
        with self._lock:
            self._cached_balance = balance
            self._cached_mm_rate = mm_rate

    def get_available_balance(self):
        with self._lock:
            return self._cached_balance
```

**Affected Files:**
- `src/utils/balance_manager.py`
- `src/strategy/grid_strategy.py` (_tp_orders, _last_cum_realised_pnl)
- `src/strategy/position_manager.py` (long_positions, short_positions)

**Estimated Time:** 5 hours total
- BalanceManager: 1 hour
- GridStrategy: 2 hours (two dicts)
- PositionManager: 2 hours (lists + add/remove operations)

**Testing Required:**
- Unit tests with concurrent access
- Integration tests with real WebSocket callbacks
- Load test with rapid price updates

---

### 2. No WebSocket Reconnect Mechanism ⚠️ CRITICAL

**Problem:**
If WebSocket connection drops, bot continues running with stale data. No automatic reconnection.

**Current Code:**
```python
# bybit_websocket.py - no reconnect logic!
def _on_message(self, ws, message):
    # Just processes messages, no connection monitoring
```

**Risk:**
Bot makes trading decisions on stale prices, missing critical updates.

**Solution:**
Implement reconnect logic with exponential backoff:

```python
class BybitWebSocket:
    def __init__(self, ...):
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 10
        self._reconnect_delay = 1.0  # seconds
        self._is_connected = False

    def _on_close(self, ws, close_status_code, close_msg):
        self.logger.warning(f"WebSocket closed: {close_msg}")
        self._is_connected = False
        self._attempt_reconnect()

    def _attempt_reconnect(self):
        if self._reconnect_attempts >= self._max_reconnect_attempts:
            self.logger.error("Max reconnect attempts reached, giving up")
            # Trigger emergency stop?
            return

        delay = min(self._reconnect_delay * (2 ** self._reconnect_attempts), 60)
        self.logger.info(f"Reconnecting in {delay}s (attempt {self._reconnect_attempts + 1})")
        time.sleep(delay)
        self._reconnect_attempts += 1
        self.start()

    def _on_open(self, ws):
        self.logger.info("WebSocket connected")
        self._is_connected = True
        self._reconnect_attempts = 0  # Reset on success
```

**Additional Features:**
- Heartbeat monitoring (detect silent disconnects)
- Connection state tracking (`is_connected()` method)
- Emergency stop trigger if reconnect fails completely

**Affected Files:**
- `src/exchange/bybit_websocket.py`

**Estimated Time:** 4-6 hours
- Reconnect logic: 2 hours
- Heartbeat monitoring: 2 hours
- Testing: 2 hours

---

### 3. Improper WebSocket Connection Closing ⚠️ CRITICAL

**Problem:**
`stop()` method just sets `self.ws = None` without explicitly closing the connection.

**Current Code:**
```python
def stop(self):
    """Stop WebSocket connection"""
    if self.ws:
        self.logger.info(f"Stopping WebSocket for {self.symbol}")
        self.ws = None  # ❌ No explicit close!
```

**Risk:**
Connection leaks, resources not freed, potential zombie connections.

**Solution:**
```python
def stop(self):
    """Stop WebSocket connection"""
    if self.ws:
        self.logger.info(f"Stopping WebSocket for {self.symbol}")
        try:
            self.ws.close()  # ✅ Explicit close
        except Exception as e:
            self.logger.error(f"Error closing WebSocket: {e}")
        finally:
            self.ws = None
```

**Affected Files:**
- `src/exchange/bybit_websocket.py`

**Estimated Time:** 30 minutes

---

## Phase 2: Medium Priority Issues

### 4. Blocking REST API Calls in WebSocket Callbacks

**Problem:**
`sync_with_exchange()` is called from WebSocket callback thread, making blocking REST API calls.

**Risk:**
WebSocket message processing blocked, messages queued, delayed updates.

**Solution:**
Use async queue to offload heavy operations:

```python
import queue
import threading

class TradingAccount:
    def __init__(self, ...):
        self._callback_queue = queue.Queue()
        self._callback_thread = threading.Thread(target=self._process_callbacks, daemon=True)
        self._callback_thread.start()

    def on_price_update(self, symbol, price):
        # Quick enqueue, return immediately
        self._callback_queue.put(('price', symbol, price))

    def _process_callbacks(self):
        while True:
            try:
                event = self._callback_queue.get(timeout=1.0)
                event_type, symbol, price = event

                if event_type == 'price':
                    # Process in dedicated thread, WebSocket thread free
                    strategy = self.strategies[symbol]
                    strategy.on_price_update(price)
            except queue.Empty:
                continue
```

**Affected Files:**
- `src/core/trading_account.py`

**Estimated Time:** 3-4 hours

---

### 5. No Timeouts for REST API Calls

**Problem:**
REST API calls can hang indefinitely if Bybit API is slow.

**Solution:**
```python
# In bybit_client.py
def __init__(self, ...):
    self.session = HTTP(
        api_key=api_key,
        api_secret=api_secret,
        testnet=demo,
        timeout=10.0  # Add timeout
    )
```

**Affected Files:**
- `src/exchange/bybit_client.py`

**Estimated Time:** 1 hour

---

### 6. Initialization Can Hang Waiting for WebSocket

**Problem:**
Startup waits for first WebSocket price indefinitely.

**Solution:**
```python
# Add timeout and fallback
async def initialize_symbol(self, symbol, ...):
    self._initial_sync_done[symbol] = False
    self.logger.info(f"⏳ [{symbol}] Waiting for first price from WebSocket...")

    # Wait max 30 seconds
    start_time = time.time()
    while not self._initial_sync_done.get(symbol, False):
        if time.time() - start_time > 30:
            self.logger.warning(f"⚠️ [{symbol}] WebSocket timeout, using REST API fallback")
            price_data = self.client.get_ticker(category="linear", symbol=symbol)
            price = float(price_data['result']['list'][0]['lastPrice'])
            strategy.sync_with_exchange(price)
            self._initial_sync_done[symbol] = True
            break
        await asyncio.sleep(0.1)
```

**Affected Files:**
- `src/core/trading_account.py`

**Estimated Time:** 2 hours

---

### 7. No Rate Limiting for REST API Calls

**Problem:**
Rapid REST API calls can hit Bybit rate limits (120 req/min).

**Solution:**
Implement token bucket rate limiter:

```python
import time
from threading import Lock

class RateLimiter:
    def __init__(self, max_requests_per_minute=100):
        self.max_requests = max_requests_per_minute
        self.tokens = max_requests_per_minute
        self.last_refill = time.time()
        self.lock = Lock()

    def acquire(self):
        with self.lock:
            now = time.time()
            # Refill tokens
            elapsed = now - self.last_refill
            self.tokens = min(self.max_requests, self.tokens + elapsed * (self.max_requests / 60))
            self.last_refill = now

            if self.tokens < 1:
                sleep_time = (1 - self.tokens) * (60 / self.max_requests)
                time.sleep(sleep_time)
                self.tokens = 0
            else:
                self.tokens -= 1

# In bybit_client.py
class BybitClient:
    def __init__(self, ...):
        self.rate_limiter = RateLimiter(max_requests_per_minute=100)

    def place_order(self, ...):
        self.rate_limiter.acquire()
        # ... actual API call
```

**Affected Files:**
- `src/exchange/bybit_client.py` (add RateLimiter class and use in all methods)

**Estimated Time:** 3 hours

---

## Phase 3: Low Priority Improvements

### 8. Config Validation

**Problem:**
Invalid config values (negative leverage, etc.) not caught until runtime.

**Solution:**
```python
def validate_config(config):
    for account in config['accounts']:
        for strategy in account['strategies']:
            assert strategy['leverage'] > 0, "Leverage must be positive"
            assert 0 < strategy['grid_step_percent'] < 100, "Grid step must be 0-100%"
            # ... more validations
```

**Estimated Time:** 2 hours

---

### 9. Insufficient Test Coverage for WebSocket

**Current Coverage:**
- Unit tests: Good (position_manager, grid_strategy)
- Integration tests: Basic
- **Missing:** WebSocket reconnect, thread safety, race conditions

**Solution:**
Add tests for:
- Concurrent WebSocket callbacks
- Reconnect logic
- Thread safety with locks
- Connection failures

**Estimated Time:** 4-6 hours

---

### 10. No Health Check / Monitoring

**Problem:**
No way to programmatically check if bot is healthy (WebSocket connected, positions synced, etc.).

**Solution:**
```python
class TradingAccount:
    def get_health_status(self):
        return {
            'ws_connected': self.public_ws.is_connected() if self.public_ws else False,
            'private_ws_connected': self.private_ws.is_connected() if self.private_ws else False,
            'last_price_update': time.time() - self._last_price_update_time,
            'emergency_stopped': any(s.emergency_stopped for s in self.strategies.values()),
            'positions_synced': all(self._initial_sync_done.values())
        }
```

**Estimated Time:** 2 hours

---

### 11. Add Type Hints Everywhere

**Problem:**
Many methods lack type hints, making code harder to understand and maintain.

**Solution:**
Add type hints to all methods:

```python
from typing import Dict, List, Optional, Tuple

def add_position(
    self,
    side: str,
    entry_price: float,
    quantity: float,
    grid_level: int
) -> Dict[str, Any]:
    # ...
```

**Estimated Time:** 3-4 hours

---

### 12. Improve Logging with Structured Data

**Problem:**
Logs are string-based, hard to parse programmatically.

**Solution:**
Use structured logging (JSON format):

```python
import logging
import json

class StructuredLogger:
    def log_event(self, event_type, **kwargs):
        log_data = {
            'timestamp': time.time(),
            'event_type': event_type,
            **kwargs
        }
        self.logger.info(json.dumps(log_data))

# Usage
logger.log_event('position_opened', symbol='DOGEUSDT', side='LONG', price=0.123)
```

**Estimated Time:** 2-3 hours

---

### 13. Better Error Isolation Between Accounts

**Problem:**
One account's error can potentially affect others (shared WebSocket).

**Solution:**
Wrap all per-account operations in try/except:

```python
async def run_account(self, account):
    try:
        await account.initialize()
        # ... run account
    except Exception as e:
        self.logger.error(f"Account {account.id_str} failed: {e}")
        # Continue with other accounts
```

**Estimated Time:** 1-2 hours

---

## Implementation Timeline

**Phase 1 (Critical - Week 1):**
- Day 1-2: Race condition fixes (threading.Lock)
- Day 3-4: WebSocket reconnect mechanism
- Day 5: WebSocket.stop() fix + testing
- **Total:** ~15 hours

**Phase 2 (Medium - Week 2):**
- Day 1: Async callback queue
- Day 2: REST API timeouts + startup timeout
- Day 3: Rate limiting
- **Total:** ~10 hours

**Phase 3 (Low Priority - Week 3):**
- Day 1: Config validation + health monitoring
- Day 2: Additional tests
- Day 3: Type hints + structured logging
- **Total:** ~15 hours

**Grand Total:** ~40 hours over 3 weeks

---

## Testing Strategy

**For Each Fix:**
1. Write unit test first (TDD approach)
2. Implement fix
3. Run unit tests
4. Run integration tests
5. Manual testing in dry_run mode
6. Manual testing in demo mode
7. Monitor for 24 hours before next fix

**Rollback Plan:**
- Git commit after each working fix
- Keep detailed changelog
- If issue found, revert specific commit

---

## Success Criteria

**Phase 1 Complete When:**
- [ ] All shared resources protected with locks
- [ ] WebSocket reconnects automatically on disconnect
- [ ] WebSocket connections close properly
- [ ] All 172+ tests pass
- [ ] No race condition warnings in logs
- [ ] Bot runs 24 hours without crashes

**Phase 2 Complete When:**
- [ ] WebSocket callbacks don't block on REST calls
- [ ] REST API calls have 10s timeout
- [ ] Startup has 30s timeout with fallback
- [ ] Rate limiting prevents API errors
- [ ] All tests pass

**Phase 3 Complete When:**
- [ ] Config validation catches errors at startup
- [ ] Test coverage > 90%
- [ ] Health check endpoint working
- [ ] Type hints on all public methods
- [ ] Structured logging in place
- [ ] Error in one account doesn't crash others

---

## Notes

- **Backward Compatibility:** All changes maintain existing functionality
- **No API Changes:** External config format unchanged
- **Safe Rollback:** Each phase can be reverted independently
- **Documentation:** Update CLAUDE.md after each phase

---

## Current Status

**Phase 1 Complete:** All critical thread safety fixes implemented ✅
- BalanceManager thread safety ✅
- GridStrategy thread safety ✅
- PositionManager thread safety ✅
- WebSocket.stop() proper connection closing ✅
- WebSocket reconnect mechanism with exponential backoff ✅
- Heartbeat monitoring for silent disconnect detection ✅

**All 172 tests passing** ✅

---

## Phase 1.5: REST API Optimization (Completed 2025-01-15)

**Goal:** Minimize REST API usage by leveraging WebSocket streams for all monitoring data.

### Completed Optimizations:

#### 1. Eliminated Duplicate Balance Calls ✅
**Problem:** Multiple REST API calls to get_wallet_balance() across different strategy components.

**Solution:**
- Created shared `BalanceManager` instance per account
- All strategies share one balance_manager
- Wallet WebSocket updates cache in real-time
- Force refresh only on initialization

**Files Modified:**
- `src/core/trading_account.py`: Create shared balance_manager, pass to all strategies
- `src/strategy/grid_strategy.py`: Accept optional balance_manager parameter

**Result:** Eliminated 1+ REST API call per strategy at startup.

---

#### 2. Position Restoration via WebSocket ✅
**Problem:** Using get_active_position() REST API in sync_with_exchange() to check for positions (~120 calls/hour).

**Solution:**
- Position WebSocket sends snapshot on connect (contains all open positions)
- Added restoration logic in `on_position_update()`:
  - Detects when position exists on exchange but not tracked locally
  - Restores position from WebSocket data (avgPrice, size)
  - Creates TP order automatically
  - Logs as "RESTORE" action
- State file auto-updates via pm.add_position()

**Files Modified:**
- `src/strategy/grid_strategy.py`:
  - Removed get_active_position() call from sync_with_exchange() (line ~496)
  - Added position restoration in on_position_update() (lines 1148-1198)
  - Updated sync_with_exchange() docstring to document new flow

**Result:**
- Eliminated ~120 REST API calls per hour
- Position WebSocket is now source of truth (not state file)
- Simpler, more reliable restoration logic

---

#### 3. Removed Order History Restoration ✅
**Problem:** Complex 132-line method using get_order_history() REST API to reconstruct positions.

**Solution:**
- Deleted entire `_restore_positions_from_order_history()` method
- Position WebSocket snapshot provides accurate position data
- No need to reconstruct from order history

**Files Modified:**
- `src/strategy/grid_strategy.py`: Deleted lines 310-441 (132 lines)

**Result:**
- Eliminated get_order_history() REST API calls
- Reduced code complexity
- More reliable restoration (exchange avgPrice > reconstructed price)

---

### REST API Usage After Optimization:

**Monitoring Calls (eliminated completely):**
- ❌ ~~get_wallet_balance()~~ → Wallet WebSocket
- ❌ ~~get_active_position()~~ → Position WebSocket snapshot
- ❌ ~~get_order_history()~~ → Position WebSocket snapshot

**Command Calls (still needed):**
- ✅ place_order() - Place grid orders
- ✅ cancel_order() - Cancel TP orders
- ✅ close_position() - Close positions
- ✅ set_leverage() - Set leverage
- ✅ set_position_mode() - Set hedge mode
- ✅ get_closed_pnl() - Fetch real PnL after position close
- ✅ get_instrument_info() - Fetch trading rules at startup
- ✅ get_wallet_balance() - Initial balance fetch (once at startup with force_refresh=True)

**Total REST API Calls Eliminated:** ~125+ calls/hour (balance + position polling)

**Architecture:** WebSocket-First - ALL monitoring via WebSocket, REST API only for commands

---

## Phase 2: Medium Priority Issues (Pending)

### 4. Blocking REST API Calls in WebSocket Callbacks
