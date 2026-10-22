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
CHANNEL_ID         = os.environ.get("CHANNEL_ID")  # مثال: -1001234567890

logging.basicConfig(level=logging.INFO)
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# ============================================================
# قاعدة بيانات المشتركين
# ============================================================
subscribers = set()

# ============================================================
# Keep Alive
# ============================================================
class KeepAlive(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"SignalsBot v1.0 Running")
    def log_message(self, f, *a): pass

def start_keep_alive():
    t = threading.Thread(target=lambda: HTTPServer(("0.0.0.0", 8081), KeepAlive).serve_forever())
    t.daemon = True; t.start()

# ============================================================
# جلب بيانات السوق من TwelveData
# ============================================================
PAIRS = ["EUR/USD", "GBP/USD", "XAU/USD", "BTC/USD", "USD/JPY", "NAS100"]

def get_market_data(symbol):
    try:
        url = f"https://api.twelvedata.com/quote?symbol={symbol}&apikey={TWELVEDATA_API_KEY}"
        r = requests.get(url, timeout=8)
        data = r.json()
        if "close" in data:
            return {
                "symbol": symbol,
                "price": float(data["close"]),
                "open": float(data["open"]),
                "high": float(data["high"]),
                "low": float(data["low"]),
                "change": float(data["close"]) - float(data["open"]),
                "change_pct": round((float(data["close"]) - float(data["open"])) / float(data["open"]) * 100, 2)
            }
    except:
        pass
    return None

def get_rsi(symbol):
    try:
        url = f"https://api.twelvedata.com/rsi?symbol={symbol}&interval=1h&apikey={TWELVEDATA_API_KEY}&outputsize=1"
        r = requests.get(url, timeout=8)
        data = r.json()
        if "values" in data:
            return round(float(data["values"][0]["rsi"]), 1)
    except:
        pass
    return None

# ============================================================
# جلب الأخبار الاقتصادية
# ============================================================
def get_high_impact_news():
    try:
        r = requests.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json", timeout=10)
        data = r.json()
        today = datetime.now().strftime("%Y-%m-%d")
        today_news = [e for e in data if e.get("impact") == "High" and today in str(e.get("date", ""))]
        return today_news[:5]
    except:
        return []

# ============================================================
# توليد الصفقة بالذكاء الاصطناعي
# ============================================================
def generate_signal_with_ai(market_data, news):
    news_text = ""
    if news:
        news_text = "أخبار اليوم عالية التأثير:\n"
        for n in news:
            news_text += f"- {n.get('title')} ({n.get('country')}) الساعة {n.get('date')}\n"

    pairs_text = ""
    for d in market_data:
        if d:
            arrow = "📈" if d["change"] >= 0 else "📉"
            pairs_text += f"{arrow} {d['symbol']}: {d['price']} ({d['change_pct']:+.2f}%)\n"

    prompt = f"""أنت محلل تقني خبير. بناءً على بيانات السوق الحية والأخبار الاقتصادية، أعطِ 3 صفقات تداول احترافية.

بيانات السوق الحية:
{pairs_text}

{news_text}

قواعد صارمة:
- اختر فقط الأزواج التي لها إشارة واضحة
- نسبة R:R لا تقل عن 1:2
- إذا كان هناك خبر عالي التأثير، كن حذراً

أعطِ الصفقات بهذا الفورمات بالضبط لكل صفقة:

━━━━━━━━━━━━━━━━━━━━━━
📊 الزوج: [اسم الزوج]
✅ الإشارة: [BUY 🟢 / SELL 🔴]
💪 القوة: [X/10]
🎯 الدخول: [السعر]
🎯 TP1: [السعر]
🎯 TP2: [السعر]
🛡️ SL: [السعر]
⚖️ R:R: [1:X]
💡 السبب: [سبب الإشارة باختصار]
━━━━━━━━━━━━━━━━━━━━━━

الرد باللغة العربية الفصحى فقط."""

    # جرب الموديلات بالترتيب
    # 1. Groq
    try:
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": "qwen/qwen3.8-27b",
            "max_tokens": 900,
            "messages": [{"role": "user", "content": prompt}]
        }
        r = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload, timeout=30)
        res = r.json()
        if "choices" in res:
            return res["choices"][0]["message"]["content"]
    except Exception as e:
        logging.warning(f"Groq فشل: {e}")

    # 2. Gemini
    try:
        for model in ["gemini-2.0-flash", "gemini-1.5-flash"]:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
            payload = {"contents": [{"parts": [{"text": prompt}]}]}
            r = requests.post(url, json=payload, timeout=30)
            res = r.json()
            if "candidates" in res:
                return res["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        logging.warning(f"Gemini فشل: {e}")

    # 3. OpenRouter
    try:
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://chart-bot-gnm5.onrender.com",
        }
        payload = {
            "model": "google/gemini-2.0-flash-lite-001",
            "max_tokens": 900,
            "messages": [{"role": "user", "content": prompt}]
        }
        r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=30)
        res = r.json()
        if "choices" in res:
            return res["choices"][0]["message"]["content"]
    except Exception as e:
        logging.warning(f"OpenRouter فشل: {e}")

    return None

