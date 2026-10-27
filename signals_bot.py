import os
import logging
import threading
import requests
import time
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
import telebot
from telebot import types

TELEGRAM_TOKEN     = os.environ.get("SIGNALS_BOT_TOKEN")
GROQ_API_KEY       = os.environ.get("GROQ_API_KEY")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
GEMINI_API_KEY     = os.environ.get("GEMINI_API_KEY")
TWELVEDATA_API_KEY = os.environ.get("TWELVEDATA_API_KEY")
CHANNEL_ID         = os.environ.get("CHANNEL_ID")

logging.basicConfig(level=logging.INFO)
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# ============================================================
# الأسواق
# ============================================================
MARKETS = {
    "forex": {
        "name": "💱 الفوركس",
        "pairs": ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", "USD/CAD"],
        "news_countries": ["USD", "EUR", "GBP", "JPY"]
    },
    "crypto": {
        "name": "🪙 الكريبتو",
        "pairs": ["BTC/USD", "ETH/USD", "SOL/USD", "BNB/USD", "XRP/USD", "ADA/USD"],
        "news_countries": ["crypto"]
    },
    "gold": {
        "name": "🥇 الذهب والسلع",
        "pairs": ["XAU/USD", "XAG/USD", "OIL/USD", "XAU/EUR"],
        "news_countries": ["USD"]
    },
    "indices": {
        "name": "📊 المؤشرات",
        "pairs": ["NDX", "SPX", "DJI", "FTSE", "DAX", "NKY"],
        "news_countries": ["USD", "EUR", "GBP"]
    },
    "all": {
        "name": "🌍 الكل",
        "pairs": ["EUR/USD", "GBP/USD", "BTC/USD", "ETH/USD", "XAU/USD", "NDX"],
        "news_countries": ["USD", "EUR", "GBP"]
    }
}

# ============================================================
# قاعدة بيانات
# ============================================================
subscribers = {}  # {uid: {"market": "all", "name": ""}}

def get_user(uid, name=""):
    if uid not in subscribers:
        subscribers[uid] = {"market": "all", "name": name}
    return subscribers[uid]

# ============================================================
# Keep Alive
# ============================================================
class KeepAlive(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"SignalsBot v2.0 Running")
    def log_message(self, f, *a): pass

def start_keep_alive():
    t = threading.Thread(target=lambda: HTTPServer(("0.0.0.0", 8081), KeepAlive).serve_forever())
    t.daemon = True; t.start()

# ============================================================
# جلب بيانات السوق
# ============================================================
def get_market_data(symbol):
    try:
        url = f"https://api.twelvedata.com/quote?symbol={symbol}&apikey={TWELVEDATA_API_KEY}"
        r = requests.get(url, timeout=8)
        data = r.json()
        if "close" in data:
            close = float(data["close"])
            open_ = float(data["open"])
            change = close - open_
            pct = round(change / open_ * 100, 2)
            return {"symbol": symbol, "price": close, "open": open_,
                    "high": float(data["high"]), "low": float(data["low"]),
                    "change": change, "change_pct": pct}
    except: pass
    return None

# ============================================================
# جلب الأخبار
# ============================================================
def get_news_for_market(market_key):
    try:
        r = requests.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json", timeout=10)
        data = r.json()
        market = MARKETS.get(market_key, MARKETS["all"])
        countries = market["news_countries"]
        today = datetime.now().strftime("%Y-%m-%d")

        if "crypto" in countries:
            # أخبار الكريبتو من CoinGecko
            return get_crypto_news()

        filtered = [e for e in data if
                    e.get("impact") == "High" and
                    today in str(e.get("date", "")) and
                    e.get("country", "") in countries]
        return filtered[:5]
    except:
        return []

def get_crypto_news():
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/news",
            timeout=10
        )
        data = r.json()
        news = []
        for item in data.get("data", [])[:5]:
            news.append({
                "title": item.get("title", ""),
                "country": "Crypto",
                "date": item.get("updated_at", "")[:10]
            })
        return news
    except:
        return []

