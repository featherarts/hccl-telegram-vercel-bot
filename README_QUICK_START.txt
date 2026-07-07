HCCL Telegram Bot v1.7 — Clean Mobile UI Quick Start

1. Upload this folder to your existing Vercel bot GitHub repo.
2. Replace old files, especially api/telegram.py.
3. Redeploy on Vercel.
4. No webhook reset is needed unless your Vercel URL changed.
5. Test in Telegram:
   /topbat
   /topbowl
   /topall
   /movers
   /fallers
   /player Pasindu
   /card Pasindu

What changed in v1.7:
- Cleaner mobile-first message design
- Bold headings through Telegram HTML formatting
- Shorter ranking entries
- Grouped climbers/fallers/gains/new entries
- Improved player profile card and compact card

Required Vercel env variables:
- TELEGRAM_BOT_TOKEN
- SUPABASE_URL
- SUPABASE_KEY
- WEBHOOK_SECRET

Important: Keep pyproject.toml in the repo root. Vercel needs it to find api.telegram:handler.
