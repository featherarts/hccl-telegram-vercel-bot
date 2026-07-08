# HCCL Telegram Bot v2.6 — Match Prediction

This version adds the `/predict` matchup preview command and keeps all previous commands.

## New command

```text
/predict AURA TITANS
/predict AURA vs TITANS
```

The prediction compares two teams using Team Power data:

```text
35% batting strength
35% bowling strength
20% all-round strength
10% recent form
```

The bot reply shows:

```text
🏆 prediction
📏 power gap
🎚️ confidence
🏏 batting edge
🎯 bowling edge
👑 all-round edge
🔥 form edge
🌟 key players
🔥 danger form players
```

## Speed optimization

`/predict`, `/power`, and `/teamprofile` share a short Team Power cache. This avoids repeated Supabase reads when players send commands quickly.

## Deploy

Replace your Vercel bot repo files with this package and redeploy Vercel.

No webhook reset is needed unless your Vercel URL changed.