# ============================================================
# توليد الصفقات بالذكاء الاصطناعي
# ============================================================
def generate_signal(market_key):
    market = MARKETS.get(market_key, MARKETS["all"])
    pairs = market["pairs"]

    # جلب بيانات السوق
    market_data = []
    for pair in pairs:
        data = get_market_data(pair)
        if data:
            market_data.append(data)
        time.sleep(0.3)

    # جلب الأخبار
    news = get_news_for_market(market_key)

    pairs_text = ""
    for d in market_data:
        arrow = "📈" if d["change"] >= 0 else "📉"
        pairs_text += f"{arrow} {d['symbol']}: {d['price']} ({d['change_pct']:+.2f}%)\n"

    news_text = ""
    if news:
        news_text = "\nأخبار مهمة اليوم:\n"
        for n in news:
            if isinstance(n, dict):
                news_text += f"• {n.get('title', '')} ({n.get('country', '')})\n"

    prompt = f"""أنت محلل تقني خبير في {market['name']}.

بيانات السوق الحية:
{pairs_text}
{news_text}

قواعد:
- اختر 3 أزواج فقط لها إشارة واضحة
- نسبة R:R لا تقل عن 1:2
- إذا السوق غير واضح قل WAIT

أعطِ 3 صفقات بهذا الفورمات:

━━━━━━━━━━━━━━━━━━━━━━
📊 الزوج: [اسم الزوج]
✅ الإشارة: [BUY 🟢 / SELL 🔴 / WAIT ⏸️]
💪 القوة: [X/10]
🎯 الدخول: [السعر]
🎯 TP1: [السعر]
🎯 TP2: [السعر]
🛡️ SL: [السعر]
⚖️ R:R: [1:X]
💡 السبب: [سبب مختصر]
━━━━━━━━━━━━━━━━━━━━━━

الرد باللغة العربية الفصحى فقط."""

    # جرب الموديلات
    for name, func in [
        ("Groq", lambda: _groq(prompt)),
        ("Gemini", lambda: _gemini(prompt)),
        ("OpenRouter", lambda: _openrouter(prompt))
    ]:
        try:
            result = func()
            if result:
                logging.info(f"✅ {name} نجح")
                return result
        except Exception as e:
            logging.warning(f"❌ {name} فشل: {e}")

    return None

def _groq(prompt):
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": "qwen/qwen3.8-27b", "max_tokens": 900,
                "messages": [{"role": "user", "content": prompt}]}
    r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                      headers=headers, json=payload, timeout=30)
    res = r.json()
    if "choices" in res: return res["choices"][0]["message"]["content"]
    raise Exception(str(res))

def _gemini(prompt):
    for model in ["gemini-2.0-flash", "gemini-1.5-flash"]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        r = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30)
        res = r.json()
        if "candidates" in res:
            return res["candidates"][0]["content"]["parts"][0]["text"]
    raise Exception("Gemini فشل")

def _openrouter(prompt):
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}",
               "Content-Type": "application/json",
               "HTTP-Referer": "https://signals-bot-ftma.onrender.com"}
    for model in ["google/gemini-2.0-flash-lite-001", "qwen/qwen2.5-72b-instruct"]:
        payload = {"model": model, "max_tokens": 900,
                   "messages": [{"role": "user", "content": prompt}]}
        r = requests.post("https://openrouter.ai/api/v1/chat/completions",
                          headers=headers, json=payload, timeout=30)
        res = r.json()
        if "choices" in res:
            return res["choices"][0]["message"]["content"]
    raise Exception("OpenRouter فشل")

# ============================================================
# إرسال الصفقات
# ============================================================
def send_signals_to_all(session_name):
    logging.info(f"📊 جارٍ توليد صفقات {session_name}...")

    # إرسال للقناة (كل الأسواق)
    signals = generate_signal("all")
    if signals and CHANNEL_ID:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        msg = f"🎯 صفقات {session_name}\n📅 {now}\n\n{signals}\n\n⚠️ للأغراض التعليمية فقط."
        try:
            bot.send_message(CHANNEL_ID, msg)
        except Exception as e:
            logging.error(f"❌ فشل إرسال القناة: {e}")

    # إرسال لكل مشترك حسب السوق المفضل
    for uid, user_info in subscribers.copy().items():
        market_key = user_info.get("market", "all")
        signals = generate_signal(market_key)
        if not signals:
            continue
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        market_name = MARKETS[market_key]["name"]
        msg = (f"🎯 صفقات {session_name}\n"
               f"📅 {now} | {market_name}\n\n"
               f"{signals}\n\n"
               f"⚠️ للأغراض التعليمية فقط.")
        try:
            bot.send_message(uid, msg)
            time.sleep(0.1)
        except Exception as e:
            if "blocked" in str(e).lower():
                subscribers.pop(uid, None)

# ============================================================
# جدول الإرسال
# ============================================================
def scheduler_job():
    while True:
        now = datetime.now()
        h, m = now.hour, now.minute
        if h == 10 and m == 0:
            send_signals_to_all("🇬🇧 سيشن لندن")
            time.sleep(61)
        elif h == 13 and m == 0:
            send_signals_to_all("☀️ منتصف النهار")
            time.sleep(61)
        elif h == 15 and m == 0:
            send_signals_to_all("🇺🇸 سيشن نيويورك")
            time.sleep(61)
        else:
            time.sleep(30)

