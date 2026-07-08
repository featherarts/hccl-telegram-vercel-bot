# HCCL Telegram Bot v2.5 — How to Climb

This version keeps v2.4 Player DNA and adds a fast How to Climb command.

New command:

```text
/climb Hasitha
/climb Hasitha batting
/climb Hasitha bowling
/climb Hasitha ar
```

The command shows the player's current rank, next target above him, approximate rating gap, current form, badges, and the best ways to climb. It uses a short cache so repeated /climb commands respond faster and do not make heavy repeated Supabase calls.

---

# HCCL Telegram Bot v1.9 — Interactive Mobile Commands

This version keeps the Vercel webhook setup from v1.8 and adds new clean mobile-first commands:

```text
/rank Hasitha
/form Hasitha
/compare Pasindu Yasitha
/compare Pasindu Dilshan vs Yasitha Nawod
/battle
/teamprofile TITANS
```

It does **not** run 24/7 with polling. Telegram sends every command to your Vercel endpoint at `/api/telegram`.

## Files

```text
api/telegram.py      # Vercel webhook endpoint
hccl_bot_data.py     # Supabase ranking queries
set_webhook.py       # run once after deployment if URL changed
delete_webhook.py    # optional, removes webhook
requirements.txt     # Python dependencies for Vercel
vercel.json          # Vercel function config
pyproject.toml       # tells Vercel the Python entrypoint
.env.example         # local env template
```

## Deploy / update

1. Replace your existing Vercel bot repo files with this package.
2. Push to GitHub.
3. Redeploy the project on Vercel.
4. You do **not** need to run `set_webhook.py` again unless the Vercel URL changed.

## Required Vercel environment variables

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

## Test commands

```text
/start
/help
/topbat
/topbowl
/topall
/player Hasitha
/card Hasitha
/rank Hasitha
/form Hasitha
/compare Pasindu Yasitha
/compare Pasindu Dilshan vs Yasitha Nawod
/battle
/teamprofile TITANS
/team DRAGONS
/movers
/fallers
/gains
/newentries
/report
/benchmarks
/weeks
```

## Group chat usage

In a group, use the bot username when needed:

```text
/compare@YourBotUsername Pasindu Yasitha
/teamprofile@YourBotUsername TITANS
```

## Notes

- `/compare Pasindu Yasitha` is best for short names.
- For full names, use `vs`: `/compare Pasindu Dilshan vs Yasitha Nawod`.
- `/battle` randomly selects two saved players from the latest Supabase snapshot.
- `/teamprofile` uses the latest saved team rankings and rating details from Supabase.


## v2.0 /expose command

New command: `/expose` shows the top 3 worst batting recent-form performers with Sinhala labels, emoji styling, inning-by-inning batting scores, and recent form scores. Only players with 100+ career runs are eligible. For full inning-by-inning output, save rankings from Dashboard v5.0 or newer so raw recent-5 data is stored in Supabase.


## v2.1 Update - Team Power Rankings

- Adds `/power` command for HCCL Team Power Rankings.
- Enhances `/teamprofile TEAM` with power score, top-10 count, squad count and category strengths.
- Power formula: 35% batting + 35% bowling + 20% all-round + 10% recent form, scaled out of 100.


New in v2.2: `/hot` and `/cold` recent-form tracker commands.
- `/hot` shows hottest overall recent-form players.
- `/cold` shows coldest overall recent-form players.
- Optional: `/hot batting`, `/hot bowling`, `/cold batting`, `/cold bowling`.
- Uses a short in-memory cache so Telegram replies stay fast.
