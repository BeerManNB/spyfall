# Spyfall Telegram Bot MVP

A minimal Spyfall-style Telegram party game bot built with Python and FastAPI. It uses Telegram webhook mode and stores rooms in memory for the first MVP.

## Features

- Private-chat focused Telegram bot UI with inline buttons
- Create a room with a 4-digit code
- Join a room by code
- In-memory room lobby with owner, player list, refresh, leave, and start buttons
- Owner-only game start and reveal
- Random 15-location shortlist, one actual location, and one spy
- Private role messages to every player
- Health endpoint at `GET /health`
- Telegram webhook endpoint at `POST /webhook`

## Create a Telegram bot with BotFather

1. Open Telegram and start a chat with [@BotFather](https://t.me/BotFather).
2. Send `/newbot`.
3. Choose a display name for your bot.
4. Choose a unique username ending in `bot`.
5. Copy the bot token BotFather gives you. This is your `TELEGRAM_BOT_TOKEN`.

## Environment variables

| Variable | Required | Description |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | Yes | Token from BotFather. |
| `WEBHOOK_SECRET` | No | Optional secret checked against Telegram's `X-Telegram-Bot-Api-Secret-Token` header. |
| `PUBLIC_BASE_URL` | No | Public HTTPS base URL of your deployed app, useful when setting the webhook. |
| `BOT_USERNAME` | No | Telegram bot username used to show invite links in lobbies. |

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN="your-bot-token"
uvicorn main:app --host 0.0.0.0 --port 8000
```

Telegram webhooks require a public HTTPS URL. For local development, use a tunnel such as ngrok or Cloudflare Tunnel, then set the webhook to the tunnel URL.

## Deploy to Koyeb

1. Push this repository to GitHub.
2. In Koyeb, create a new app from the GitHub repository.
3. Choose a Python service.
4. Set the run command:

   ```bash
   uvicorn main:app --host 0.0.0.0 --port $PORT
   ```

5. Add environment variables in Koyeb:
   - `TELEGRAM_BOT_TOKEN`: your BotFather token
   - `WEBHOOK_SECRET`: any strong random string, optional but recommended
   - `PUBLIC_BASE_URL`: your Koyeb public app URL, for example `https://your-app.koyeb.app`
   - `BOT_USERNAME`: your Telegram bot username without `@`, optional
6. Deploy the app.
7. Confirm the health endpoint works:

   ```bash
   curl https://your-app.koyeb.app/health
   ```

## Set the Telegram webhook

After deployment, tell Telegram where to send updates. Replace the values below with your real token, app URL, and optional secret.

Without a secret:

```bash
curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
  -d "url=$PUBLIC_BASE_URL/webhook"
```

With a secret:

```bash
curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
  -d "url=$PUBLIC_BASE_URL/webhook" \
  -d "secret_token=$WEBHOOK_SECRET"
```

You can inspect the active webhook with:

```bash
curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getWebhookInfo"
```

## Notes for the MVP

- Rooms are stored in memory, so they disappear when the process restarts.
- The bot is intended to be used mainly in private chat.
- No database, login, payments, Docker, tests, or CI are included yet.
