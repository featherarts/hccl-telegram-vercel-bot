HCCL Telegram Bot v1.9 — Interactive Mobile Commands

1. Upload/replace these files in your existing Vercel bot GitHub repo.
2. Redeploy on Vercel.
3. No webhook reset is needed unless your Vercel URL changed.
4. Test in Telegram:

   /rank Pasindu
   /form Pasindu
   /compare Pasindu Yasitha
   /compare Pasindu Dilshan vs Yasitha Nawod
   /battle
   /teamprofile TITANS

New in v1.9:
- /rank quick ranks
- /form recent form card
- /compare player battle card
- /battle random player battle
- /teamprofile team summary card
- Clean mobile-first formatting with emojis and short sections

Required Vercel env variables:
- TELEGRAM_BOT_TOKEN
- SUPABASE_URL
- SUPABASE_KEY
- WEBHOOK_SECRET

Important: Keep pyproject.toml in the repo root. Vercel needs it to find api.telegram:handler.
