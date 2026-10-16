import asyncio
import logging
import os
import time
from datetime import datetime, timezone

import aiohttp
import aiosqlite
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

API = "https://api.binance.com"
STABLES = {"USDT", "USDC", "BUSD", "FDUSD", "TUSD", "USDP", "DAI", "UST", "USTC", "EUR", "AEUR", "TRY", "BRL", "GBP", "AUD", "BIDR", "IDRT", "UAH"}
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
DB = os.getenv("DATABASE_PATH", "bot.sqlite3")
TOP_COUNT = int(os.getenv("TOP_COUNT", "10"))
RANK_HOURS = int(os.getenv("RANK_REFRESH_HOURS", "24"))
EXCLUDED = {s.strip().upper() for s in os.getenv("EXCLUDED_ASSETS", "").split(",") if s.strip()}
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("signals")

async def db_init():
    async with aiosqlite.connect(DB) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS users(chat_id INTEGER PRIMARY KEY, active INTEGER NOT NULL DEFAULT 1)")
        await db.execute("CREATE TABLE IF NOT EXISTS alerts(symbol TEXT, candle INTEGER, PRIMARY KEY(symbol,candle))")
        await db.commit()

async def set_user(chat, active):
    async with aiosqlite.connect(DB) as db:
        await db.execute("INSERT INTO users VALUES(?,?) ON CONFLICT(chat_id) DO UPDATE SET active=excluded.active", (chat, int(active)))
        await db.commit()

async def recipients():
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT chat_id FROM users WHERE active=1") as cur:
            return [row[0] async for row in cur]

async def get_json(session, path, params=None):
    async with session.get(API + path, params=params, timeout=aiohttp.ClientTimeout(total=20)) as res:
        res.raise_for_status()
        return await res.json()

async def top_symbols(session):
    info, tickers = await asyncio.gather(get_json(session, "/api/v3/exchangeInfo"), get_json(session, "/api/v3/ticker/24hr"))
    valid = {s["symbol"]: s["baseAsset"] for s in info["symbols"] if s.get("status") == "TRADING" and s.get("isSpotTradingAllowed") and s.get("quoteAsset") == "USDT"}
    ranked = []
    for t in tickers:
        symbol = t.get("symbol", "")
        base = valid.get(symbol)
        if not base or base in STABLES or base in EXCLUDED or base.endswith(("UP", "DOWN", "BULL", "BEAR")):
            continue
        ranked.append((float(t.get("quoteVolume", 0)), symbol))
    ranked.sort(reverse=True)
    return [symbol for _, symbol in ranked[:TOP_COUNT]]

def ema(values, period):
    alpha = 2 / (period + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append(alpha * value + (1 - alpha) * result[-1])
    return result

def rsi(values, period=14):
    diffs = [b-a for a,b in zip(values[-period-1:-1], values[-period:])]
    gain = sum(max(x, 0) for x in diffs) / period
    loss = sum(max(-x, 0) for x in diffs) / period
    return 100.0 if loss == 0 else 100 - 100 / (1 + gain/loss)

def signal(klines):
    candles = klines[:-1]  # discard forming candle
    if len(candles) < 220: return None
    close = [float(c[4]) for c in candles]
    volume = [float(c[5]) for c in candles]
    e20, e50, e200 = ema(close,20), ema(close,50), ema(close,200)
    strength = rsi(close)
    avg_volume = sum(volume[-21:-1]) / 20
    up = e20[-2] <= e50[-2] and e20[-1] > e50[-1]
    down = e20[-2] >= e50[-2] and e20[-1] < e50[-1]
    buy = up and close[-1] > e200[-1] and 50 <= strength <= 68 and volume[-1] > avg_volume
    sell = down or (close[-2] >= e50[-2] and close[-1] < e50[-1] and strength < 45)
    if buy: return "BUY", strength, close[-1], candles[-1][0]
    if sell: return "SELL", strength, close[-1], candles[-1][0]
    return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await set_user(update.effective_chat.id, True)
    await update.effective_message.reply_text("Вы подписаны на сигналы Binance Spot. Сделки бот не размещает. Команды: /top /status /stop /help")

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await set_user(update.effective_chat.id, False)
    await update.effective_message.reply_text("Уведомления отключены. Для возобновления отправьте /start.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("Только сигналы, Spot, закрытые 15-минутные свечи. Топ-10 по 24-часовому USDT-объёму; обновление раз в сутки.")

async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbols = context.application.bot_data.get("symbols", [])
    await update.effective_message.reply_text("Текущий список: " + (", ".join(symbols) if symbols else "список загружается"))

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("/start — подписаться
/stop — отключить сигналы
/status — настройки
/top — пары
Сигналы — индикаторная информация, не гарантия результата.")

async def send_once(app, symbol, side, strength, price, candle):
    async with aiosqlite.connect(DB) as db:
        try:
            await db.execute("INSERT INTO alerts VALUES(?,?)", (symbol, candle))
            await db.commit()
        except aiosqlite.IntegrityError:
            return
    stamp = datetime.fromtimestamp(candle/1000, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    msg = f"{'🟢' if side == 'BUY' else '🔴'} {side} {symbol}\nЦена закрытия: {price:.8g} USDT\nRSI(14): {strength:.1f}\nСвеча: {stamp}\nИндикаторный сигнал, не финансовая рекомендация."
    for chat in await recipients():
        try: await app.bot.send_message(chat, msg)
        except Exception as exc: log.warning("Cannot message %s: %s", chat, exc)

async def scan(app):
    last_rank = 0.0
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                now = time.monotonic()
                if now-last_rank >= RANK_HOURS*3600 or not app.bot_data.get("symbols"):
                    app.bot_data["symbols"] = await top_symbols(session)
                    last_rank = now
                    log.info("Ranked symbols: %s", app.bot_data["symbols"])
                for symbol in app.bot_data.get("symbols", []):
                    klines = await get_json(session, "/api/v3/klines", {"symbol":symbol,"interval":"15m","limit":230})
                    found = signal(klines)
                    if found: await send_once(app, symbol, *found)
                await asyncio.sleep(60)
            except asyncio.CancelledError: raise
            except Exception:
                log.exception("Market scan failed")
                await asyncio.sleep(60)

async def post_init(app):
    await db_init()
    app.create_task(scan(app))

def main():
    if not TOKEN or TOKEN == "replace_me": raise RuntimeError("Set TELEGRAM_BOT_TOKEN in environment")
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    for cmd, handler in (("start",start),("stop",stop),("status",status),("top",top),("help",help_cmd)):
        app.add_handler(CommandHandler(cmd,handler))
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__": main()
