"""Metrics tracker for performance analytics"""

import csv
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
from src.utils.timezone import now_helsinki, format_helsinki


@dataclass
class TradeMetric:
    """Single trade metric"""
    timestamp: str
    symbol: str  # Trading symbol (e.g., SOLUSDT)
    side: str  # 'Buy' or 'Sell'
    action: str  # 'OPEN' or 'CLOSE'
    price: float
    quantity: float
    reason: str
    pnl: Optional[float] = None
    open_fee: float = 0.0  # Fee paid to open position
    close_fee: float = 0.0  # Fee paid to close position
    funding_fee: float = 0.0  # Accumulated funding fees


@dataclass
class PerformanceSnapshot:
    """Performance snapshot at a point in time"""
    timestamp: str
    symbol: str  # Trading symbol (e.g., SOLUSDT)
    price: float
    long_positions: int
    short_positions: int
    long_qty: float
    short_qty: float
    long_pnl: float
    short_pnl: float
    total_pnl: float
    total_trades: int
    balance: float


class MetricsTracker:
    """Track and analyze bot performance metrics"""

    def __init__(self, initial_balance: float = 1000.0, file_prefix: str = ""):
        """
        Initialize metrics tracker

        Args:
            initial_balance: Starting balance in USDT
            file_prefix: Prefix for CSV files (e.g., "test_" for backtesting)
        """
        self.logger = logging.getLogger("sol-trader.metrics")
        self.initial_balance = initial_balance
        self.current_balance = initial_balance
        self.start_time = now_helsinki()
        self.file_prefix = file_prefix

        # Metrics storage
        self.trades: List[TradeMetric] = []
        self.snapshots: List[PerformanceSnapshot] = []

        # Statistics
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.total_pnl = 0.0
        self.realized_pnl = 0.0
        self.max_drawdown = 0.0
        self.peak_balance = initial_balance

        # Create data directory
        self.data_dir = Path("data")
        self.data_dir.mkdir(exist_ok=True)

        # Initialize CSV files
        self._init_csv_files()

        prefix_info = f" (prefix: {file_prefix})" if file_prefix else ""
        self.logger.info(f"MetricsTracker initialized with balance: ${initial_balance:.2f}{prefix_info}")

    def _init_csv_files(self):
        """Initialize CSV files with headers"""
        # Performance metrics CSV (with prefix for backtesting)
        metrics_filename = f"{self.file_prefix}performance_metrics.csv"
        self.metrics_file = self.data_dir / metrics_filename
        if not self.metrics_file.exists():
            with open(self.metrics_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'timestamp', 'symbol', 'price', 'long_positions', 'short_positions',
                    'long_qty', 'short_qty', 'long_pnl', 'short_pnl',
                    'total_pnl', 'total_trades', 'balance'
                ])

        # Trades history CSV (with prefix for backtesting)
        trades_filename = f"{self.file_prefix}trades_history.csv"
        self.trades_file = self.data_dir / trades_filename
        if not self.trades_file.exists():
            with open(self.trades_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'timestamp', 'symbol', 'side', 'action', 'price', 'quantity',
                    'reason', 'pnl', 'open_fee', 'close_fee', 'funding_fee'
                ])

    def log_trade(
        self,
        symbol: str,
        side: str,
        action: str,
        price: float,
        quantity: float,
        reason: str,
        pnl: Optional[float] = None,
        open_fee: float = 0.0,
        close_fee: float = 0.0,
        funding_fee: float = 0.0
    ):
        """
        Log a trade

        Args:
            symbol: Trading symbol (e.g., SOLUSDT)
            side: 'Buy' or 'Sell'
            action: 'OPEN' or 'CLOSE'
            price: Trade price
            quantity: Trade quantity
            reason: Reason for trade
            pnl: PnL if closing (optional)
            open_fee: Fee paid to open position (default: 0.0)
            close_fee: Fee paid to close position (default: 0.0)
            funding_fee: Accumulated funding fees (default: 0.0)
        """
        trade = TradeMetric(
            timestamp=format_helsinki(),
            symbol=symbol,
            side=side,
            action=action,
            price=price,
            quantity=quantity,
            reason=reason,
            pnl=pnl,
            open_fee=open_fee,
            close_fee=close_fee,
            funding_fee=funding_fee
        )

        self.trades.append(trade)
        self.total_trades += 1

        # Update statistics for closing trades
        if action == 'CLOSE' and pnl is not None:
            self.realized_pnl += pnl
            self.current_balance += pnl

            if pnl > 0:
                self.winning_trades += 1
            else:
                self.losing_trades += 1

            # Update peak and drawdown
            if self.current_balance > self.peak_balance:
                self.peak_balance = self.current_balance

            drawdown = self.peak_balance - self.current_balance
            if drawdown > self.max_drawdown:
                self.max_drawdown = drawdown

        # Write to CSV
        with open(self.trades_file, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                trade.timestamp, trade.symbol, trade.side, trade.action, trade.price,
                trade.quantity, trade.reason, trade.pnl or '',
                trade.open_fee, trade.close_fee, trade.funding_fee
            ])

        self.logger.debug(f"Trade logged: {action} {side} {quantity} @ ${price:.4f}")

    def log_snapshot(
        self,
        symbol: str,
        price: float,
        long_positions: int,
        short_positions: int,
        long_qty: float,
        short_qty: float,
        long_pnl: float,
        short_pnl: float
    ):
        """
        Log a performance snapshot

        Args:
            symbol: Trading symbol (e.g., SOLUSDT)
            price: Current price
            long_positions: Number of LONG positions
            short_positions: Number of SHORT positions
            long_qty: Total LONG quantity
            short_qty: Total SHORT quantity
            long_pnl: LONG unrealized PnL
            short_pnl: SHORT unrealized PnL
        """
        total_pnl = long_pnl + short_pnl + self.realized_pnl

        snapshot = PerformanceSnapshot(
            timestamp=format_helsinki(),
            symbol=symbol,
            price=price,
            long_positions=long_positions,
            short_positions=short_positions,
            long_qty=long_qty,
            short_qty=short_qty,
            long_pnl=long_pnl,
            short_pnl=short_pnl,
            total_pnl=total_pnl,
            total_trades=self.total_trades,
            balance=self.current_balance + long_pnl + short_pnl
        )

        self.snapshots.append(snapshot)
        self.total_pnl = total_pnl

        # Write to CSV
        with open(self.metrics_file, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                snapshot.timestamp, snapshot.symbol, snapshot.price, snapshot.long_positions,
                snapshot.short_positions, snapshot.long_qty, snapshot.short_qty,
                snapshot.long_pnl, snapshot.short_pnl, snapshot.total_pnl,
                snapshot.total_trades, snapshot.balance
            ])

    def generate_summary_report(self) -> Dict:
        """
        Generate summary report

        Returns:
            Dictionary with summary statistics
        """
        end_time = now_helsinki()
        duration = (end_time - self.start_time).total_seconds() / 3600  # hours

        # Calculate statistics
        win_rate = (self.winning_trades / max(self.winning_trades + self.losing_trades, 1)) * 100

        # Get best and worst trades
        closing_trades = [t for t in self.trades if t.action == 'CLOSE' and t.pnl is not None]
        best_trade = max([t.pnl for t in closing_trades], default=0)
        worst_trade = min([t.pnl for t in closing_trades], default=0)

        # Calculate average trade
        avg_profit = self.realized_pnl / max(len(closing_trades), 1)

        # ROI
        roi = (self.total_pnl / self.initial_balance) * 100

        # Max drawdown %
        max_drawdown_pct = (self.max_drawdown / self.peak_balance) * 100 if self.peak_balance > 0 else 0

        summary = {
            "period": {
                "start": self.start_time.strftime("%Y-%m-%d %H:%M:%S"),
                "end": end_time.strftime("%Y-%m-%d %H:%M:%S"),
                "duration_hours": round(duration, 2)
            },
            "performance": {
                "initial_balance": self.initial_balance,
                "final_balance": round(self.current_balance + self.total_pnl - self.realized_pnl, 2),
                "realized_pnl": round(self.realized_pnl, 2),
                "unrealized_pnl": round(self.total_pnl - self.realized_pnl, 2),
                "total_pnl": round(self.total_pnl, 2),
                "roi_percent": round(roi, 2),
                "best_trade": round(best_trade, 2),
                "worst_trade": round(worst_trade, 2),
                "avg_trade": round(avg_profit, 2),
                "win_rate": round(win_rate, 2)
            },
            "trading_stats": {
                "total_trades": self.total_trades,
                "winning_trades": self.winning_trades,
                "losing_trades": self.losing_trades,
                "open_trades": self.total_trades - (self.winning_trades + self.losing_trades)
            },
            "risk_metrics": {
                "max_drawdown": round(self.max_drawdown, 2),
                "max_drawdown_percent": round(max_drawdown_pct, 2),
                "peak_balance": round(self.peak_balance, 2)
            }
        }

        return summary

    def save_summary_report(self, date_suffix: str = None):
        """
        Save summary report to files

        Args:
            date_suffix: Optional date suffix for filename (e.g., '2025-10-10').
                        If None, uses default 'summary_report' name.
        """
        summary = self.generate_summary_report()

        # Build filename with optional date suffix
        if date_suffix:
            base_name = f"summary_report_{date_suffix}"
        else:
            base_name = "summary_report"

        # Save JSON (with prefix for backtesting)
        json_filename = f"{self.file_prefix}{base_name}.json"
        json_file = self.data_dir / json_filename
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)

        # Save human-readable text (with prefix for backtesting)
        txt_filename = f"{self.file_prefix}{base_name}.txt"
        txt_file = self.data_dir / txt_filename
        with open(txt_file, 'w', encoding='utf-8') as f:
            f.write("═" * 60 + "\n")
            f.write("📊 SOL-Trader Performance Summary\n")
            f.write("═" * 60 + "\n\n")

            # Period
            f.write(f"📅 Period:\n")
            f.write(f"  Start:    {summary['period']['start']}\n")
            f.write(f"  End:      {summary['period']['end']}\n")
            f.write(f"  Duration: {summary['period']['duration_hours']:.2f} hours\n\n")

            # Performance
            perf = summary['performance']
            f.write(f"💰 Performance:\n")
            f.write(f"  Initial Balance:  ${perf['initial_balance']:.2f}\n")
            f.write(f"  Final Balance:    ${perf['final_balance']:.2f}\n")
            f.write(f"  Realized PnL:     ${perf['realized_pnl']:.2f}\n")
            f.write(f"  Unrealized PnL:   ${perf['unrealized_pnl']:.2f}\n")
            f.write(f"  Total PnL:        ${perf['total_pnl']:.2f} ({perf['roi_percent']:+.2f}%)\n\n")

            f.write(f"  Best Trade:       ${perf['best_trade']:.2f}\n")
            f.write(f"  Worst Trade:      ${perf['worst_trade']:.2f}\n")
            f.write(f"  Average Trade:    ${perf['avg_trade']:.2f}\n")
            f.write(f"  Win Rate:         {perf['win_rate']:.1f}%\n\n")

            # Trading Stats
            stats = summary['trading_stats']
            f.write(f"📈 Trading Statistics:\n")
            f.write(f"  Total Trades:     {stats['total_trades']}\n")
            f.write(f"  Winning Trades:   {stats['winning_trades']}\n")
            f.write(f"  Losing Trades:    {stats['losing_trades']}\n")
            f.write(f"  Open Trades:      {stats['open_trades']}\n\n")

            # Risk Metrics
            risk = summary['risk_metrics']
            f.write(f"⚠️  Risk Metrics:\n")
            f.write(f"  Max Drawdown:     ${risk['max_drawdown']:.2f} ({risk['max_drawdown_percent']:.2f}%)\n")
            f.write(f"  Peak Balance:     ${risk['peak_balance']:.2f}\n\n")

            f.write("═" * 60 + "\n")

        self.logger.info(f"Summary report saved to {json_file} and {txt_file}")

        return summary
