#!/usr/bin/env python3
"""
Analytics script for SOL-Trader performance analysis

Usage:
    python scripts/analyze.py                    # Show full report
    python scripts/analyze.py --plot             # Show report with plots
    python scripts/analyze.py --period 24h       # Last 24 hours
    python scripts/analyze.py --export report.txt  # Export to file
"""

import argparse
import sys
from pathlib import Path
from datetime import datetime, timedelta
import json

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import pandas as pd
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    HAS_PLOTTING = True
except ImportError:
    HAS_PLOTTING = False
    print("⚠️  Warning: pandas/matplotlib not installed. Plotting disabled.")
    print("   Install with: pip install pandas matplotlib")


def load_performance_metrics(file_path="data/performance_metrics.csv"):
    """Load performance metrics CSV"""
    if not Path(file_path).exists():
        print(f"❌ File not found: {file_path}")
        return None

    df = pd.read_csv(file_path)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    return df


def load_trades_history(file_path="data/trades_history.csv"):
    """Load trades history CSV"""
    if not Path(file_path).exists():
        print(f"❌ File not found: {file_path}")
        return None

    df = pd.read_csv(file_path)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    return df


def load_summary_report(file_path="data/summary_report.json"):
    """Load summary report JSON"""
    if not Path(file_path).exists():
        return None

    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def filter_by_period(df, period):
    """Filter dataframe by time period"""
    if period == 'all':
        return df

    now = datetime.now()

    if period.endswith('h'):
        hours = int(period[:-1])
        start_time = now - timedelta(hours=hours)
    elif period.endswith('d'):
        days = int(period[:-1])
        start_time = now - timedelta(days=days)
    else:
        print(f"⚠️  Unknown period format: {period}. Using 'all'")
        return df

    return df[df['timestamp'] >= start_time]


def print_summary(summary):
    """Print summary report"""
    print("\n" + "═" * 60)
    print("📊 SOL-Trader Performance Summary")
    print("═" * 60)

    if summary is None:
        print("⚠️  No summary report found. Run the bot to generate one.")
        return

    # Period
    print(f"\n📅 Period:")
    print(f"  Start:    {summary['period']['start']}")
    print(f"  End:      {summary['period']['end']}")
    print(f"  Duration: {summary['period']['duration_hours']:.2f} hours")

    # Performance
    perf = summary['performance']
    print(f"\n💰 Performance:")
    print(f"  Initial Balance:  ${perf['initial_balance']:.2f}")
    print(f"  Final Balance:    ${perf['final_balance']:.2f}")
    print(f"  Realized PnL:     ${perf['realized_pnl']:.2f}")
    print(f"  Unrealized PnL:   ${perf['unrealized_pnl']:.2f}")

    pnl_sign = "+" if perf['total_pnl'] >= 0 else ""
    roi_sign = "+" if perf['roi_percent'] >= 0 else ""
    print(f"  Total PnL:        ${pnl_sign}{perf['total_pnl']:.2f} ({roi_sign}{perf['roi_percent']:.2f}%)")

    print(f"\n  Best Trade:       ${perf['best_trade']:.2f}")
    print(f"  Worst Trade:      ${perf['worst_trade']:.2f}")
    print(f"  Average Trade:    ${perf['avg_trade']:.2f}")
    print(f"  Win Rate:         {perf['win_rate']:.1f}%")

    # Trading Stats
    stats = summary['trading_stats']
    print(f"\n📈 Trading Statistics:")
    print(f"  Total Trades:     {stats['total_trades']}")
    print(f"  Winning Trades:   {stats['winning_trades']}")
    print(f"  Losing Trades:    {stats['losing_trades']}")
    print(f"  Open Trades:      {stats['open_trades']}")

    # Risk Metrics
    risk = summary['risk_metrics']
    print(f"\n⚠️  Risk Metrics:")
    print(f"  Max Drawdown:     ${risk['max_drawdown']:.2f} ({risk['max_drawdown_percent']:.2f}%)")
    print(f"  Peak Balance:     ${risk['peak_balance']:.2f}")

    print("\n" + "═" * 60)


