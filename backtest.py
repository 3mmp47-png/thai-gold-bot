from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

from config import SPREAD_THB, TIMEFRAME
from gold_data import add_indicators, fetch_thai_gold_intraday, generate_signal_for_row


@dataclass
class BacktestTrade:
    entry_time: str
    entry_price: float
    exit_time: str
    exit_price: float
    exit_reason: str  # "TP", "SL", "SELL_SIGNAL", "EOD_CLOSE"
    gross_pnl: float
    spread_cost: float
    net_pnl: float


def simulate_5day_backtest(
    df_ind: pd.DataFrame,
    days: int = 5,
    spread_thb: float = SPREAD_THB,
) -> tuple[list[BacktestTrade], dict]:
    """
    Simulate short-term Long-only Thai Gold 96.5% trading over the last `days` calendar days.
    Entry on BUY signal at candle Close; exit on High >= TP, Low <= SL, or SELL signal.
    """
    end_ts = df_ind.index.max()
    start_ts = end_ts - pd.Timedelta(days=days)
    test_indices = [i for i in range(1, len(df_ind)) if df_ind.index[i] >= start_ts]

    trades: list[BacktestTrade] = []
    position: Optional[dict] = None

    for i in test_indices:
        row = df_ind.iloc[i]
        prev_row = df_ind.iloc[i - 1]
        ts = str(row.name)[:19]

        if position is not None:
            # Check SL first (conservative), then TP, then Trailing/Profit-Lock
            low_p = float(row["Low"])
            high_p = float(row["High"])
            close_p = float(row["Close"])

            exit_price: Optional[float] = None
            exit_reason: Optional[str] = None

            if low_p <= position["sl"]:
                exit_price = position["sl"]
                exit_reason = "TRAILING_TP" if position.get("locked_profit", False) else "SL"
            elif high_p >= position["tp"]:
                exit_price = position["tp"]
                exit_reason = "TP"
            else:
                # Move SL to lock in +110 THB gross profit once price rallies at least +190 THB
                if high_p >= position["entry_price"] + 190.0:
                    new_sl = position["entry_price"] + 110.0
                    if new_sl > position["sl"]:
                        position["sl"] = new_sl
                        position["locked_profit"] = True

            if exit_price is not None and exit_reason is not None:
                gross = exit_price - position["entry_price"]
                net = gross - spread_thb
                trades.append(
                    BacktestTrade(
                        entry_time=position["entry_time"],
                        entry_price=round(position["entry_price"], 2),
                        exit_time=ts,
                        exit_price=round(exit_price, 2),
                        exit_reason=exit_reason,
                        gross_pnl=round(gross, 2),
                        spread_cost=spread_thb,
                        net_pnl=round(net, 2),
                    )
                )
                position = None
                continue

        if position is None:
            sig = generate_signal_for_row(row, prev_row)
            if sig.action == "BUY":
                position = {
                    "entry_time": ts,
                    "entry_price": float(row["Close"]),
                    "tp": sig.take_profit,
                    "sl": sig.stop_loss,
                }

    # Close any open position at the end of the 5-day window (mark-to-market)
    if position is not None and test_indices:
        last_row = df_ind.iloc[test_indices[-1]]
        exit_p = float(last_row["Close"])
        gross = exit_p - position["entry_price"]
        net = gross - spread_thb
        trades.append(
            BacktestTrade(
                entry_time=position["entry_time"],
                entry_price=round(position["entry_price"], 2),
                exit_time=str(last_row.name)[:19],
                exit_price=round(exit_p, 2),
                exit_reason="EOD_CLOSE",
                gross_pnl=round(gross, 2),
                spread_cost=spread_thb,
                net_pnl=round(net, 2),
            )
        )

    # Calculate statistics
    total = len(trades)
    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl <= 0]
    win_rate = (len(wins) / total * 100.0) if total > 0 else 0.0
    total_net_pnl = sum(t.net_pnl for t in trades)
    total_gross_pnl = sum(t.gross_pnl for t in trades)
    gross_profit = sum(t.net_pnl for t in wins)
    gross_loss = abs(sum(t.net_pnl for t in losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)

    # Max Drawdown
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        equity += t.net_pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    summary = {
        "period_start": str(df_ind.index[test_indices[0]])[:19] if test_indices else "",
        "period_end": str(end_ts)[:19],
        "candles_tested": len(test_indices),
        "timeframe": TIMEFRAME,
        "spread_thb": spread_thb,
        "total_trades": total,
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate_pct": round(win_rate, 2),
        "total_gross_pnl_thb": round(total_gross_pnl, 2),
        "total_net_pnl_thb": round(total_net_pnl, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else "INF",
        "max_drawdown_thb": round(max_dd, 2),
        "avg_win_thb": round(gross_profit / len(wins), 2) if wins else 0.0,
        "avg_loss_thb": round(-gross_loss / len(losses), 2) if losses else 0.0,
    }
    return trades, summary


def run_backtest(days: int = 30, interval: str = TIMEFRAME, output_dir: str = ".") -> dict:
    # Fetch 35 days so indicators warm up cleanly before the 30-day window
    fetch_days = max(days + 5, 14)
    df = fetch_thai_gold_intraday(period=f"{fetch_days}d", interval=interval, use_cache=True)
    df_ind = add_indicators(df)

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    # Primary run with configured SPREAD_THB
    trades, summary = simulate_5day_backtest(df_ind, days=days, spread_thb=SPREAD_THB)

    # Save trade log & summary
    if trades:
        pd.DataFrame([asdict(t) for t in trades]).to_csv(
            out_path / "backtest_5d_trades.csv", index=False, encoding="utf-8-sig"
        )
    with open(out_path / "backtest_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 65)
    print(f"📊 ผลการทดสอบเทรดทองคำไทยย้อนหลัง {days} วัน (Timeframe: {interval})")
    print(f"ช่วงเวลา: {summary['period_start']}  -->  {summary['period_end']}")
    print("=" * 65)
    print(f"จำนวนแท่งเทียนที่ทดสอบ : {summary['candles_tested']} แท่ง")
    print(f"Spread ที่หักต่อรอบ      : {summary['spread_thb']:,.0f} บาท/บาททอง")
    print(f"จำนวนไม้ที่เทรดทั้งหมด   : {summary['total_trades']} ไม้ (ชนะ {summary['winning_trades']} / แพ้ {summary['losing_trades']})")
    print(f"Win Rate               : {summary['win_rate_pct']:.2f}%")
    print(f"กำไรขั้นต้น (ก่อนหัก Spread): {summary['total_gross_pnl_thb']:+,.2f} บาท")
    print(f"กำไรสุทธิ (Net PnL)      : {summary['total_net_pnl_thb']:+,.2f} บาท/บาททอง")
    print(f"Profit Factor          : {summary['profit_factor']}")
    print(f"Max Drawdown           : -{summary['max_drawdown_thb']:,.2f} บาท")
    print(f"เฉลี่ยไม้ชนะ / ไม้แพ้   : {summary['avg_win_thb']:+,.2f} / {summary['avg_loss_thb']:+,.2f} บาท")

    print("\n--- เปรียบเทียบตามค่า Spread (ต่อ 1 บาททองคำ) ---")
    for sp in [0.0, 50.0, 100.0]:
        _, s = simulate_5day_backtest(df_ind, days=days, spread_thb=sp)
        label = "Spot/No Spread" if sp == 0 else ("แอปเทรดทอง (50บ.)" if sp == 50 else "ทองแท่งสมาคม (100บ.)")
        print(f"  • {label:<22} => Net PnL: {s['total_net_pnl_thb']:+8,.2f} บาท | Win Rate: {s['win_rate_pct']:5.1f}%")

    if trades:
        print("\n--- รายการเทรด (Trade Log) ---")
        for idx, t in enumerate(trades, 1):
            print(
                f"{idx:2d}. BUY {t.entry_time} @ {t.entry_price:,.0f} -> "
                f"EXIT {t.exit_time} @ {t.exit_price:,.0f} ({t.exit_reason}) | "
                f"Net: {t.net_pnl:+,.0f} บ."
            )
    print("=" * 65 + "\n")
    return summary


if __name__ == "__main__":
    run_backtest(days=5)
