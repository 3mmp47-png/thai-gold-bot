from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from portfolio_tracker import (
    format_stats_message,
    get_open_positions,
    record_buy,
    record_sell,
)

logger = logging.getLogger(__name__)


def build_trading_keyboard(
    current_price: float,
    tp: Optional[float] = None,
    sl: Optional[float] = None,
    trade_id: Optional[int] = None,
    action: str = "HOLD",
    confidence: str = "LOW",
    entry_low: Optional[float] = None,
) -> dict:
    """
    Create full-width (100% screen width) Telegram InlineKeyboardMarkup:
    - Row 1: Giant Signal & Confidence Status Banner Button
    - Row 2: Giant Full-Width BUY Button (for recording manual trade)
    - Row 3: Giant Full-Width SELL Button (for closing manual trade & calculating stats)
    - Row 4: Portfolio Stats & Refresh Buttons
    """
    p_int = int(round(current_price))
    tp_int = int(round(tp)) if tp else p_int + 260
    sl_int = int(round(sl)) if sl else p_int - 280
    wait_int = int(round(entry_low)) if entry_low else p_int - 150

    buy_cb = f"buy:{p_int}:{tp_int}:{sl_int}"
    sell_cb = f"sell:{p_int}:{trade_id}" if trade_id is not None else f"sell:{p_int}"

    if action == "BUY":
        banner_text = "🔥🟢 มั่นใจสูง (85%+): แนะนำเข้าซื้อตอนนี้! 🟢🔥"
        buy_label = f"🟢🛒 กดซื้อ (@ {p_int:,} บ.) — บันทึกไม้ซื้อ"
    elif action == "SELL":
        banner_text = "🚨🔴 มั่นใจสูง: แนะนำขายทำกำไรตอนนี้! 🔴🚨"
        buy_label = f"🟢🛒 กดซื้อ (@ {p_int:,} บ.) — บันทึกไม้ซื้อ"
    else:
        banner_text = f"⏳🟡 ความมั่นใจต่ำ: รอย่อแถว {wait_int:,} บ. ค่อยซื้อ 🟡⏳"
        buy_label = f"🟢🛒 กดซื้อ (@ {p_int:,} บ.) — บันทึกไม้ซื้อ"

    if trade_id is not None:
        sell_label = f"🔴💰 กดขายปิดไม้ #{trade_id} (@ {p_int:,} บ.) — คิดกำไรทันที"
    elif action == "SELL":
        sell_label = f"🔴💰 กดขายทำกำไร (@ {p_int:,} บ.) — แนะนำขาย!"
    else:
        sell_label = f"🔴💰 กดขาย / ปิดไม้ (@ {p_int:,} บ.) — คิดกำไรสุทธิ"

    return {
        "inline_keyboard": [
            [{"text": banner_text, "callback_data": "check_now"}],
            [{"text": buy_label, "callback_data": buy_cb}],
            [{"text": sell_label, "callback_data": sell_cb}],
            [
                {"text": "📊 ดูสถิติพอร์ตของฉัน", "callback_data": "stats"},
                {"text": "🔄 อัปเดตราคาตอนนี้", "callback_data": "check_now"},
            ],
        ]
    }


def auto_discover_chat_id() -> str:
    """If TELEGRAM_BOT_TOKEN is set but TELEGRAM_CHAT_ID is empty, try discovering it from /start."""
    if TELEGRAM_CHAT_ID:
        return TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN:
        return ""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        resp = requests.get(url, timeout=8)
        if resp.status_code == 200:
            results = resp.json().get("result", [])
            for item in reversed(results):
                msg = item.get("message") or item.get("callback_query", {}).get("message")
                if msg and "chat" in msg:
                    cid = str(msg["chat"]["id"])
                    logger.info("Auto-discovered TELEGRAM_CHAT_ID: %s", cid)
                    _persist_chat_id_to_env(cid)
                    return cid
    except Exception as exc:
        logger.debug("Chat ID auto-discovery failed: %s", exc)
    return ""


def _persist_chat_id_to_env(chat_id: str) -> None:
    global TELEGRAM_CHAT_ID
    TELEGRAM_CHAT_ID = chat_id
    env_path = Path(__file__).parent / ".env"
    try:
        if env_path.exists():
            content = env_path.read_text(encoding="utf-8")
            if "TELEGRAM_CHAT_ID=" in content:
                lines = []
                for line in content.splitlines():
                    if line.startswith("TELEGRAM_CHAT_ID="):
                        lines.append(f"TELEGRAM_CHAT_ID={chat_id}")
                    else:
                        lines.append(line)
                env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                return
        with open(env_path, "a", encoding="utf-8") as f:
            f.write(f"\nTELEGRAM_CHAT_ID={chat_id}\n")
    except Exception as exc:
        logger.debug("Could not write TELEGRAM_CHAT_ID to .env: %s", exc)


