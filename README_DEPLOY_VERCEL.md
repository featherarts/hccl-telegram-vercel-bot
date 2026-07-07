# HCCL Telegram Bot — Vercel Webhook Version

This version is designed for free serverless hosting on Vercel.
It does **not** run 24/7 with polling. Instead, Telegram calls `/api/telegram` whenever someone sends a bot command.

## Files

```text
api/telegram.py      # Vercel webhook endpoint
hccl_bot_data.py     # Supabase ranking queries
set_webhook.py       # run once after deployment
delete_webhook.py    # optional, removes webhook if returning to local polling
requirements.txt     # Python dependencies for Vercel
vercel.json          # Vercel function config
.env.example         # local env template
```

## Step 1 — Push to GitHub

Create a new repo or folder for this bot and upload all files from this package.

Suggested repo name:

```text
hccl-telegram-vercel-bot
```

## Step 2 — Import repo into Vercel

1. Go to Vercel
2. Add New Project
3. Import your GitHub repo
4. Keep default settings
5. Add environment variables before/after deploy

## Step 3 — Add Vercel environment variables

In Vercel project settings, add:

```text
TELEGRAM_BOT_TOKEN
SUPABASE_URL
SUPABASE_KEY
WEBHOOK_SECRET
```

Optional:

```text
ALLOWED_CHAT_IDS
DEFAULT_TOP_LIMIT
```

`WEBHOOK_SECRET` is recommended. Use any long random text. The same value must also be used when running `set_webhook.py`.

## Step 4 — Deploy

After deployment, your webhook endpoint will be:

```text
https://YOUR-VERCEL-PROJECT.vercel.app/api/telegram
```

Open it in your browser. You should see JSON saying the HCCL Telegram Bot Webhook is ready.

## Step 5 — Set Telegram webhook

On your computer, create a local `.env` file from `.env.example` and fill:

```text
TELEGRAM_BOT_TOKEN=your_bot_token
WEBHOOK_SECRET=same_secret_you_added_to_vercel
```

Then run:

```bash
pip install python-dotenv
python set_webhook.py https://YOUR-VERCEL-PROJECT.vercel.app/api/telegram
```

If it prints `"ok": true`, the webhook is active.

## Step 6 — Test in Telegram

Send your bot:

```text
/start
/topbat
/player Hasitha
/report
```

In groups, try:

```text
/topbat@YourBotUsername
```

## Commands

```text
/start
/help
/topbat
/topbat 5
/topbowl
/topall
/player Hasitha
/team DRAGONS
/team DRAGONS batting
/movers
/fallers
/gains
/newentries
/report
/benchmarks
/weeks
```

## Important notes

- Your Streamlit Dashboard must save at least one snapshot to Supabase before this bot can show data.
- Do not commit `.env` to GitHub.
- If you previously ran the polling bot locally, stop it before using webhooks.
- To return to local polling later, run `python delete_webhook.py`.


## Vercel CLI 54+ entrypoint fix

This package includes `pyproject.toml` with:

```toml
[tool.vercel]
entrypoint = "api.telegram:handler"
```

Do not delete this file. It tells Vercel to load the `handler` class from `api/telegram.py`.

## v1.2 deployment fix

This package includes a valid `pyproject.toml` with both a `[project]` table and the Vercel entrypoint:

```toml
[tool.vercel]
entrypoint = "api.telegram:handler"
```

Vercel now runs `uv lock` when `pyproject.toml` exists, so the `[project]` table and dependencies are included here.


## v1.3 Profile Cards

This version adds richer Telegram profile cards:

```text
/player Hasitha
/profile Hasitha
/card Hasitha
```

The full card shows ranking positions, ratings, movement, qualification badges, runs, wickets, recent form, career scores, achievement scores, and best ranking discipline.

No database schema change is required. It uses existing tables:

```text
hccl_rankings
hccl_rating_details
```


## v1.4 Profile Card Data Fix

This version fixes blank career snapshot values in `/player`, `/profile`, and `/card`.
Some Supabase deployments return the `hccl_rating_details.data` jsonb field as a JSON string instead of a Python dictionary. The bot now accepts both formats and also supports old/new detail key names.

No database schema change is required. Redeploy Vercel after replacing the files.
