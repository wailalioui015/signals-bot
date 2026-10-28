import os
import time
import json
import logging
import threading
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
from http.server import HTTPServer, BaseHTTPRequestHandler

import requests
import telebot
from telebot import types

# SignalsBot v3.0
# Rule-based technical signals. AI is not allowed to invent entry/SL/TP values.
# Set API keys as environment variables; never hard-code secrets.

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s"
)

TELEGRAM_TOKEN = os.getenv("SIGNALS_BOT_TOKEN")
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY")
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

TIMEZONE = ZoneInfo(os.getenv("BOT_TIMEZONE", "Africa/Algiers"))
DB_PATH = os.getenv("DATABASE_PATH", "signals_bot.sqlite3")
HTTP_TIMEOUT = 12
SESSION = requests.Session()

if not TELEGRAM_TOKEN:
    raise RuntimeError("Missing required environment variable: SIGNALS_BOT_TOKEN")

bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=True)

MARKETS = {
    "forex": {
        "name": "💱 الفوركس",
        "pairs": ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", "USD/CAD"],
        "news_countries": ["USD", "EUR", "GBP", "JPY"],
    },
    "crypto": {
        "name": "🪙 العملات الرقمية",
        "pairs": ["BTC/USD", "ETH/USD", "SOL/USD", "BNB/USD", "XRP/USD", "ADA/USD"],
        "news_countries": ["crypto"],
    },
    "gold": {
        "name": "🥇 الذهب والسلع",
        "pairs": ["XAU/USD", "XAG/USD", "WTI/USD"],
        "news_countries": ["USD"],
    },
    "indices": {
        "name": "📊 المؤشرات",
        "pairs": ["NDX", "SPX", "DJI", "FTSE", "DAX", "NKY"],
        "news_countries": ["USD", "EUR", "GBP"],
    },
    "all": {
        "name": "🌍 جميع الأسواق",
        "pairs": ["EUR/USD", "GBP/USD", "BTC/USD", "ETH/USD", "XAU/USD", "NDX"],
        "news_countries": ["USD", "EUR", "GBP"],
    },
}

# -------------------- Persistence --------------------