def send_telegram_message(
    text: str,
    dry_run: bool = False,
    reply_markup: Optional[dict] = None,
    chat_id_override: Optional[str] = None,
) -> bool:
    """Send a message to Telegram via Bot API with optional Inline Keyboard buttons."""
    target_chat_id = chat_id_override or TELEGRAM_CHAT_ID or auto_discover_chat_id()

    if dry_run or not TELEGRAM_BOT_TOKEN or not target_chat_id:
        print("\n" + "=" * 60)
        print("[TELEGRAM PREVIEW / DRY-RUN]")
        print("=" * 60)
        print(text)
        if reply_markup and "inline_keyboard" in reply_markup:
            print("-" * 60)
            print("ปุ่มกดบน Telegram (Inline Buttons):")
            for row in reply_markup["inline_keyboard"]:
                btn_texts = [f"[ {b['text']} ]" for b in row]
                print("  " + "  ".join(btn_texts))
        print("=" * 60 + "\n")
        if not dry_run and not TELEGRAM_BOT_TOKEN:
            logger.warning("ยังไม่ได้ใส่ TELEGRAM_BOT_TOKEN ในไฟล์ .env")
        elif not dry_run and not target_chat_id:
            logger.warning("ยังไม่พบ TELEGRAM_CHAT_ID (ให้ทัก /start ไปที่บอทใน Telegram แล้วรันใหม่)")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload: dict = {
        "chat_id": target_chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            logger.info("Telegram alert sent successfully.")
            return True
        if resp.status_code == 400:
            payload.pop("parse_mode", None)
            resp2 = requests.post(url, json=payload, timeout=10)
            if resp2.status_code == 200:
                logger.info("Telegram alert sent (plain text fallback).")
                return True
        logger.error("Telegram API error %s: %s", resp.status_code, resp.text)
    except Exception as exc:
        logger.error("Failed to send Telegram message: %s", exc)
    return False


def _answer_callback(callback_query_id: str, text: str) -> None:
    if not TELEGRAM_BOT_TOKEN:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id, "text": text},
            timeout=5,
        )
    except Exception:
        pass


