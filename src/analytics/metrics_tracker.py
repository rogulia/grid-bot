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
        """Initialize CSV files - no longer needed, files created dynamically with dates"""
        # CSV files now include date suffix and are created on-demand
        pass

    def _get_csv_filename(self, base_name: str) -> Path:
        """
        Get CSV filename with current date suffix

        Args:
            base_name: Base filename (e.g., 'performance_metrics' or 'trades_history')

        Returns:
            Full path with format: {prefix}{base_name}_{YYYY-MM-DD}.csv
        """
        current_date = now_helsinki().strftime("%Y-%m-%d")
        filename = f"{self.file_prefix}{base_name}_{current_date}.csv"
        return self.data_dir / filename

    def _ensure_csv_header(self, file_path: Path, headers: list):
        """
        Create CSV file with headers if it doesn't exist

        Args:
            file_path: Path to CSV file
            headers: List of column headers
        """
        if not file_path.exists():
            with open(file_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(headers)

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
        funding_fee: float = 0.0,
        timestamp: Optional[str] = None
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
            timestamp: Trade timestamp from exchange (optional, defaults to current time)
        """
        trade = TradeMetric(
            timestamp=timestamp if timestamp else format_helsinki(),
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

        # Write to CSV (with date suffix)
        trades_file = self._get_csv_filename('trades_history')
        self._ensure_csv_header(trades_file, [
            'timestamp', 'symbol', 'side', 'action', 'price', 'quantity',
            'reason', 'pnl', 'open_fee', 'close_fee', 'funding_fee'
        ])

        with open(trades_file, 'a', newline='', encoding='utf-8') as f:
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

        # Write to CSV (with date suffix)
        metrics_file = self._get_csv_filename('performance_metrics')
        self._ensure_csv_header(metrics_file, [
            'timestamp', 'symbol', 'price', 'long_positions', 'short_positions',
            'long_qty', 'short_qty', 'long_pnl', 'short_pnl',
            'total_pnl', 'total_trades', 'balance'
        ])

        with open(metrics_file, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                snapshot.timestamp, snapshot.symbol, snapshot.price, snapshot.long_positions,
                snapshot.short_positions, snapshot.long_qty, snapshot.short_qty,
                snapshot.long_pnl, snapshot.short_pnl, snapshot.total_pnl,
                snapshot.total_trades, snapshot.balance
            ])

    def generate_summary_report(self, end_time=None) -> Dict:
        """
        Generate summary report

        Args:
            end_time: Optional end time for the report (default: current time)

        Returns:
            Dictionary with summary statistics
        """
        if end_time is None:
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

    def save_summary_report(self, date_suffix: str, end_time=None):
        """
        Save summary report to files (daily reports only)

        Args:
            date_suffix: REQUIRED date suffix for filename (e.g., '2025-10-10')
            end_time: Optional end time for the report (default: current time)
        """
        summary = self.generate_summary_report(end_time=end_time)

        # Build filename with date suffix (REQUIRED - no session reports!)
        base_name = f"summary_report_{date_suffix}"

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

    def generate_daily_report(self, date: str):
        """
        Generate daily report for specific date

        Args:
            date: Date in format YYYY-MM-DD (e.g., '2025-10-11')

        Returns:
            Summary dict if report was generated, None if no data for this date
        """
        # Check if trades CSV file exists (with date suffix)
        trades_csv = self.data_dir / f"{self.file_prefix}trades_history_{date}.csv"
        metrics_csv = self.data_dir / f"{self.file_prefix}performance_metrics_{date}.csv"

        if not trades_csv.exists():
            self.logger.info(f"No trades history file found for daily report {date}")
            return None

        # Read trades from CSV
        try:
            with open(trades_csv, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                all_trades = list(reader)
        except Exception as e:
            self.logger.error(f"Failed to read trades CSV for daily report: {e}")
            return None

        # Read performance metrics from CSV (for initial/final balance and drawdown)
        initial_balance = self.initial_balance  # fallback to current initial_balance
        final_balance = self.initial_balance
        max_drawdown = 0.0
        peak_balance = self.initial_balance

        if metrics_csv.exists():
            try:
                with open(metrics_csv, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    metrics_rows = list(reader)

                if metrics_rows:
                    # Get initial balance from first row
                    initial_balance = float(metrics_rows[0]['balance'])
                    # Get final balance from last row
                    final_balance = float(metrics_rows[-1]['balance'])

                    # Calculate max drawdown from performance metrics
                    balances = [float(row['balance']) for row in metrics_rows]
                    peak_balance = initial_balance
                    max_drawdown = 0.0

                    for balance in balances:
                        if balance > peak_balance:
                            peak_balance = balance
                        drawdown = peak_balance - balance
                        if drawdown > max_drawdown:
                            max_drawdown = drawdown

                    self.logger.debug(f"Read balance from metrics: initial=${initial_balance:.2f}, final=${final_balance:.2f}")
            except Exception as e:
                self.logger.warning(f"Failed to read performance metrics CSV: {e}. Using fallback values.")

        # Filter trades by date (timestamp format: "2025-10-11 13:39:19")
        daily_trades = [t for t in all_trades if t['timestamp'].startswith(date)]

        if not daily_trades:
            self.logger.info(f"No trades found for date {date}, skipping daily report")
            return None

        self.logger.info(f"📊 Generating daily report for {date} ({len(daily_trades)} trades)")

        # Calculate daily statistics
        closing_trades = [t for t in daily_trades if t['action'] == 'CLOSE' and t.get('pnl') and t['pnl'].strip()]

        if not closing_trades:
            self.logger.info(f"No closed trades for {date}, skipping report")
            return None

        # Calculate PnL
        realized_pnl = sum(float(t['pnl']) for t in closing_trades)
        total_fees = sum(
            float(t.get('open_fee', 0) or 0) + float(t.get('close_fee', 0) or 0) + float(t.get('funding_fee', 0) or 0)
            for t in closing_trades
        )

        # Win/loss stats
        winning = [t for t in closing_trades if float(t['pnl']) > 0]
        losing = [t for t in closing_trades if float(t['pnl']) < 0]
        win_rate = (len(winning) / len(closing_trades) * 100) if closing_trades else 0

        # Best/worst trades (from CSV data, not self.trades!)
        pnls = [float(t['pnl']) for t in closing_trades]
        best_trade = max(pnls) if pnls else 0
        worst_trade = min(pnls) if pnls else 0
        avg_trade = realized_pnl / len(closing_trades) if closing_trades else 0

        # Store original tracker state
        original_start = self.start_time
        original_initial = self.initial_balance
        original_current = self.current_balance
        original_realized = self.realized_pnl
        original_winning = self.winning_trades
        original_losing = self.losing_trades
        original_total = self.total_trades
        original_pnl = self.total_pnl
        original_max_drawdown = self.max_drawdown
        original_peak = self.peak_balance
        original_trades = self.trades.copy()

        # Temporarily set daily values
        # Create timezone-aware datetime for Helsinki
        import pytz
        helsinki_tz = pytz.timezone('Europe/Helsinki')

        # Start time: 00:00:00
        naive_start = datetime.strptime(f"{date} 00:00:00", "%Y-%m-%d %H:%M:%S")
        self.start_time = helsinki_tz.localize(naive_start)

        # End time: 23:59:59
        naive_end = datetime.strptime(f"{date} 23:59:59", "%Y-%m-%d %H:%M:%S")
        end_time = helsinki_tz.localize(naive_end)

        # Set temporary state for report generation
        self.initial_balance = initial_balance
        self.current_balance = final_balance
        self.realized_pnl = realized_pnl
        self.winning_trades = len(winning)
        self.losing_trades = len(losing)
        self.total_trades = len(daily_trades)
        self.total_pnl = realized_pnl  # For daily report, total_pnl = realized_pnl (no unrealized)
        self.max_drawdown = max_drawdown
        self.peak_balance = peak_balance

        # Create temporary trades list with TradeMetric objects from CSV
        self.trades = []
        for t in closing_trades:
            trade = TradeMetric(
                timestamp=t['timestamp'],
                symbol=t['symbol'],
                side=t['side'],
                action=t['action'],
                price=float(t['price']),
                quantity=float(t['quantity']),
                reason=t['reason'],
                pnl=float(t['pnl']),
                open_fee=float(t.get('open_fee', 0) or 0),
                close_fee=float(t.get('close_fee', 0) or 0),
                funding_fee=float(t.get('funding_fee', 0) or 0)
            )
            self.trades.append(trade)

        try:
            # Generate and save report with date suffix and end time
            summary = self.save_summary_report(date_suffix=date, end_time=end_time)

            self.logger.info(
                f"✅ Daily report for {date}: "
                f"PnL=${realized_pnl:.2f}, Trades={len(closing_trades)}, Win Rate={win_rate:.1f}%"
            )

            return summary
        finally:
            # Restore original tracker state
            self.start_time = original_start
            self.initial_balance = original_initial
            self.current_balance = original_current
            self.realized_pnl = original_realized
            self.winning_trades = original_winning
            self.losing_trades = original_losing
            self.total_trades = original_total
            self.total_pnl = original_pnl
            self.max_drawdown = original_max_drawdown
            self.peak_balance = original_peak
            self.trades = original_trades
