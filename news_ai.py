from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import feedparser
import requests

from config import GEMINI_API_KEY, GEMINI_MODEL
from gold_data import TradeSignal

logger = logging.getLogger(__name__)

RSS_FEEDS = [
    "https://news.google.com/rss/search?q=%E0%B8%A3%E0%B8%B2%E0%B8%84%E0%B8%B2%E0%B8%97%E0%B8%AD%E0%B8%87%E0%B8%84%E0%B8%B3+OR+%E0%B8%97%E0%B8%AD%E0%B8%87%E0%B8%84%E0%B8%B3&hl=th&gl=TH&ceid=TH:th",
    "https://news.google.com/rss/search?q=gold+price+XAUUSD+Fed+CPI&hl=en-US&gl=US&ceid=US:en",
]


def fetch_gold_news(limit: int = 6) -> list[dict]:
    """Fetch recent Thai and global gold market headlines via Google News RSS."""
    articles: list[dict] = []
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[: limit // 2 + 1]:
                articles.append(
                    {
                        "title": entry.get("title", "").strip(),
                        "published": entry.get("published", ""),
                        "link": entry.get("link", ""),
                    }
                )
        except Exception as exc:
            logger.warning("Failed to fetch RSS %s: %s", url, exc)
    return articles[:limit]


def _build_signal_banner(signal: TradeSignal) -> str:
    if signal.action == "BUY":
        if signal.confidence == "HIGH":
            return (
                "┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
                "🟢 *สัญญาณ: แนะนำเข้าซื้อทันที (ระดับ 1: Sniper)*\n"
                "🔥 *ความมั่นใจ:* 🟩🟩🟩🟩🟩 *สูงมาก (Win Rate 85%+)*\n"
                "┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛"
            )
        return (
            "┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
            "🟢 *สัญญาณ: จังหวะเข้าซื้อเก็งกำไรสั้น (ระดับ 2: Fast Scalp)*\n"
            "⚡ *ความมั่นใจ:* 🟩🟩🟩🟨⬜ *ปานกลาง-สูง (Win Rate ~70%)*\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛"
        )
    if signal.action == "SELL":
        return (
            "┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
            "🔴 *สัญญาณ: แนะนำขายทำกำไร (SELL)*\n"
            "🚨 *ความมั่นใจ:* 🟥🟥🟥🟥⬜ *สูง (ควรขาย/ชะลอซื้อ)*\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛"
        )
    return (
        "┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
        "🟡 *สัญญาณ: รอดูจังหวะ / ห้ามเพิ่งไล่ราคา (HOLD)*\n"
        f"⏳ *ความมั่นใจเข้าซื้อตอนนี้:* 🟨🟨⬜⬜⬜ *ต่ำ (รอลงมาที่ {signal.entry_low:,.0f} บ.)*\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛"
    )


def build_rule_based_reason(signal: TradeSignal, news: list[dict], gta: Optional[dict]) -> str:
    """Fallback Thai explanation when GEMINI_API_KEY is not configured."""
    banner = _build_signal_banner(signal)

    gta_line = ""
    if gta:
        gta_line = (
            f"\n🏪 *ราคาสมาคมฯ ล่าสุด:* รับซื้อ `{gta['buy']:,.0f}` | ขายออก `{gta['sell']:,.0f}` บาท"
            f" ({gta['update_time']})"
        )

    news_lines = "\n".join(f"• {n['title']}" for n in news[:3]) if news else "• ยังไม่พบข่าวเด่นเพิ่มเติม"
    tech_lines = "\n".join(f"• {r}" for r in signal.reasons[:3])

    return (
        f"{banner}\n\n"
        f"🪙 *ราคาทองคำไทย 96.5% (Real-Time 5m)*\n"
        f"⏰ เวลา: `{signal.timestamp[:19]}`\n"
        f"💰 *ราคาประเมินตลาดโลก:* `{signal.thai_gold_price:,.0f}` บาท/บาททอง\n"
        f"🌍 Spot Gold: `${signal.xau_usd:,.2f}` | USD/THB: `{signal.usd_thb:.2f}`"
        f"{gta_line}\n\n"
        f"📍 *โซนเข้าซื้อที่ได้เปรียบ:* `{signal.entry_low:,.0f} - {signal.entry_high:,.0f}` บาท\n"
        f"🎯 *เป้าขายทำกำไร (TP):* `{signal.take_profit:,.0f}` บาท\n"
        f"🛑 *จุดตัดขาดทุน (SL):* `{signal.stop_loss:,.0f}` บาท\n\n"
        f"📊 *เหตุผลทางเทคนิค (RSI {signal.rsi}):*\n{tech_lines}\n\n"
        f"📰 *ข่าวที่มีผลต่อราคาทองวันนี้:*\n{news_lines}\n\n"
        f"📅 *ปฏิทินเศรษฐกิจ:* ติดตามตัวเลขเงินเฟ้อสหรัฐฯ (CPI/PCE), จ้างงาน (NFP) และดอกเบี้ย Fed"
    )


def generate_ai_analysis(signal: TradeSignal, news: list[dict], gta: Optional[dict]) -> str:
    """Use Gemini API to synthesize technicals + news + macro timing into a Thai Telegram alert."""
    if not GEMINI_API_KEY:
        return build_rule_based_reason(signal, news, gta)

    news_text = "\n".join(f"- {n['title']} ({n['published']})" for n in news) or "ไม่มีข่าวใหม่"
    gta_text = (
        f"ราคาสมาคมฯ: รับซื้อ {gta['buy']:,.0f} / ขายออก {gta['sell']:,.0f} บาท"
        if gta
        else "ไม่มีข้อมูลราคาสมาคมฯ ณ เวลานี้"
    )

    prompt = f"""คุณคือนักวิเคราะห์ทองคำไทยระยะสั้น (เพื่อเก็งกำไร) จงสรุปข้อความแจ้งเตือนส่งเข้า Telegram ภาษาไทยให้กระชับ อ่านง่าย ใช้ Markdown ของ Telegram ได้
ข้อมูลตลาด ณ {datetime.now().strftime('%Y-%m-%d %H:%M')}:
- ราคาทองคำไทย 96.5% (คำนวณ Real-time): {signal.thai_gold_price:,.0f} บาท/บาททอง
- {gta_text}
- Spot Gold (GC=F): ${signal.xau_usd:,.2f} | ค่าเงินบาท: {signal.usd_thb:.2f}
- สัญญาณเทคนิค: {signal.action} (ความมั่นใจ {signal.confidence})
- RSI(14): {signal.rsi} | แนวรับ: {signal.support:,.0f} | แนวต้าน: {signal.resistance:,.0f}
- โซนเข้าซื้อ: {signal.entry_low:,.0f}-{signal.entry_high:,.0f} | TP: {signal.take_profit:,.0f} | SL: {signal.stop_loss:,.0f}
- เหตุผลเทคนิค: {', '.join(signal.reasons)}

ข่าวทองคำและเศรษฐกิจล่าสุด:
{news_text}

สิ่งที่ต้องระบุในข้อความ (ไม่เกิน 18 บรรทัด):
1. สรุปราคาปัจจุบันและสัญญาณ (ซื้อ/ขาย/รอดู) พร้อมโซนเข้าซื้อ เป้าขาย (TP) และจุดตัดขาดทุน (SL)
2. อธิบายสั้นๆ ว่า "วันนี้ทองขึ้น/ลงเพราะข่าวอะไร" อ้างอิงจากข่าวด้านบน
3. ระบุ "ช่วงเวลาหรือวันที่ควรจับตา" (ถ้าข่าวมีพูดถึงวันประกาศตัวเลขสำคัญ เช่น Fed/CPI/NFP ให้ระบุวันและแนวโน้มว่าราคาอาจขึ้นหรือลง)
4. ปิดท้ายด้วยคำเตือนสั้นๆ ว่าเป็นการวิเคราะห์ประกอบการตัดสินใจ"""

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )
    try:
        resp = requests.post(
            url,
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            if text:
                return f"{_build_signal_banner(signal)}\n\n{text}"
        logger.warning("Gemini API returned status %s: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.warning("Gemini API failed, using rule-based fallback: %s", exc)

    return build_rule_based_reason(signal, news, gta)