def poll_telegram_updates(
    get_current_price_fn: Callable[[], float],
    trigger_analysis_fn: Callable[[str], None],
    stop_event=None,
) -> None:
    """
    Long-poll Telegram getUpdates to handle manual button taps (Buy / Sell / Stats / Check Now)
    and text commands (/start, /check, /stats).
    """
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("Cannot start Telegram button listener: TELEGRAM_BOT_TOKEN is empty.")
        return

    offset: Optional[int] = None
    base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    logger.info("Telegram interactive button listener started (ready for Buy/Sell button taps)...")

    while stop_event is None or not stop_event.is_set():
        try:
            params: dict = {"timeout": 15}
            if offset is not None:
                params["offset"] = offset
            resp = requests.get(base_url, params=params, timeout=20)
            if resp.status_code != 200:
                continue

            updates = resp.json().get("result", [])
            for upd in updates:
                offset = upd["update_id"] + 1

                # 1. Handle Inline Button Taps (callback_query)
                if "callback_query" in upd:
                    cq = upd["callback_query"]
                    cq_id = cq["id"]
                    data = cq.get("data", "")
                    chat_id = str(cq.get("message", {}).get("chat", {}).get("id", TELEGRAM_CHAT_ID))

                    if data.startswith("buy:"):
                        parts = data.split(":")
                        price = float(parts[1]) if len(parts) > 1 else get_current_price_fn()
                        tp = float(parts[2]) if len(parts) > 2 else price + 260.0
                        sl = float(parts[3]) if len(parts) > 3 else price - 280.0
                        pos = record_buy(price=price, tp=tp, sl=sl)
                        _answer_callback(cq_id, f"✅ บันทึกไม้ซื้อ #{pos['trade_id']} @ {price:,.0f} บ.")

                        msg = (
                            f"✅ *บันทึกการเข้าซื้อสำเร็จ (ไม้ที่ #{pos['trade_id']})*\n"
                            f"⏰ เวลาเข้าซื้อ: `{pos['entry_time']}`\n"
                            f"💰 ราคาเข้าซื้อ: `{pos['entry_price']:,.0f}` บาท/บาททอง\n"
                            f"🎯 เป้าขายทำกำไร (TP): `{pos['tp']:,.0f}` บาท (+260)\n"
                            f"🔒 จุดล็อกกำไรอัตโนมัติ: เมื่อแตะ `{pos['entry_price'] + 190:,.0f}` บาท\n"
                            f"🛑 จุดตัดขาดทุน (SL): `{pos['sl']:,.0f}` บาท (-280)\n\n"
                            f"💡 *ระบบกำลังช่วยเฝ้าราคาให้คุณ เมื่อถึงจุดขายบอทจะแจ้งเตือนทันทีครับ*"
                        )
                        kb = build_trading_keyboard(price, tp, sl, trade_id=pos["trade_id"])
                        send_telegram_message(msg, reply_markup=kb, chat_id_override=chat_id)

                    elif data.startswith("sell:"):
                        parts = data.split(":")
                        live_p = get_current_price_fn()
                        price = live_p if live_p > 0 else (float(parts[1]) if len(parts) > 1 else 0.0)
                        trade_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None

                        closed = record_sell(exit_price=price, trade_id=trade_id)
                        if closed is None:
                            _answer_callback(cq_id, "⚠️ ไม่มีไม้ซื้อที่เปิดค้างอยู่")
                            send_telegram_message(
                                "⚠️ *ไม่พบไม้ที่ถือค้างอยู่* (กรุณากดปุ่ม `🟢🛒 กดซื้อ` เพื่อบันทึกไม้ก่อนครับ)",
                                chat_id_override=chat_id,
                            )
                        else:
                            icon = "🎉 กำไร" if closed["net_pnl"] > 0 else "🔻 ขาดทุน"
                            _answer_callback(
                                cq_id,
                                f"✅ ปิดไม้ #{closed['trade_id']} สุทธิ {closed['net_pnl']:+,.0f} บ.",
                            )
                            stats_msg = format_stats_message(current_price=price)
                            msg = (
                                f"🧾 *บันทึกการขายปิดไม้ #{closed['trade_id']} สำเร็จ!*\n"
                                f"• ราคาซื้อ: `{closed['entry_price']:,.0f}` บ. ({closed['entry_time']})\n"
                                f"• ราคาขาย: `{closed['exit_price']:,.0f}` บ. ({closed['exit_time']})\n"
                                f"• ส่วนต่างราคา: `{closed['gross_pnl']:+,.0f}` บ. (หัก Spread `{closed['spread_cost']:.0f}` บ.)\n"
                                f"• *ผลลัพธ์สุทธิ:* {icon} `{closed['net_pnl']:+,.0f}` บาท/บาททอง\n\n"
                                f"{stats_msg}"
                            )
                            kb = build_trading_keyboard(price)
                            send_telegram_message(msg, reply_markup=kb, chat_id_override=chat_id)

                    elif data == "stats":
                        _answer_callback(cq_id, "📊 กำลังดึงสถิติพอร์ตของคุณ...")
                        live_p = get_current_price_fn()
                        open_pos = get_open_positions()
                        latest_id = open_pos[-1]["trade_id"] if open_pos else None
                        kb = build_trading_keyboard(live_p, trade_id=latest_id)
                        send_telegram_message(
                            format_stats_message(current_price=live_p),
                            reply_markup=kb,
                            chat_id_override=chat_id,
                        )

                    elif data == "check_now":
                        _answer_callback(cq_id, "🔄 กำลังวิเคราะห์ราคาทองและข่าวล่าสุด...")
                        trigger_analysis_fn(chat_id)

                # 2. Handle Chat Text Commands (/start, /check, /stats, or any message)
                elif "message" in upd:
                    msg_obj = upd["message"]
                    chat_id = str(msg_obj.get("chat", {}).get("id", ""))
                    text = (msg_obj.get("text") or "").strip()

                    is_first_link = False
                    if chat_id and not TELEGRAM_CHAT_ID:
                        _persist_chat_id_to_env(chat_id)
                        is_first_link = True

                    if is_first_link or text.startswith("/start") or text.startswith("/help"):
                        _persist_chat_id_to_env(chat_id)
                        live_p = get_current_price_fn()
                        welcome = (
                            f"👋 *บอทผู้ช่วยวิเคราะห์และแจ้งเตือนทองคำไทย 96.5% (Timeframe 5m)*\n\n"
                            f"*วิธีใช้งาน:*\n"
                            f"• บอทจะวิเคราะห์กราฟ+ข่าว และแจ้งเตือนเมื่อถึงจุดเข้าซื้อที่มีความมั่นใจสูง\n"
                            f"• กด `🟢🛒 กดซื้อ` เมื่อคุณซื้อทองจริง เพื่อบันทึกต้นทุนและให้บอทช่วยเฝ้าเตือนจุดขาย\n"
                            f"• กด `🔴💰 กดขาย` เมื่อคุณขายทอง เพื่อคำนวณกำไรสุทธิ (หัก Spread) และเก็บสถิติ Win Rate สะสม"
                        )
                        kb = build_trading_keyboard(live_p)
                        send_telegram_message(welcome, reply_markup=kb, chat_id_override=chat_id)
                        trigger_analysis_fn(chat_id)
                    elif text.startswith("/stats"):
                        live_p = get_current_price_fn()
                        kb = build_trading_keyboard(live_p)
                        send_telegram_message(
                            format_stats_message(current_price=live_p),
                            reply_markup=kb,
                            chat_id_override=chat_id,
                        )
                    else:
                        trigger_analysis_fn(chat_id)

        except Exception as exc:
            logger.debug("Polling cycle exception: %s", exc)
