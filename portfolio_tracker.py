from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import SPREAD_THB

BASE_DIR = Path(__file__).parent
PORTFOLIO_FILE = BASE_DIR / "user_portfolio.json"
HISTORY_CSV = BASE_DIR / "user_trade_history.csv"


def _load_data() -> dict:
    if PORTFOLIO_FILE.exists():
        try:
            with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"open_positions": [], "closed_trades": [], "next_id": 1}


def _save_data(data: dict) -> None:
    with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _append_csv(trade: dict) -> None:
    file_exists = HISTORY_CSV.exists()
    fieldnames = [
        "trade_id",
        "entry_time",
        "entry_price",
        "exit_time",
        "exit_price",
        "gross_pnl",
        "spread_cost",
        "net_pnl",
        "result",
    ]
    with open(HISTORY_CSV, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow({k: trade.get(k, "") for k in fieldnames})


def get_open_positions() -> list[dict]:
    data = _load_data()
    return data.get("open_positions", [])


def record_buy(price: float, tp: Optional[float] = None, sl: Optional[float] = None) -> dict:
    """Record a new user BUY position when they tap the Buy button on Telegram."""
    data = _load_data()
    trade_id = data.get("next_id", 1)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    pos = {
        "trade_id": trade_id,
        "entry_time": now_str,
        "entry_price": round(float(price), 2),
        "tp": round(float(tp), 2) if tp else round(float(price) + 260.0, 2),
        "sl": round(float(sl), 2) if sl else round(float(price) - 280.0, 2),
        "trailing_locked": False,
        "alerted_tp": False,
    }
    data["open_positions"].append(pos)
    data["next_id"] = trade_id + 1
    _save_data(data)
    return pos


def record_sell(exit_price: float, trade_id: Optional[int] = None) -> Optional[dict]:
    """
    Close an open BUY position (specific trade_id, or latest open position if None)
    and compute updated trading statistics.
    """
    data = _load_data()
    open_positions = data.get("open_positions", [])
    if not open_positions:
        return None

    target_idx = len(open_positions) - 1
    if trade_id is not None:
        for idx, p in enumerate(open_positions):
            if p["trade_id"] == trade_id:
                target_idx = idx
                break

    pos = open_positions.pop(target_idx)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry_p = float(pos["entry_price"])
    exit_p = float(exit_price)
    gross = round(exit_p - entry_p, 2)
    net = round(gross - SPREAD_THB, 2)

    closed = {
        "trade_id": pos["trade_id"],
        "entry_time": pos["entry_time"],
        "entry_price": entry_p,
        "exit_time": now_str,
        "exit_price": round(exit_p, 2),
        "gross_pnl": gross,
        "spread_cost": SPREAD_THB,
        "net_pnl": net,
        "result": "WIN" if net > 0 else "LOSS",
    }
    data["closed_trades"].append(closed)
    _save_data(data)
    _append_csv(closed)
    return closed


def compute_user_stats() -> dict:
    """Compute cumulative performance statistics from all user-recorded trades."""
    data = _load_data()
    closed = data.get("closed_trades", [])
    open_pos = data.get("open_positions", [])

    total = len(closed)
    wins = [t for t in closed if t["net_pnl"] > 0]
    losses = [t for t in closed if t["net_pnl"] <= 0]
    win_rate = (len(wins) / total * 100.0) if total > 0 else 0.0
    total_gross = sum(t["gross_pnl"] for t in closed)
    total_net = sum(t["net_pnl"] for t in closed)
    gross_win = sum(t["net_pnl"] for t in wins)
    gross_loss = abs(sum(t["net_pnl"] for t in losses))
    pf = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)

    return {
        "total_closed": total,
        "open_count": len(open_pos),
        "open_positions": open_pos,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(win_rate, 2),
        "total_gross_pnl": round(total_gross, 2),
        "total_net_pnl": round(total_net, 2),
        "avg_net_pnl": round(total_net / total, 2) if total > 0 else 0.0,
        "profit_factor": round(pf, 2) if pf != float("inf") else "INF",
        "recent_trades": closed[-5:],
    }


