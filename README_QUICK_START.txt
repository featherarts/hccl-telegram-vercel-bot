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


## v2.0 /expose command

New command: `/expose` shows the top 3 worst batting recent-form performers with Sinhala labels, emoji styling, inning-by-inning batting scores, and recent form scores. Only players with 100+ career runs are eligible. For full inning-by-inning output, save rankings from Dashboard v5.0 or newer so raw recent-5 data is stored in Supabase.


New in v2.1: /power and enhanced /teamprofile TEAM with team power score.


New in v2.2: `/hot` and `/cold` recent-form tracker commands.
- `/hot` shows hottest overall recent-form players.
- `/cold` shows coldest overall recent-form players.
- Optional: `/hot batting`, `/hot bowling`, `/cold batting`, `/cold bowling`.
- Uses a short in-memory cache so Telegram replies stay fast.