# ============================================================
# إرسال الصفقات
# ============================================================
def send_signals(session_name):
    logging.info(f"📊 جارٍ توليد صفقات {session_name}...")

    # جلب بيانات السوق
    market_data = []
    for pair in PAIRS:
        data = get_market_data(pair)
        if data:
            market_data.append(data)
        time.sleep(0.5)

    # جلب الأخبار
    news = get_high_impact_news()

    # توليد الصفقات
    signals = generate_signal_with_ai(market_data, news)
    if not signals:
        logging.warning("فشل توليد الصفقات")
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    header = (
        f"🎯 صفقات {session_name}\n"
        f"📅 {now}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    # أخبار مهمة اليوم
    news_section = ""
    if news:
        news_section = "📰 أخبار مهمة اليوم:\n"
        for n in news[:3]:
            news_section += f"🔴 {n.get('title')} ({n.get('country')})\n"
        news_section += "\n"

    message = header + news_section + signals + "\n\n⚠️ هذه الصفقات للأغراض التعليمية فقط."

    # إرسال للقناة
    if CHANNEL_ID:
        try:
            bot.send_message(CHANNEL_ID, message)
            logging.info(f"✅ تم الإرسال للقناة")
        except Exception as e:
            logging.error(f"❌ فشل الإرسال للقناة: {e}")

    # إرسال للمشتركين
    sent = 0
    for uid in subscribers.copy():
        try:
            bot.send_message(uid, message)
            sent += 1
            time.sleep(0.1)
        except Exception as e:
            if "blocked" in str(e).lower():
                subscribers.discard(uid)

    logging.info(f"✅ تم الإرسال لـ {sent} مشترك")

# ============================================================
# جدول الإرسال التلقائي
# ============================================================
def scheduler_job():
    while True:
        now = datetime.now()
        h, m = now.hour, now.minute

        # صباح — سيشن لندن 10:00
        if h == 10 and m == 0:
            send_signals("🇬🇧 سيشن لندن الصباحي")
            time.sleep(61)

        # ظهر — منتصف النهار 13:00
        elif h == 13 and m == 0:
            send_signals("☀️ منتصف النهار")
            time.sleep(61)

        # مساء — سيشن نيويورك 15:00
        elif h == 15 and m == 0:
            send_signals("🇺🇸 سيشن نيويورك")
            time.sleep(61)

        else:
            time.sleep(30)

def start_scheduler():
    t = threading.Thread(target=scheduler_job)
    t.daemon = True; t.start()

# ============================================================
# أوامر البوت
# ============================================================
def get_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("📊 صفقات الآن", "📰 أخبار اليوم")
    kb.row("💹 أسعار السوق", "📈 إحصائيات")
    kb.row("🔔 اشتراك", "🔕 إلغاء الاشتراك")
    return kb

@bot.message_handler(commands=["start"])
def start(message):
    uid = message.from_user.id
    subscribers.add(uid)
    bot.reply_to(message,
        "السلام عليكم! 👋\n\n"
        "أنا بوت الصفقات التلقائية 🎯\n\n"
        "📌 كيف يعمل:\n"
        "✅ يرسل صفقات تلقائية 3 مرات يومياً\n"
        "✅ يحلل السوق بالذكاء الاصطناعي\n"
        "✅ يراقب الأخبار الاقتصادية\n"
        "✅ يعطيك TP1, TP2, SL\n\n"
        "⏰ مواعيد الإرسال:\n"
        "🇬🇧 10:00 — سيشن لندن\n"
        "☀️ 13:00 — منتصف النهار\n"
        "🇺🇸 15:00 — سيشن نيويورك\n\n"
        "اختر من القائمة 👇",
        reply_markup=get_kb()
    )

@bot.message_handler(func=lambda m: m.text == "🔔 اشتراك")
def subscribe(message):
    subscribers.add(message.from_user.id)
    bot.reply_to(message, "✅ تم الاشتراك! ستصلك الصفقات تلقائياً 3 مرات يومياً 🎯")

@bot.message_handler(func=lambda m: m.text == "🔕 إلغاء الاشتراك")
def unsubscribe(message):
    subscribers.discard(message.from_user.id)
    bot.reply_to(message, "✅ تم إلغاء الاشتراك.")

@bot.message_handler(func=lambda m: m.text == "📊 صفقات الآن")
def signals_now(message):
    msg = bot.reply_to(message, "⏳ جارٍ تحليل السوق وتوليد الصفقات...")
    send_signals("🔥 صفقات فورية")
    bot.delete_message(message.chat.id, msg.message_id)

@bot.message_handler(func=lambda m: m.text == "📰 أخبار اليوم")
def news_today(message):
    news = get_high_impact_news()
    if not news:
        bot.reply_to(message, "✅ لا توجد أخبار عالية التأثير اليوم.")
        return
    text = "📰 أخبار اليوم عالية التأثير:\n\n"
    for n in news:
        text += f"🔴 {n.get('title')}\n🌍 {n.get('country')} | ⏰ {n.get('date')}\n\n"
    bot.reply_to(message, text)

@bot.message_handler(func=lambda m: m.text == "💹 أسعار السوق")
def market_prices(message):
    msg = bot.reply_to(message, "⏳ جارٍ جلب الأسعار...")
    text = "💹 أسعار السوق الحية:\n\n"
    for pair in PAIRS:
        data = get_market_data(pair)
        if data:
            arrow = "📈" if data["change"] >= 0 else "📉"
            text += f"{arrow} {data['symbol']}: {data['price']} ({data['change_pct']:+.2f}%)\n"
        time.sleep(0.3)
    bot.edit_message_text(text, message.chat.id, msg.message_id)

@bot.message_handler(func=lambda m: m.text == "📈 إحصائيات")
def stats(message):
    bot.reply_to(message,
        f"📈 إحصائيات البوت:\n\n"
        f"👥 المشتركون: {len(subscribers)}\n"
        f"⏰ الوقت: {datetime.now().strftime('%H:%M')}\n"
        f"📅 التاريخ: {datetime.now().strftime('%Y-%m-%d')}"
    )

@bot.message_handler(func=lambda m: True)
def handle_text(message):
    bot.reply_to(message, "اختر من القائمة 👇", reply_markup=get_kb())

# ============================================================
# التشغيل
# ============================================================
def main():
    start_keep_alive()
    start_scheduler()
    print("✅ SignalsBot v1.0 شغال!")
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=60, none_stop=True)
        except Exception as e:
            logging.error(f"❌ خطأ: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
