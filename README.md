# Spyfall Telegram Bot MVP

A minimal Spyfall-style Telegram party game bot built with Python and FastAPI. It uses Telegram webhook mode and stores rooms in memory for the first MVP.

## Features

- Private-chat focused Telegram bot UI with inline buttons
- Create a room with a 4-digit code
- Join a room by code
- In-memory room lobby with owner, player list, refresh, leave, and start buttons
- Owner-only game start and reveal
- Owner-only solo test start for checking the game flow with one player
- Location modes before start: standard enabled locations, 15 random locations, or manual owner selection
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

## Deploy to Yandex Serverless Containers

1. Create a Yandex Cloud billing account.
2. Create a Container Registry.
3. Create a service account with permission to push images to Container Registry, for example the `container-registry.images.pusher` role, then create an authorized key for it.
4. In the GitHub repository, add these secrets under **Settings** -> **Secrets and variables** -> **Actions**:
   - `YC_REGISTRY_ID`: your Yandex Container Registry ID.
   - `YC_SERVICE_ACCOUNT_KEY`: the full JSON authorized key of the Yandex service account.
5. Run the GitHub Actions workflow manually from **Actions** -> **Build and push Yandex Container Registry image** -> **Run workflow**. The workflow also runs automatically on pushes to `main`.
6. After the workflow succeeds, use one of these image URLs:
   - `cr.yandex/<registry-id>/spyfall-bot:<commit-sha>` for the exact image built from a commit.
   - `cr.yandex/<registry-id>/spyfall-bot:latest` for the latest pushed image.
7. Create a Yandex Serverless Container from the pushed image URL.
8. Set environment variables for the container:
   - `TELEGRAM_BOT_TOKEN`: your BotFather token
   - `BOT_USERNAME`: your Telegram bot username without `@`
   - `WEBHOOK_SECRET`: any strong random string
9. Make the container public / allow unauthenticated invoke.
10. Set the Telegram webhook to the container URL:

   ```bash
   curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
     -d "url=https://<container-url>/webhook" \
     -d "secret_token=$WEBHOOK_SECRET"
   ```

Rooms are still stored in memory and disappear after the container restarts. The Docker image is built in GitHub Actions, so local Docker is not required for deployment.

## Set the Telegram webhook

After deployment, tell Telegram where to send updates. Replace the values below with your real token, app URL, and optional secret.

The `/webhook` endpoint validates the optional secret, reads the update, creates an `asyncio` background task for bot processing, and immediately returns `{"status": "ok"}`. This avoids Telegram delivery retry timeouts while `sendMessage` or `sendPhoto` calls continue after acknowledgement.

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

## Location data and images

Locations live in `locations.py`. During the private role reveal, the bot can send an image for the actual location to non-spy players. The spy never receives the actual location image.

Put location images into `assets/locations/`. The filename must match the location `image_key` from `locations.py`. The preferred format is `.jpg`, and the supported formats are `.jpg`, `.jpeg`, and `.png`. For example, a location with `"image_key": "casino"` should use `assets/locations/casino.jpg`.

Add a fallback image as `assets/locations/fallback.jpg`, `assets/locations/fallback.jpeg`, or `assets/locations/fallback.png`. If a location image is missing, the bot falls back to the fallback image; if the fallback image is also missing, the bot sends the role reveal as text only.

The expanded location list is adapted from `adrianocola/spyfall`; see `THIRD_PARTY_NOTICES.md` for the notice and MIT license text.

## Notes for the MVP

- Rooms are stored in memory, so they disappear when the process restarts.
- The bot is intended to be used mainly in private chat.
- No database, login, payments, tests, or CI are included yet.
