# Telegram Channel Controller — Long Polling

This version uses Telegram long polling (`getUpdates`), not a webhook. It includes a small HTTP health endpoint for Render Web Service.

## Required Render Environment Variables
- `BOT_TOKEN`: your newly generated BotFather token. Do not hardcode it or commit it to GitHub.
- `DATABASE_URL`: PostgreSQL connection string. Needed for durable user/channel/schedule data.

The numeric admin ID is set in `bot.py` as requested.

## Render
Build: `pip install -r requirements.txt`
Start: `python bot.py`
Health path: `/healthz`

Create a PostgreSQL database and set DATABASE_URL in Render. The local filesystem is not durable storage. Free hosting may sleep or have plan limits; monitoring cannot guarantee lifetime uptime.

Add bot as admin to each channel, then run `/addchannel` in the target channel. The bot needs permission to post.

Important: regenerate any bot token previously shared in chat or exposed publicly. Paste the new token only into Render > Environment > BOT_TOKEN.
