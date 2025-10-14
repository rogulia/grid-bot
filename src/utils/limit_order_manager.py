"""Limit Order Manager for handling limit orders with timeout and retry logic"""

import time
import threading
import logging
from typing import Optional, Dict, Callable, TYPE_CHECKING
from config.constants import TradingConstants

if TYPE_CHECKING:
    from ..exchange.bybit_client import BybitClient


class LimitOrderManager:
    """
    Manages limit orders with automatic retry and fallback to market orders
    
    Features:
    - Places limit orders with configurable price offset
    - Tracks order status via WebSocket
    - Automatically cancels and retries after timeout
    - Falls back to market orders after max retries
    """

    def __init__(
        self,
        client: 'BybitClient',
        symbol: str,
        category: str = "linear",
        logger: Optional[logging.Logger] = None,
        dry_run: bool = False
    ):
        """
        Initialize Limit Order Manager
        
        Args:
            client: Bybit API client
            symbol: Trading symbol
            category: Market category
            logger: Logger instance
            dry_run: Dry run mode (no real orders)
        """
        self.client = client
        self.symbol = symbol
        self.category = category
        self.dry_run = dry_run
        self.logger = logger or logging.getLogger("sol-trader.limit_order_manager")
        
        # Tracked orders: {order_id: order_info}
        self._tracked_orders: Dict[str, Dict] = {}
        self._lock = threading.Lock()
        
        # Timers for timeout checking
        self._timers: Dict[str, threading.Timer] = {}
        
        # Callbacks for order completion
        self._on_filled_callback: Optional[Callable] = None
        self._on_failed_callback: Optional[Callable] = None

    def set_callbacks(
        self,
        on_filled: Optional[Callable] = None,
        on_failed: Optional[Callable] = None
    ):
        """
        Set callbacks for order events
        
        Args:
            on_filled: Called when order is filled (order_id, order_info)
            on_failed: Called when order fails after retries (order_id, order_info, reason)
        """
        self._on_filled_callback = on_filled
        self._on_failed_callback = on_failed

    def calculate_limit_price(
        self,
        side: str,
        current_price: float,
        offset_percent: Optional[float] = None
    ) -> float:
        """
        Calculate limit order price with offset for better fill rate
        
        Args:
            side: 'Buy' or 'Sell'
            current_price: Current market price
            offset_percent: Price offset in percent (default: from constants)
            
        Returns:
            Limit price adjusted for side and offset
        """
        if offset_percent is None:
            offset_percent = TradingConstants.LIMIT_ORDER_PRICE_OFFSET_PERCENT
        
        # Buy: slightly above market (more likely to fill as maker)
        # Sell: slightly below market (more likely to fill as maker)
        if side == 'Buy':
            limit_price = current_price * (1 + offset_percent / 100)
        else:  # Sell
            limit_price = current_price * (1 - offset_percent / 100)
        
        return limit_price

    def place_limit_order(
        self,
        side: str,
        qty: float,
        current_price: float,
        reason: str,
        position_idx: Optional[int] = None,
        reduce_only: bool = False,
        retry_count: int = 0
    ) -> Optional[str]:
        """
        Place a limit order with tracking
        
        Args:
            side: 'Buy' or 'Sell'
            qty: Order quantity
            current_price: Current market price
            reason: Reason for order (for logging)
            position_idx: Position index for hedge mode
            reduce_only: If True, order can only reduce position
            retry_count: Current retry count (internal use)
            
        Returns:
            Order ID if successful, None if failed
        """
        if self.dry_run:
            self.logger.info(
                f"[{self.symbol}] [DRY RUN] Would place limit order: {side} {qty} "
                f"@ ~${current_price:.4f} (reason: {reason})"
            )
            return f"DRY_RUN_LIMIT_{side}_{int(time.time())}"
        
        try:
            # Calculate limit price with offset
            limit_price = self.calculate_limit_price(side, current_price)
            
            # Place order via client
            response = self.client.place_order(
                symbol=self.symbol,
                side=side,
                qty=qty,
                order_type="Limit",
                price=limit_price,
                category=self.category,
                position_idx=position_idx,
                reduce_only=reduce_only
            )
            
            if not response or response.get('retCode') != 0:
                self.logger.error(
                    f"[{self.symbol}] Failed to place limit order: {response}"
                )
                return None
            
            # Extract order ID
            order_id = response.get('result', {}).get('orderId')
            if not order_id:
                self.logger.error(
                    f"[{self.symbol}] No order ID in response: {response}"
                )
                return None
            
            # Track order
            order_info = {
                'order_id': order_id,
                'side': side,
                'qty': qty,
                'limit_price': limit_price,
                'current_price': current_price,
                'reason': reason,
                'position_idx': position_idx,
                'reduce_only': reduce_only,
                'retry_count': retry_count,
                'placed_at': time.time(),
                'status': 'New'
            }
            
            with self._lock:
                self._tracked_orders[order_id] = order_info
            
            self.logger.info(
                f"[{self.symbol}] ✅ Limit order placed: {side} {qty} @ ${limit_price:.4f} "
                f"(market: ${current_price:.4f}, offset: {TradingConstants.LIMIT_ORDER_PRICE_OFFSET_PERCENT}%, "
                f"reason: {reason}, retry: {retry_count}/{TradingConstants.LIMIT_ORDER_MAX_RETRIES}, ID: {order_id})"
            )
            
            # Start timeout timer
            self.logger.info(f"[{self.symbol}] 🔔 Starting {TradingConstants.LIMIT_ORDER_TIMEOUT_SEC}s timeout timer for order {order_id}")
            self._start_timeout_timer(order_id)
            
            return order_id
            
        except Exception as e:
            self.logger.error(
                f"[{self.symbol}] Exception placing limit order: {e}",
                exc_info=True
            )
            return None

    def _start_timeout_timer(self, order_id: str):
        """
        Start timeout timer for an order
        
        Args:
            order_id: Order ID to track
        """
        def on_timeout():
            self.logger.info(f"[{self.symbol}] ⏱️  Timer callback fired for order {order_id}")
            self._handle_timeout(order_id)
        
        timer = threading.Timer(
            TradingConstants.LIMIT_ORDER_TIMEOUT_SEC,
            on_timeout
        )
        timer.daemon = True
        timer.start()
        self.logger.info(f"[{self.symbol}] ⏲️  Timer.start() called for order {order_id}, will fire in {TradingConstants.LIMIT_ORDER_TIMEOUT_SEC}s")
        
        with self._lock:
            self._timers[order_id] = timer
            self.logger.info(f"[{self.symbol}] 📝 Timer stored in self._timers for order {order_id}")

    def _handle_timeout(self, order_id: str):
        """
        Handle order timeout - cancel and retry or fallback to market
        
        Args:
            order_id: Order ID that timed out
        """
        self.logger.info(f"[{self.symbol}] 🕐 _handle_timeout CALLED for order {order_id}")
        
        with self._lock:
            order_info = self._tracked_orders.get(order_id)
            if not order_info:
                self.logger.warning(f"[{self.symbol}] ⚠️  Order {order_id} not in tracked orders (already processed?)")
                return  # Already processed
            
            # Check if order is still pending
            self.logger.info(f"[{self.symbol}] 🔍 Order {order_id} status: {order_info['status']}")
            if order_info['status'] != 'New':
                self.logger.info(f"[{self.symbol}] ⏭️  Order {order_id} status is {order_info['status']}, skipping timeout handling")
                return  # Already filled or cancelled
        
        self.logger.warning(
            f"[{self.symbol}] ⏰ Limit order timeout: {order_info['side']} {order_info['qty']} "
            f"@ ${order_info['limit_price']:.4f} (ID: {order_id}, "
            f"retry: {order_info['retry_count']}/{TradingConstants.LIMIT_ORDER_MAX_RETRIES})"
        )
        
        # Cancel the order
        try:
            if not self.dry_run:
                self.client.cancel_order(self.symbol, order_id, self.category)
                self.logger.info(f"[{self.symbol}] 🗑️  Cancelled timed out order: {order_id}")
        except Exception as e:
            self.logger.warning(
                f"[{self.symbol}] Failed to cancel order {order_id}: {e}"
            )
        
        # Check if we should retry or fallback
        retry_count = order_info['retry_count']
        if retry_count < TradingConstants.LIMIT_ORDER_MAX_RETRIES - 1:
            # Retry with limit order
            self.logger.info(
                f"[{self.symbol}] 🔄 Retrying limit order (attempt {retry_count + 2}/{TradingConstants.LIMIT_ORDER_MAX_RETRIES})"
            )
            
            # Get fresh price (from order info, caller should update if needed)
            current_price = order_info['current_price']
            
            # Place new limit order with incremented retry count
            new_order_id = self.place_limit_order(
                side=order_info['side'],
                qty=order_info['qty'],
                current_price=current_price,
                reason=order_info['reason'],
                position_idx=order_info['position_idx'],
                reduce_only=order_info['reduce_only'],
                retry_count=retry_count + 1
            )
            
            if new_order_id:
                self.logger.info(
                    f"[{self.symbol}] ✅ Retry limit order placed: {new_order_id}"
                )
        else:
            # Fallback to market order
            self.logger.warning(
                f"[{self.symbol}] 🚨 Max retries reached, falling back to MARKET order"
            )
            
            self._fallback_to_market(order_info)
        
        # Remove from tracking
        with self._lock:
            if order_id in self._tracked_orders:
                del self._tracked_orders[order_id]
            if order_id in self._timers:
                del self._timers[order_id]

    def _fallback_to_market(self, order_info: Dict):
        """
        Fallback to market order after limit order retries exhausted
        
        Args:
            order_info: Original order information
        """
        try:
            if self.dry_run:
                self.logger.info(
                    f"[{self.symbol}] [DRY RUN] Would fallback to market: "
                    f"{order_info['side']} {order_info['qty']}"
                )
                return
            
            response = self.client.place_order(
                symbol=self.symbol,
                side=order_info['side'],
                qty=order_info['qty'],
                order_type="Market",
                category=self.category,
                position_idx=order_info['position_idx'],
                reduce_only=order_info['reduce_only']
            )
            
            if response and response.get('retCode') == 0:
                fallback_order_id = response.get('result', {}).get('orderId')
                self.logger.info(
                    f"[{self.symbol}] ✅ Fallback market order placed: {order_info['side']} "
                    f"{order_info['qty']} (ID: {fallback_order_id})"
                )
                
                # Call failed callback (market order means limit strategy failed)
                if self._on_failed_callback:
                    self._on_failed_callback(
                        order_info['order_id'],
                        order_info,
                        "timeout_fallback_to_market"
                    )
            else:
                self.logger.error(
                    f"[{self.symbol}] ❌ Fallback market order failed: {response}"
                )
                
                if self._on_failed_callback:
                    self._on_failed_callback(
                        order_info['order_id'],
                        order_info,
                        "fallback_failed"
                    )
                    
        except Exception as e:
            self.logger.error(
                f"[{self.symbol}] Exception in fallback to market: {e}",
                exc_info=True
            )
            
            if self._on_failed_callback:
                self._on_failed_callback(
                    order_info['order_id'],
                    order_info,
                    f"fallback_exception: {e}"
                )

    def on_order_update(self, order_data: Dict):
        """
        Handle order update from WebSocket
        
        Should be called by GridStrategy.on_order_update() for tracked orders
        
        Args:
            order_data: Order data from Bybit WebSocket
        """
        order_id = order_data.get('orderId')
        order_status = order_data.get('orderStatus')
        
        self.logger.debug(f"[{self.symbol}] 📨 LimitOrderManager.on_order_update: order={order_id}, status={order_status}")
        
        with self._lock:
            if order_id not in self._tracked_orders:
                self.logger.debug(f"[{self.symbol}] ⏩ Order {order_id} not in tracked orders, skipping")
                return  # Not our order
            
            order_info = self._tracked_orders[order_id]
            self.logger.info(f"[{self.symbol}] 🎯 Processing tracked order {order_id}, status: {order_status}")
        
        # Update status
        order_info['status'] = order_status
        
        if order_status == 'Filled':
            # Order filled successfully!
            self.logger.info(
                f"[{self.symbol}] ✅ Limit order FILLED: {order_info['side']} "
                f"{order_info['qty']} @ ${order_info['limit_price']:.4f} (ID: {order_id})"
            )
            
            # Cancel timeout timer
            with self._lock:
                if order_id in self._timers:
                    self._timers[order_id].cancel()
                    del self._timers[order_id]
                
                # Remove from tracking
                if order_id in self._tracked_orders:
                    del self._tracked_orders[order_id]
            
            # Call filled callback
            if self._on_filled_callback:
                self._on_filled_callback(order_id, order_info)
        
        elif order_status == 'PartiallyFilled':
            # Partially filled - keep waiting
            self.logger.info(
                f"[{self.symbol}] 🔄 Limit order partially filled: {order_id}"
            )
        
        elif order_status == 'Cancelled':
            # Order cancelled (might be by us due to timeout)
            self.logger.debug(
                f"[{self.symbol}] Order cancelled: {order_id}"
            )
            
            # Cancel timer if exists
            with self._lock:
                if order_id in self._timers:
                    self._timers[order_id].cancel()
                    del self._timers[order_id]

    def update_current_price(self, order_id: str, new_price: float):
        """
        Update current price for tracked order (used for retry logic)
        
        Args:
            order_id: Order ID to update
            new_price: New current market price
        """
        with self._lock:
            if order_id in self._tracked_orders:
                self._tracked_orders[order_id]['current_price'] = new_price

    def get_tracked_order(self, order_id: str) -> Optional[Dict]:
        """
        Get tracked order info
        
        Args:
            order_id: Order ID
            
        Returns:
            Order info dict or None
        """
        with self._lock:
            return self._tracked_orders.get(order_id)

    def cleanup(self):
        """
        Cleanup all timers and tracked orders (call on shutdown)
        """
        with self._lock:
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()
            self._tracked_orders.clear()
        
        self.logger.info(f"[{self.symbol}] LimitOrderManager cleaned up")