def analyze_metrics(df, period='all'):
    """Analyze performance metrics"""
    if df is None or df.empty:
        print("❌ No metrics data available")
        return

    df = filter_by_period(df, period)

    if df.empty:
        print(f"❌ No data for period: {period}")
        return

    print(f"\n📊 Metrics Analysis (Period: {period})")
    print("─" * 60)

    # Basic stats
    print(f"\n📈 Price Statistics:")
    print(f"  Start Price:  ${df.iloc[0]['price']:.4f}")
    print(f"  End Price:    ${df.iloc[-1]['price']:.4f}")
    print(f"  Highest:      ${df['price'].max():.4f}")
    print(f"  Lowest:       ${df['price'].min():.4f}")
    price_change = ((df.iloc[-1]['price'] - df.iloc[0]['price']) / df.iloc[0]['price']) * 100
    print(f"  Change:       {price_change:+.2f}%")

    # PnL stats
    print(f"\n💰 PnL Statistics:")
    print(f"  Final Total PnL:    ${df.iloc[-1]['total_pnl']:.2f}")
    print(f"  Max Total PnL:      ${df['total_pnl'].max():.2f}")
    print(f"  Min Total PnL:      ${df['total_pnl'].min():.2f}")
    print(f"  Final Balance:      ${df.iloc[-1]['balance']:.2f}")

    # Position stats
    print(f"\n📊 Position Statistics:")
    print(f"  Max LONG positions:  {int(df['long_positions'].max())}")
    print(f"  Max SHORT positions: {int(df['short_positions'].max())}")
    print(f"  Total trades:        {int(df.iloc[-1]['total_trades'])}")


def plot_performance(df, period='all'):
    """Plot performance charts"""
    if not HAS_PLOTTING:
        print("❌ Plotting not available. Install pandas and matplotlib.")
        return

    if df is None or df.empty:
        print("❌ No data to plot")
        return

    df = filter_by_period(df, period)

    if df.empty:
        print(f"❌ No data for period: {period}")
        return

    # Create subplots
    fig, axes = plt.subplots(3, 1, figsize=(12, 10))
    fig.suptitle(f'SOL-Trader Performance Analysis ({period})', fontsize=16)

    # Plot 1: Price and PnL
    ax1 = axes[0]
    ax1_twin = ax1.twinx()

    ax1.plot(df['timestamp'], df['price'], 'b-', label='Price', linewidth=1.5)
    ax1_twin.plot(df['timestamp'], df['total_pnl'], 'g-', label='Total PnL', linewidth=1.5)

    ax1.set_ylabel('Price (USDT)', color='b')
    ax1_twin.set_ylabel('Total PnL (USDT)', color='g')
    ax1.set_title('Price vs Total PnL')
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc='upper left')
    ax1_twin.legend(loc='upper right')

    # Plot 2: LONG vs SHORT PnL
    ax2 = axes[1]
    ax2.plot(df['timestamp'], df['long_pnl'], 'g-', label='LONG PnL', linewidth=1.5)
    ax2.plot(df['timestamp'], df['short_pnl'], 'r-', label='SHORT PnL', linewidth=1.5)
    ax2.axhline(y=0, color='black', linestyle='--', alpha=0.3)
    ax2.set_ylabel('PnL (USDT)')
    ax2.set_title('LONG vs SHORT PnL')
    ax2.legend()
    ax2.grid(True, alpha=0.3)

    # Plot 3: Number of positions
    ax3 = axes[2]
    ax3.plot(df['timestamp'], df['long_positions'], 'g-', label='LONG Positions', linewidth=1.5)
    ax3.plot(df['timestamp'], df['short_positions'], 'r-', label='SHORT Positions', linewidth=1.5)
    ax3.set_ylabel('Number of Positions')
    ax3.set_xlabel('Time')
    ax3.set_title('Active Positions Over Time')
    ax3.legend()
    ax3.grid(True, alpha=0.3)

    # Format x-axis
    for ax in axes:
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=1))
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)

    plt.tight_layout()
    plt.show()


def main():
    parser = argparse.ArgumentParser(description='Analyze SOL-Trader performance')
    parser.add_argument('--period', default='all', help='Time period (e.g., 24h, 7d, all)')
    parser.add_argument('--plot', action='store_true', help='Show performance plots')
    parser.add_argument('--export', help='Export report to file')

    args = parser.parse_args()

    # Load data
    print("\n🔍 Loading data...")
    summary = load_summary_report()
    metrics_df = load_performance_metrics()
    trades_df = load_trades_history()

    # Show summary
    print_summary(summary)

    # Analyze metrics
    if metrics_df is not None:
        analyze_metrics(metrics_df, args.period)

    # Plot if requested
    if args.plot:
        if HAS_PLOTTING:
            print("\n📈 Generating plots...")
            plot_performance(metrics_df, args.period)
        else:
            print("\n❌ Plotting requires pandas and matplotlib")
            print("   Install with: pip install pandas matplotlib")

    # Export if requested
    if args.export:
        print(f"\n💾 Exporting report to {args.export}...")
        # TODO: Implement export functionality
        print("   (Export functionality to be implemented)")

    print("\n✅ Analysis complete!")
    print(f"\n💡 Tip: Use --plot to see visual charts")
    print(f"   Example: python scripts/analyze.py --plot --period 24h\n")


if __name__ == "__main__":
    main()
