HCCL Telegram Bot v2.6 - Match Prediction

New command:
/predict AURA TITANS
/predict AURA vs TITANS

Also keeps:
/climb PlayerName
/dna PlayerName
/badges PlayerName
/hot
/cold
/power
/teamprofile TITANS

Deploy:
1. Replace your Vercel bot repo files with this package.
2. Redeploy Vercel.
3. No webhook reset needed unless your Vercel URL changed.

Speed note:
/predict uses cached Team Power data, so repeated commands respond faster and avoid extra Supabase reads.