def start_scheduler():
    t = threading.Thread(target=scheduler_job)
    t.daemon = True; t.start()

# ============================================================
# الكيبورد
# ============================================================
def get_main_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("📊 صفقات الآن", "📰 أخبار اليوم")
    kb.row("💹 أسعار السوق", "📈 إحصائيات")
    kb.row("🎯 اختيار السوق", "🔔 اشتراك")
    kb.row("🔕 إلغاء الاشتراك")
    return kb

def get_market_kb():
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("💱 الفوركس", callback_data="market_forex"),
        types.InlineKeyboardButton("🪙 الكريبتو", callback_data="market_crypto")
    )
    kb.row(
        types.InlineKeyboardButton("🥇 الذهب", callback_data="market_gold"),
        types.InlineKeyboardButton("📊 المؤشرات", callback_data="market_indices")
    )
    kb.row(types.InlineKeyboardButton("🌍 الكل", callback_data="market_all"))
    return kb

def get_signals_market_kb():
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("💱 فوركس", callback_data="sig_forex"),
        types.InlineKeyboardButton("🪙 كريبتو", callback_data="sig_crypto")
    )
    kb.row(
        types.InlineKeyboardButton("🥇 ذهب", callback_data="sig_gold"),
        types.InlineKeyboardButton("📊 مؤشرات", callback_data="sig_indices")
    )
    kb.row(types.InlineKeyboardButton("🌍 الكل", callback_data="sig_all"))
    return kb

def get_news_market_kb():
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("💱 فوركس", callback_data="news_forex"),
        types.InlineKeyboardButton("🪙 كريبتو", callback_data="news_crypto")
    )
    kb.row(
        types.InlineKeyboardButton("🥇 ذهب", callback_data="news_gold"),
        types.InlineKeyboardButton("📊 مؤشرات", callback_data="news_indices")
    )
    kb.row(types.InlineKeyboardButton("🌍 الكل", callback_data="news_all"))
    return kb

def get_prices_market_kb():
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("💱 فوركس", callback_data="prices_forex"),
        types.InlineKeyboardButton("🪙 كريبتو", callback_data="prices_crypto")
    )
    kb.row(
        types.InlineKeyboardButton("🥇 ذهب", callback_data="prices_gold"),
        types.InlineKeyboardButton("📊 مؤشرات", callback_data="prices_indices")
    )
    kb.row(types.InlineKeyboardButton("🌍 الكل", callback_data="prices_all"))
    return kb

# ============================================================
# الأوامر
# ============================================================
@bot.message_handler(commands=["start"])
def start(message):
    uid = message.from_user.id
    name = message.from_user.first_name or ""
    get_user(uid, name)
    bot.reply_to(message,
        f"السلام عليكم {name}! 👋\n\n"
        "أنا بوت الصفقات التلقائية 🎯\n\n"
        "📌 الميزات:\n"
        "✅ صفقات تلقائية 3 مرات يومياً\n"
        "✅ أخبار الفوركس والكريبتو\n"
        "✅ أسعار حية لجميع الأسواق\n"
        "✅ اختيار السوق المفضل\n\n"
        "⏰ مواعيد الإرسال:\n"
        "🇬🇧 10:00 — سيشن لندن\n"
        "☀️ 13:00 — منتصف النهار\n"
        "🇺🇸 15:00 — سيشن نيويورك\n\n"
        "اختر من القائمة 👇",
        reply_markup=get_main_kb()
    )

@bot.message_handler(func=lambda m: m.text == "🔔 اشتراك")
def subscribe(message):
    uid = message.from_user.id
    get_user(uid, message.from_user.first_name or "")
    bot.reply_to(message, "✅ تم الاشتراك! ستصلك الصفقات تلقائياً 🎯")

@bot.message_handler(func=lambda m: m.text == "🔕 إلغاء الاشتراك")
def unsubscribe(message):
    subscribers.pop(message.from_user.id, None)
    bot.reply_to(message, "✅ تم إلغاء الاشتراك.")

@bot.message_handler(func=lambda m: m.text == "🎯 اختيار السوق")
def choose_market(message):
    uid = message.from_user.id
    user = get_user(uid)
    current = MARKETS[user["market"]]["name"]
    bot.reply_to(message,
        f"🎯 اختر السوق المفضل لديك\n\nالسوق الحالي: {current}",
        reply_markup=get_market_kb()
    )