def format_stats_message(current_price: Optional[float] = None) -> str:
    """Format user portfolio and historical statistics for Telegram display."""
    s = compute_user_stats()
    lines = [
        "📊 *สถิติการเทรดจริงของคุณ (Portfolio & Stats)*",
        f"• จำนวนไม้ที่ปิดแล้ว: `{s['total_closed']}` ไม้ (ชนะ `{s['wins']}` / แพ้ `{s['losses']}`)",
        f"• *Win Rate สะสม:* `{s['win_rate_pct']:.2f}%`",
        f"• กำไรขั้นต้นสะสม: `{s['total_gross_pnl']:+,.0f}` บาท",
        f"• *กำไรสุทธิสะสม (หัก Spread {SPREAD_THB:.0f} บ.):* `{s['total_net_pnl']:+,.0f}` บาท/บาททอง",
        f"• เฉลี่ยกำไรสุทธิต่อไม้: `{s['avg_net_pnl']:+,.0f}` บาท | Profit Factor: `{s['profit_factor']}`",
    ]

    if s["open_positions"]:
        lines.append(f"\n📂 *ไม้ที่กำลังถืออยู่ ({s['open_count']} ไม้):*")
        for p in s["open_positions"]:
            unreal_str = ""
            if current_price is not None:
                unreal_net = (current_price - p["entry_price"]) - SPREAD_THB
                unreal_str = f" | กำไรสุทธิตอนนี้: `{unreal_net:+,.0f}` บ."
            lines.append(
                f"  • ไม้ #{p['trade_id']}: ซื้อ @ `{p['entry_price']:,.0f}` บ. "
                f"(TP `{p['tp']:,.0f}` / SL `{p['sl']:,.0f}`){unreal_str}"
            )
    else:
        lines.append("\n📂 *ไม้ที่กำลังถืออยู่:* ไม่มีสถานะคงค้าง")

    if s["recent_trades"]:
        lines.append("\n📝 *5 ไม้ล่าสุดที่ปิดแล้ว:*")
        for t in s["recent_trades"]:
            icon = "✅" if t["net_pnl"] > 0 else "❌"
            lines.append(
                f"  {icon} #{t['trade_id']}: ซื้อ `{t['entry_price']:,.0f}` ➔ ขาย `{t['exit_price']:,.0f}` "
                f"| สุทธิ `{t['net_pnl']:+,.0f}` บ."
            )

    return "\n".join(lines)


def check_open_positions_alerts(current_price: float) -> list[tuple[dict, str]]:
    """
    Check if any open user position has reached TP (+260), Trailing Profit Lock (+190), or SL (-280).
    Updates trailing lock state in user_portfolio.json and returns list of (position, reason_text).
    """
    data = _load_data()
    alerts: list[tuple[dict, str]] = []
    changed = False

    for pos in data.get("open_positions", []):
        entry = float(pos["entry_price"])
        tp = float(pos["tp"])
        sl = float(pos["sl"])

        # Activate trailing lock at +190 THB gross (+110 THB locked)
        if current_price >= entry + 190.0 and not pos.get("trailing_locked", False):
            pos["sl"] = round(entry + 110.0, 2)
            pos["trailing_locked"] = True
            changed = True
            alerts.append(
                (
                    pos,
                    f"🔒 ราคาบวกทะลุ +190 บาทแล้ว! ระบบเลื่อนจุดล็อกกำไรขั้นต่ำของไม้ #{pos['trade_id']} ขึ้นมาที่ `{pos['sl']:,.0f}` บาท (หากต้องการขายทำกำไรเลย กดปุ่มขายด้านล่างได้ทันที)",
                )
            )
        elif current_price >= tp:
            alerts.append(
                (
                    pos,
                    f"🎯 *ถึงเป้าทำกำไร (TP) แล้ว!* ไม้ #{pos['trade_id']} (ซื้อไว้ @ `{entry:,.0f}` บ.) ราคาปัจจุบัน `{current_price:,.0f}` บ. แนะนำกดปุ่มขายทำกำไรด้านล่างครับ",
                )
            )
        elif current_price <= sl:
            reason = "จุดล็อกกำไร (Trailing Lock)" if pos.get("trailing_locked") else "จุดตัดขาดทุน (SL)"
            alerts.append(
                (
                    pos,
                    f"🛑 *ราคาแตะ{reason}แล้ว!* ไม้ #{pos['trade_id']} (ซื้อไว้ @ `{entry:,.0f}` บ.) ราคาปัจจุบัน `{current_price:,.0f}` บ. แนะนำกดปุ่มขายปิดสถานะด้านล่างครับ",
                )
            )

    if changed:
        _save_data(data)

    return alerts