def db_connect():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db_connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS subscribers (
                user_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL DEFAULT '',
                market TEXT NOT NULL DEFAULT 'all',
                subscribed INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sent_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry REAL NOT NULL,
                stop_loss REAL NOT NULL,
                take_profit REAL NOT NULL,
                created_at TEXT NOT NULL,
                outcome TEXT NOT NULL DEFAULT 'pending'
            )
        """)

def save_user(user_id, name="", market=None, subscribed=None):
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM subscribers WHERE user_id=?", (user_id,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO subscribers(user_id,name,market,subscribed,created_at) VALUES(?,?,?,?,?)",
                (user_id, name or "", market or "all",
                 1 if subscribed is None else int(subscribed), datetime.now(TIMEZONE).isoformat())
            )
        else:
            conn.execute(
                "UPDATE subscribers SET name=?, market=?, subscribed=? WHERE user_id=?",
                (name if name is not None else row["name"],
                 market if market is not None else row["market"],
                 row["subscribed"] if subscribed is None else int(subscribed),
                 user_id)
            )

def get_user(user_id):
    with db_connect() as conn:
        return conn.execute("SELECT * FROM subscribers WHERE user_id=?", (user_id,)).fetchone()

def get_subscribers():
    with db_connect() as conn:
        return conn.execute(
            "SELECT user_id, name, market FROM subscribers WHERE subscribed=1"
        ).fetchall()

def log_signal(signal):
    with db_connect() as conn:
        conn.execute("""
            INSERT INTO sent_signals(symbol,timeframe,direction,entry,stop_loss,take_profit,created_at)
            VALUES(?,?,?,?,?,?,?)
        """, (signal["symbol"], "15min", signal["direction"], signal["entry"],
              signal["sl"], signal["tp2"], datetime.now(TIMEZONE).isoformat()))

# -------------------- HTTP keep-alive --------------------

class KeepAlive(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"SignalsBot v3.0 OK"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        return

def start_keep_alive():
    def serve():
        port = int(os.getenv("PORT", "8081"))
        try:
            HTTPServer(("0.0.0.0", port), KeepAlive).serve_forever()
        except OSError:
            logging.exception("Keep-alive server failed")
    threading.Thread(target=serve, daemon=True).start()

# -------------------- Market data --------------------

def fetch_candles(symbol, interval, outputsize=100):
    if not TWELVEDATA_API_KEY:
        raise RuntimeError("TWELVEDATA_API_KEY غير مضبوط")
    url = "https://api.twelvedata.com/time_series"
    response = SESSION.get(url, params={
        "symbol": symbol,
        "interval": interval,
        "outputsize": outputsize,
        "apikey": TWELVEDATA_API_KEY,
        "format": "JSON",
    }, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    data = response.json()
    if "values" not in data or not data["values"]:
        message = data.get("message") or data.get("code") or "لا توجد شموع في الاستجابة"
        raise ValueError(f"{symbol} {interval}: {message}")
    candles = []
    for item in reversed(data["values"]):  # oldest -> newest
        try:
            candle = {k: float(item[k]) for k in ("open", "high", "low", "close")}
            candle["datetime"] = item.get("datetime", "")
            if candle["high"] < candle["low"]:
                continue
            candles.append(candle)
        except (KeyError, TypeError, ValueError):
            continue
    if len(candles) < 55:
        raise ValueError(f"بيانات {symbol} غير كافية: {len(candles)} شمعة")
    return candles

def ema(values, period):
    if len(values) < period:
        return None
    alpha = 2.0 / (period + 1)
    value = sum(values[:period]) / period
    for price in values[period:]:
        value = alpha * price + (1 - alpha) * value
    return value

def rsi(values, period=14):
    if len(values) < period + 1:
        return None
    gains, losses = [], []
    for i in range(len(values) - period, len(values)):
        delta = values[i] - values[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def atr(candles, period=14):
    if len(candles) < period + 1:
        return None
    ranges = []
    for i in range(1, len(candles)):
        cur, prev = candles[i], candles[i - 1]
        ranges.append(max(
            cur["high"] - cur["low"],
            abs(cur["high"] - prev["close"]),
            abs(cur["low"] - prev["close"])
        ))
    return sum(ranges[-period:]) / period

def fmt_price(value):
    if value >= 1000:
        return f"{value:.2f}"
    if value >= 10:
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return f"{value:.5f}".rstrip("0").rstrip(".")

def analyze_symbol(symbol):
    # M15 is used for the candidate entry; H1 confirms direction only.
    m15 = fetch_candles(symbol, "15min", 100)
    h1 = fetch_candles(symbol, "1h", 100)
    # Exclude the newest candle because it may still be forming.
    if len(m15) > 55:
        m15 = m15[:-1]
    if len(h1) > 55:
        h1 = h1[:-1]
    mclose = [c["close"] for c in m15]
    hclose = [c["close"] for c in h1]

    entry = mclose[-1]
    m15_ema20 = ema(mclose[-60:], 20)
    h1_ema20 = ema(hclose[-80:], 20)
    h1_ema50 = ema(hclose[-80:], 50)
    rsi_value = rsi(mclose, 14)
    atr_value = atr(m15, 14)

    if None in (m15_ema20, h1_ema20, h1_ema50, rsi_value, atr_value) or atr_value <= 0:
        return {"symbol": symbol, "direction": "WAIT", "reason": "بيانات المؤشرات غير كافية"}

    # Conservative confirmation: H1 trend + M15 price/EMA + RSI filter.
    bullish = h1_ema20 > h1_ema50 and entry > m15_ema20 and 50 <= rsi_value <= 68
    bearish = h1_ema20 < h1_ema50 and entry < m15_ema20 and 32 <= rsi_value <= 50

    common = {
        "symbol": symbol, "entry": entry, "rsi": rsi_value, "atr": atr_value,
        "m15_ema20": m15_ema20, "h1_ema20": h1_ema20, "h1_ema50": h1_ema50,
        "last_candle_time": m15[-1].get("datetime", ""),
    }

    if bullish:
        sl = entry - 1.5 * atr_value
        tp1 = entry + 2.0 * (entry - sl)
        tp2 = entry + 3.0 * (entry - sl)
        return {**common, "direction": "BUY", "sl": sl, "tp1": tp1, "tp2": tp2,
                "rr": 3.0, "reason": "اتجاه H1 صاعد والسعر فوق EMA20 على M15 وRSI ضمن النطاق المحدد"}
    if bearish:
        sl = entry + 1.5 * atr_value
        tp1 = entry - 2.0 * (sl - entry)
        tp2 = entry - 3.0 * (sl - entry)
        return {**common, "direction": "SELL", "sl": sl, "tp1": tp1, "tp2": tp2,
                "rr": 3.0, "reason": "اتجاه H1 هابط والسعر تحت EMA20 على M15 وRSI ضمن النطاق المحدد"}
    return {**common, "direction": "WAIT",
            "reason": "شروط الاتجاه والزخم لم تتوافق؛ لا توجد إشارة وفق القواعد الحالية"}

def get_news_for_market(market_key):
    market = MARKETS.get(market_key, MARKETS["all"])
    try:
        if "crypto" in market["news_countries"]:
            response = SESSION.get("https://api.coingecko.com/api/v3/news", timeout=HTTP_TIMEOUT)
            response.raise_for_status()
            data = response.json()
            result = []
            for item in data.get("data", [])[:5]:
                result.append({
                    "title": item.get("title", ""),
                    "country": "Crypto",
                    "date": str(item.get("updated_at", ""))[:10],
                })
            return result
        response = SESSION.get(
            "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            timeout=HTTP_TIMEOUT
        )
        response.raise_for_status()
        data = response.json()
        today = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
        return [
            event for event in data
            if event.get("impact") == "High"
            and today in str(event.get("date", ""))
            and event.get("country", "") in market["news_countries"]
        ][:5]
    except Exception:
        logging.exception("News fetch failed for %s", market_key)
        return []

# -------------------- Signal formatting --------------------

def generate_signals(market_key):
    market = MARKETS.get(market_key, MARKETS["all"])
    results = []
    for symbol in market["pairs"]:
        try:
            signal = analyze_symbol(symbol)
            results.append(signal)
            time.sleep(0.15)
        except Exception as exc:
            logging.warning("Analysis failed for %s: %s", symbol, exc)
            results.append({
                "symbol": symbol, "direction": "WAIT",
                "reason": f"تعذر التحليل أو بيانات الرمز غير متاحة ({str(exc)[:100]})"
            })

    # Never force three trades. Publish only rule-qualified signals; otherwise WAIT.
    qualified = [s for s in results if s.get("direction") in ("BUY", "SELL")]
    if not qualified:
        return "⏸️ لا توجد إشارة تستوفي الشروط حاليًا.\n\n" + "\n".join(
            f"• {s['symbol']}: WAIT — {s.get('reason', '')}" for s in results
        )

    lines = []
    for s in qualified[:3]:
        lines.extend([
            "━━━━━━━━━━━━━━━━━━━━",
            f"📊 الزوج: {s['symbol']}",
            f"✅ الإشارة: {'BUY 🟢' if s['direction'] == 'BUY' else 'SELL 🔴'}",
            "💪 التقييم: شروط فنية متوافقة (ليس احتمال نجاح)",
            f"🎯 الدخول المرجعي: {fmt_price(s['entry'])}",
            f"🎯 TP1: {fmt_price(s['tp1'])}",
            f"🎯 TP2: {fmt_price(s['tp2'])}",
            f"🛡️ SL: {fmt_price(s['sl'])}",
            f"⚖️ R:R إلى TP2: 1:{s['rr']:.1f}",
            f"📉 RSI(14) M15: {s['rsi']:.1f}",
            f"🧭 السبب: {s['reason']}",
            f"🕯️ آخر شمعة M15 من المصدر: {s.get('last_candle_time', 'غير متاح')}",
            "━━━━━━━━━━━━━━━━━━━━",
        ])
        try:
            log_signal(s)
        except Exception:
            logging.exception("Could not log signal")
    return "\n".join(lines) + "\n\n⚠️ تعليمي فقط؛ الأسعار قد تتأخر، ولا توجد ضمانات للنتائج."

def send_signals_to_all(session_name):
    logging.info("Generating scheduled signals: %s", session_name)
    # Compute once per market, then reuse; avoids a fresh API/AI call per subscriber.
    cache = {}
    for market_key in set(["all"] + [row["market"] for row in get_subscribers()]):
        cache[market_key] = generate_signals(market_key)

    now = datetime.now(TIMEZONE).strftime("%Y-%m-%d %H:%M %Z")
    if CHANNEL_ID and cache.get("all"):
        try:
            bot.send_message(CHANNEL_ID,
                f"🎯 {session_name}\n📅 {now}\n\n{cache['all']}",
                disable_web_page_preview=True)
        except Exception:
            logging.exception("Failed to send scheduled message to channel")

    for row in get_subscribers():
        try:
            market_key = row["market"] if row["market"] in MARKETS else "all"
            bot.send_message(row["user_id"],
                f"🎯 {session_name} — {MARKETS[market_key]['name']}\n📅 {now}\n\n{cache.get(market_key, cache['all'])}",
                disable_web_page_preview=True)
            time.sleep(0.08)
        except Exception as exc:
            logging.warning("Could not message subscriber %s: %s", row["user_id"], exc)

# -------------------- Menus --------------------

def main_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("📊 صفقات الآن", "📰 أخبار اليوم")
    kb.row("💹 أسعار السوق", "📈 إحصائيات")
    kb.row("🎯 اختيار السوق", "🔔 اشتراك")
    kb.row("🔕 إلغاء الاشتراك")
    return kb

def market_keyboard(prefix):
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("💱 فوركس", callback_data=f"{prefix}_forex"),
        types.InlineKeyboardButton("🪙 كريبتو", callback_data=f"{prefix}_crypto"),
    )
    kb.row(
        types.InlineKeyboardButton("🥇 الذهب والسلع", callback_data=f"{prefix}_gold"),
        types.InlineKeyboardButton("📊 المؤشرات", callback_data=f"{prefix}_indices"),
    )
    kb.row(types.InlineKeyboardButton("🌍 الكل", callback_data=f"{prefix}_all"))
    return kb

@bot.message_handler(commands=["start"])
def start(message):
    user = message.from_user
    save_user(user.id, user.first_name or "", subscribed=1)
    bot.reply_to(
        message,
        "السلام عليكم 👋\nأنا SignalsBot v3.0.\n"
        "أستخدم قواعد فنية واضحة: اتجاه H1 للتأكيد وM15 للإشارة.\n"
        "لا أضمن الأرباح، وقد لا تتوفر بيانات لبعض الرموز.\n\nاختر من القائمة:",
        reply_markup=main_keyboard()
    )

@bot.message_handler(func=lambda m: m.text == "🔔 اشتراك")
def subscribe(message):
    user = get_user(message.from_user.id)
    market = user["market"] if user else "all"
    save_user(message.from_user.id, message.from_user.first_name or "", market=market, subscribed=1)
    bot.reply_to(message, "✅ تم تفعيل الاشتراك في الإشارات التلقائية.", reply_markup=main_keyboard())

@bot.message_handler(func=lambda m: m.text == "🔕 إلغاء الاشتراك")
def unsubscribe(message):
    user = get_user(message.from_user.id)
    if user:
        save_user(message.from_user.id, subscribed=0)
    bot.reply_to(message, "تم إيقاف الإشارات التلقائية. يمكنك طلب التحليل يدويًا في أي وقت.", reply_markup=main_keyboard())

@bot.message_handler(func=lambda m: m.text == "🎯 اختيار السوق")
def choose_market(message):
    user = get_user(message.from_user.id)
    current = MARKETS[user["market"] if user and user["market"] in MARKETS else "all"]["name"]
    bot.reply_to(message, f"اختر السوق المفضل. الحالي: {current}", reply_markup=market_keyboard("market"))

@bot.message_handler(func=lambda m: m.text == "📊 صفقات الآن")
def signals_now(message):
    bot.reply_to(message, "اختر السوق للتحليل:", reply_markup=market_keyboard("sig"))

@bot.message_handler(func=lambda m: m.text == "📰 أخبار اليوم")
def news_today(message):
    bot.reply_to(message, "اختر سوق الأخبار:", reply_markup=market_keyboard("news"))

@bot.message_handler(func=lambda m: m.text == "💹 أسعار السوق")
def prices_now(message):
    bot.reply_to(message, "اختر السوق لعرض الأسعار:", reply_markup=market_keyboard("prices"))

@bot.message_handler(func=lambda m: m.text == "📈 إحصائيات")
def stats(message):
    with db_connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM subscribers WHERE subscribed=1").fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM sent_signals").fetchone()[0]
    now = datetime.now(TIMEZONE)
    bot.reply_to(message,
        f"📈 إحصائيات SignalsBot v3.0\n👥 المشتركون النشطون: {count}\n"
        f"🧾 الإشارات المسجلة: {total}\n🕒 الوقت: {now:%Y-%m-%d %H:%M %Z}",
        reply_markup=main_keyboard())

@bot.callback_query_handler(func=lambda c: c.data.startswith(("market_", "sig_", "news_", "prices_")))
def callbacks(call):
    try:
        prefix, market_key = call.data.split("_", 1)
        if market_key not in MARKETS:
            bot.answer_callback_query(call.id, "اختيار غير صالح")
            return
        bot.answer_callback_query(call.id, "جارٍ التنفيذ...")
        chat_id = call.message.chat.id

        if prefix == "market":
            user = get_user(call.from_user.id)
            save_user(call.from_user.id, call.from_user.first_name or "",
                      market=market_key, subscribed=(user["subscribed"] if user else 1))
            bot.send_message(chat_id, f"✅ تم اختيار {MARKETS[market_key]['name']}", reply_markup=main_keyboard())
        elif prefix == "sig":
            msg = bot.send_message(chat_id, f"⏳ جارٍ تحليل {MARKETS[market_key]['name']}...")
            result = generate_signals(market_key)
            bot.edit_message_text(result, chat_id, msg.message_id, disable_web_page_preview=True)
        elif prefix == "news":
            news = get_news_for_market(market_key)
            if not news:
                bot.send_message(chat_id, "لم أتمكن من جلب الأخبار أو لا توجد أخبار مطابقة اليوم.")
            else:
                text = f"📰 أخبار {MARKETS[market_key]['name']}:\n\n"
                for item in news:
                    text += f"• {item.get('title', 'خبر بدون عنوان')}\n🌍 {item.get('country', '')} | 📅 {item.get('date', item.get('date', ''))}\n\n"
                bot.send_message(chat_id, text[:4000])
        elif prefix == "prices":
            msg = bot.send_message(chat_id, "⏳ جارٍ جلب الأسعار...")
            lines = []
            for symbol in MARKETS[market_key]["pairs"]:
                try:
                    candles = fetch_candles(symbol, "15min", 60)
                    last = candles[-1]
                    previous = candles[-2]["close"]
                    change_pct = ((last["close"] - previous) / previous * 100) if previous else 0
                    arrow = "📈" if change_pct >= 0 else "📉"
                    lines.append(f"{arrow} {symbol}: {fmt_price(last['close'])} ({change_pct:+.2f}% آخر شمعتين)")
                except Exception as exc:
                    lines.append(f"⚠️ {symbol}: غير متاح ({str(exc)[:80]})")
                time.sleep(0.15)
            bot.edit_message_text("💹 أسعار السوق (تغير آخر شمعتين M15):\n\n" + "\n".join(lines), chat_id, msg.message_id)
    except Exception:
        logging.exception("Callback failed")
        try:
            bot.send_message(call.message.chat.id, "حدث خطأ أثناء تنفيذ الطلب. تحقق من سجلات الاستضافة ومفاتيح API.")
        except Exception:
            pass

@bot.message_handler(func=lambda m: True)
def fallback(message):
    bot.reply_to(message, "استخدم الأزرار الموجودة في القائمة 👇", reply_markup=main_keyboard())

# -------------------- Scheduler --------------------

def scheduler_job():
    # Schedule in Algeria time; checks each minute and avoids duplicate sends in same day/session.
    sent_today = set()
    while True:
        now = datetime.now(TIMEZONE)
        if now.hour == 0 and now.minute == 1:
            sent_today.clear()
        session = {
            (10, 0): "🇬🇧 جلسة لندن",
            (13, 0): "☀️ منتصف اليوم",
            (15, 0): "🇺🇸 جلسة نيويورك",
        }.get((now.hour, now.minute))
        key = (now.date().isoformat(), now.hour)
        if session and key not in sent_today:
            sent_today.add(key)
            try:
                send_signals_to_all(session)
            except Exception:
                logging.exception("Scheduled job failed")
        time.sleep(20)

def main():
    init_db()
    start_keep_alive()
    threading.Thread(target=scheduler_job, daemon=True).start()
    logging.info("SignalsBot v3.0 started")
    while True:
        try:
            bot.infinity_polling(timeout=45, long_polling_timeout=45, skip_pending=True)
        except Exception:
            logging.exception("Telegram polling failed")
            time.sleep(5)

if __name__ == "__main__":
    main()
