ENCORE / BURLYWOOD SOCIAL BOT — RAILWAY READY

What's changed:
- removed Replit-only files
- removed keep_alive / Flask dependency
- added requirements.txt
- added railway.json
- added Procfile
- main.py now runs directly on Railway

Railway variables to add:
- DISCORD_TOKEN = your bot token

Optional:
- GUILD_ID = use this only if you want faster guild-only slash command sync while testing

Start command:
- python main.py

Notes:
- music_sim_social.db is included, so your current data comes with the project
- SQLite works for simple use, but Railway storage can reset on redeploys unless you add persistent storage later
- if you want fully permanent data later, move the DB to Railway Postgres

Recommended Railway setup:
1. Create a new GitHub repo
2. Upload these files
3. In Railway, deploy from GitHub
4. Add DISCORD_TOKEN in Variables
5. Deploy
