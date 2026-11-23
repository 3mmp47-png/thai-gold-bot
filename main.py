from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import logging
import os
import threading
import time
from typing import Optional

from backtest import run_backtest
from config import CHECK_INTERVAL_SEC, LOOKBACK_DAYS, TIMEFRAME
from gold_data import (
    add_indicators,
    fetch_live_gta_price,
    fetch_thai_gold_intraday,
    generate_signal_for_row,
)
from news_ai import fetch_gold_news, generate_ai_analysis
from portfolio_tracker import (
    check_open_positions_alerts,
    format_stats_message,
    get_open_positions,
    record_buy,
    record_sell,
)
from telegram_bot import (
    build_trading_keyboard,
    poll_telegram_updates,
    send_telegram_message,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

_latest_cached_price: float = 58050.0


class _HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"Thai Gold Advisor Bot is running 24/7. Latest price: {_latest_cached_price:,.0f} THB".encode("utf-8"))

    def log_message(self, format: str, *args) -> None:
        pass  # Suppress noisy HTTP access logs


def _start_cloud_health_server() -> None:
    """Start a lightweight HTTP health-check server if PORT env var is set (for Render/HuggingFace/Koyeb 24/7 hosting)."""
    port_str = os.getenv("PORT", "")
    if not port_str:
        return
    try:
        port = int(port_str)
        server = HTTPServer(("0.0.0.0", port), _HealthCheckHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        logger.info("Cloud 24/7 health-check server listening on port %d", port)
    except Exception as exc:
        logger.warning("Could not start health-check server: %s", exc)


def get_latest_price() -> float:
    global _latest_cached_price
    try:
        gta = fetch_live_gta_price()
        if gta and gta.get("sell"):
            _latest_cached_price = float(gta["sell"])
            return _latest_cached_price
        df = fetch_thai_gold_intraday(period="5d", interval=TIMEFRAME)
        if not df.empty:
            _latest_cached_price = round(float(df["Close"].iloc[-1]), 0)
    except Exception:
        pass
    return _latest_cached_price


def run_once(dry_run: bool = False, chat_id_override: Optional[str] = None) -> str:
    """Fetch latest Thai Gold data + news, generate analysis, and send Telegram alert with manual Buy/Sell buttons."""
    global _latest_cached_price
    fetch_days = min(max(LOOKBACK_DAYS, 7), 14)
    df = fetch_thai_gold_intraday(period=f"{fetch_days}d", interval=TIMEFRAME)
    df_ind = add_indicators(df)
    signal = generate_signal_for_row(df_ind.iloc[-1], df_ind.iloc[-2])
    _latest_cached_price = signal.thai_gold_price

    gta = fetch_live_gta_price()
    news = fetch_gold_news(limit=6)
    message = generate_ai_analysis(signal, news, gta)

    open_pos = get_open_positions()
    latest_id = open_pos[-1]["trade_id"] if open_pos else None
    keyboard = build_trading_keyboard(
        current_price=signal.thai_gold_price,
        tp=signal.take_profit,
        sl=signal.stop_loss,
        trade_id=latest_id,
        action=signal.action,
        confidence=signal.confidence,
        entry_low=signal.entry_low,
    )
    send_telegram_message(
        message,
        dry_run=dry_run,
        reply_markup=keyboard,
        chat_id_override=chat_id_override,
    )
    return signal.action


def run_realtime_loop(dry_run: bool = False, max_runtime_sec: int = 0) -> None:
    """
    Monitor Thai Gold in real-time (every 5 minutes), send signal alerts to Telegram,
    and alert when any user-recorded position reaches TP / Trailing Lock / SL.
    If max_runtime_sec > 0 (e.g. on GitHub Actions 24/7 runner), exits cleanly after that duration.
    """
    global _latest_cached_price
    stop_event = threading.Event()
    start_time = time.time()
    _start_cloud_health_server()

    if not dry_run:
        listener_thread = threading.Thread(
            target=poll_telegram_updates,
            args=(get_latest_price, lambda cid: run_once(dry_run=False, chat_id_override=cid), stop_event),
            daemon=True,
        )
        listener_thread.start()

    logger.info(
        "Starting real-time Thai Gold 5m advisor & Telegram alert bot (interval=%ss)",
        CHECK_INTERVAL_SEC,
    )
    last_action: Optional[str] = None
    fetch_days = min(max(LOOKBACK_DAYS, 7), 14)

    try:
        while True:
            try:
                df = fetch_thai_gold_intraday(period=f"{fetch_days}d", interval=TIMEFRAME)
                df_ind = add_indicators(df)
                signal = generate_signal_for_row(df_ind.iloc[-1], df_ind.iloc[-2])
                _latest_cached_price = signal.thai_gold_price

                # 1. Check if any user-recorded BUY position reached TP / Trailing Lock / SL
                pos_alerts = check_open_positions_alerts(signal.thai_gold_price)
                for pos, alert_text in pos_alerts:
                    kb = build_trading_keyboard(
                        current_price=signal.thai_gold_price,
                        tp=pos["tp"],
                        sl=pos["sl"],
                        trade_id=pos["trade_id"],
                        action=signal.action,
                        confidence=signal.confidence,
                        entry_low=signal.entry_low,
                    )
                    send_telegram_message(alert_text, dry_run=dry_run, reply_markup=kb)

                # 2. Check market signal alert
                should_alert = (
                    (last_action is None)
                    or (signal.action != last_action)
                    or (signal.action in ("BUY", "SELL"))
                )
                if should_alert:
                    gta = fetch_live_gta_price()
                    news = fetch_gold_news(limit=6)
                    message = generate_ai_analysis(signal, news, gta)
                    open_pos = get_open_positions()
                    latest_id = open_pos[-1]["trade_id"] if open_pos else None
                    kb = build_trading_keyboard(
                        current_price=signal.thai_gold_price,
                        tp=signal.take_profit,
                        sl=signal.stop_loss,
                        trade_id=latest_id,
                        action=signal.action,
                        confidence=signal.confidence,
                        entry_low=signal.entry_low,
                    )
                    send_telegram_message(message, dry_run=dry_run, reply_markup=kb)
                    last_action = signal.action
                else:
                    logger.info(
                        "No signal change (%s @ %,.0f THB) | Open user trades: %d",
                        signal.action,
                        signal.thai_gold_price,
                        len(get_open_positions()),
                    )
            except Exception as exc:
                logger.exception("Error in monitoring loop: %s", exc)

            if max_runtime_sec > 0 and (time.time() - start_time + CHECK_INTERVAL_SEC >= max_runtime_sec):
                remaining = max(0.0, max_runtime_sec - (time.time() - start_time))
                if remaining > 0:
                    time.sleep(remaining)
                stop_event.set()
                logger.info("Reached max_runtime_sec (%ds), exiting shift cleanly.", max_runtime_sec)
                break

            time.sleep(CHECK_INTERVAL_SEC)
    except KeyboardInterrupt:
        stop_event.set()
        logger.info("Stopped by user.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Thai Gold 96.5% Short-Term Advisor & Telegram Alert Bot")
    parser.add_argument("--once", action="store_true", help="Run analysis once and send alert with buttons")
    parser.add_argument("--backtest", action="store_true", help="Run historical backtest (default 30 days)")
    parser.add_argument("--days", type=int, default=30, help="Number of days for backtest (default: 30)")
    parser.add_argument("--dry-run", action="store_true", help="Print Telegram message & buttons to console")
    parser.add_argument("--sim-buy", type=float, help="Simulate tapping the Buy button at a given price")
    parser.add_argument("--sim-sell", type=float, help="Simulate tapping the Sell button at a given price")
    parser.add_argument("--stats", action="store_true", help="Show user portfolio and cumulative trading stats")
    parser.add_argument("--max-runtime-sec", type=int, default=0, help="Max runtime in seconds for cloud shift runners")
    args = parser.parse_args()

    if args.sim_buy:
        pos = record_buy(args.sim_buy)
        print(f"✅ บันทึกไม้ซื้อ #{pos['trade_id']} @ {pos['entry_price']:,.0f} บาท (TP {pos['tp']:,.0f} / SL {pos['sl']:,.0f})")
        print(format_stats_message(current_price=args.sim_buy))
    elif args.sim_sell:
        closed = record_sell(args.sim_sell)
        if closed:
            print(f"🧾 ปิดไม้ขาย #{closed['trade_id']} @ {closed['exit_price']:,.0f} บาท | กำไรสุทธิ: {closed['net_pnl']:+,.0f} บาท")
        else:
            print("⚠️ ไม่มีไม้ซื้อที่เปิดค้างอยู่")
        print(format_stats_message(current_price=args.sim_sell))
    elif args.stats:
        print(format_stats_message())
    elif args.backtest:
        run_backtest(days=args.days)
    elif args.once:
        run_once(dry_run=args.dry_run)
    else:
        run_realtime_loop(dry_run=args.dry_run, max_runtime_sec=args.max_runtime_sec)


if __name__ == "__main__":
    main()