@bot.message_handler(func=lambda m: m.text == "📊 صفقات الآن")
def signals_now(message):
    bot.reply_to(message, "اختر نوع السوق:", reply_markup=get_signals_market_kb())

@bot.message_handler(func=lambda m: m.text == "📰 أخبار اليوم")
def news_today(message):
    bot.reply_to(message, "اختر نوع الأخبار:", reply_markup=get_news_market_kb())

@bot.message_handler(func=lambda m: m.text == "💹 أسعار السوق")
def market_prices(message):
    bot.reply_to(message, "اختر السوق:", reply_markup=get_prices_market_kb())

@bot.message_handler(func=lambda m: m.text == "📈 إحصائيات")
def stats(message):
    bot.reply_to(message,
        f"📈 إحصائيات البوت:\n\n"
        f"👥 المشتركون: {len(subscribers)}\n"
        f"⏰ الوقت: {datetime.now().strftime('%H:%M')}\n"
        f"📅 التاريخ: {datetime.now().strftime('%Y-%m-%d')}\n\n"
        f"⏰ الإرسال التالي:\n"
        f"🇬🇧 10:00 | ☀️ 13:00 | 🇺🇸 15:00"
    )

# ============================================================
# Callbacks
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data.startswith("market_"))
def handle_market_choice(call):
    uid = call.from_user.id
    market_key = call.data.replace("market_", "")
    get_user(uid)[market_key] = market_key
    subscribers[uid]["market"] = market_key
    market_name = MARKETS[market_key]["name"]
    bot.answer_callback_query(call.id, f"✅ تم اختيار {market_name}")
    bot.edit_message_text(
        f"✅ تم تعيين السوق المفضل: {market_name}\n\nستصلك الصفقات التلقائية لهذا السوق 🎯",
        call.message.chat.id, call.message.message_id
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("sig_"))
def handle_signal_request(call):
    market_key = call.data.replace("sig_", "")
    market_name = MARKETS[market_key]["name"]
    bot.answer_callback_query(call.id, "⏳ جارٍ التحليل...")
    msg = bot.send_message(call.message.chat.id, f"⏳ جارٍ تحليل {market_name}...")
    signals = generate_signal(market_key)
    if signals:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        text = f"🎯 صفقات {market_name}\n📅 {now}\n\n{signals}\n\n⚠️ للأغراض التعليمية فقط."
        bot.edit_message_text(text, call.message.chat.id, msg.message_id)
    else:
        bot.edit_message_text("⚠️ تعذر توليد الصفقات، حاول مرة أخرى.", call.message.chat.id, msg.message_id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("news_"))
def handle_news_request(call):
    market_key = call.data.replace("news_", "")
    market_name = MARKETS[market_key]["name"]
    bot.answer_callback_query(call.id, "⏳ جارٍ جلب الأخبار...")
    news = get_news_for_market(market_key)
    if not news:
        bot.send_message(call.message.chat.id, f"✅ لا توجد أخبار مهمة لـ {market_name} اليوم.")
        return
    text = f"📰 أخبار {market_name} اليوم:\n\n"
    for n in news:
        if isinstance(n, dict):
            text += f"🔴 {n.get('title', '')}\n🌍 {n.get('country', '')} | 📅 {n.get('date', '')}\n\n"
    bot.send_message(call.message.chat.id, text)

@bot.callback_query_handler(func=lambda c: c.data.startswith("prices_"))
def handle_prices_request(call):
    market_key = call.data.replace("prices_", "")
    market_name = MARKETS[market_key]["name"]
    bot.answer_callback_query(call.id, "⏳ جارٍ جلب الأسعار...")
    pairs = MARKETS[market_key]["pairs"]
    msg = bot.send_message(call.message.chat.id, f"⏳ جارٍ جلب أسعار {market_name}...")
    text = f"💹 أسعار {market_name}:\n\n"
    for pair in pairs:
        data = get_market_data(pair)
        if data:
            arrow = "📈" if data["change"] >= 0 else "📉"
            text += f"{arrow} {data['symbol']}: {data['price']} ({data['change_pct']:+.2f}%)\n"
        time.sleep(0.3)
    bot.edit_message_text(text, call.message.chat.id, msg.message_id)

@bot.message_handler(func=lambda m: True)
def handle_text(message):
    bot.reply_to(message, "اختر من القائمة 👇", reply_markup=get_main_kb())

# ============================================================
# التشغيل
# ============================================================
def main():
    start_keep_alive()
    start_scheduler()
    print("✅ SignalsBot v2.0 شغال!")
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=60, none_stop=True)
        except Exception as e:
            logging.error(f"❌ خطأ: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
