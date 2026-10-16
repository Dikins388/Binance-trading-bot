# Binance Spot Telegram Signal Bot

Telegram bot that sends BUY/SELL alerts for the ten eligible Binance Spot USDT pairs with the highest 24-hour quote volume. It never places orders and uses public Binance data (no Binance API key).

## Signals

- Rank active Spot/USDT pairs once every 24 hours; exclude stablecoins, leveraged tokens and assets listed in `EXCLUDED_ASSETS`.
- Inspect completed 15-minute candles every minute. BUY: EMA20 crosses above EMA50, close above EMA200, RSI(14) 50–68 and volume above its 20-candle average. SELL: EMA20 crosses below EMA50, or close crosses below EMA50 with RSI below 45.
- Alert users who pressed Start; at most one alert per pair and candle. Commands: `/start`, `/stop`, `/status`, `/top`, `/help`.

Signals are technical indicators, not financial advice or a promise of performance. Evaluate them in observation mode before relying on them.

## Run

1. Create a bot with `@BotFather`; keep the token secret.
2. Copy `.env.example` to `.env` and set `TELEGRAM_BOT_TOKEN`.
3. Run `docker compose up --build -d`.
4. Open the bot in Telegram and press Start.

GitHub stores the source but does not run a process continuously. Deploy this Docker worker to an always-on host and set the token as a secret environment variable there. Never commit `.env`. SQLite is stored in a Docker volume.
