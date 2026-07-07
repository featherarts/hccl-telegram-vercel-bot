HCCL Telegram Bot — Vercel Quick Start

1. Upload this folder to a new GitHub repo.
2. Import the repo into Vercel.
3. Add Vercel environment variables:
   TELEGRAM_BOT_TOKEN
   SUPABASE_URL
   SUPABASE_KEY
   WEBHOOK_SECRET
4. Deploy.
5. Copy your Vercel URL:
   https://YOUR-PROJECT.vercel.app/api/telegram
6. On your computer, create .env from .env.example and add:
   TELEGRAM_BOT_TOKEN=your_token
   WEBHOOK_SECRET=same_as_vercel
7. Run:
   pip install python-dotenv
   python set_webhook.py https://YOUR-PROJECT.vercel.app/api/telegram
8. Test in Telegram:
   /start
   /topbat
   /player Hasitha
   /profile Hasitha
   /card Hasitha


IMPORTANT: Keep pyproject.toml in the repo root. Vercel needs it to find api.telegram:handler.


Profile Card Commands Added in v1.3
- /player PlayerName  -> full official profile card
- /profile PlayerName -> same as /player
- /card PlayerName    -> compact player card

These commands read both hccl_rankings and hccl_rating_details from the latest saved Supabase snapshot.
