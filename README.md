# Deal Tracker Bot

A Python `discord.py` bot for private deal requests, staff review, deal completion, and spender/host leaderboards. It is configured for Railway and uses SQLite.

## Privacy model

- `/deal create` responds ephemerally to the spender. Discord does not allow an ephemeral response to be visible to the tagged host or staff.
- The host receives the request by DM with Accept and Decline buttons. Only the spender and host receive the deal details in DMs.
- Deal details and host earnings are logged in the configured staff channel. Keep that channel restricted to staff.
- Public leaderboards show total USD spent by spenders and completed-deal counts for hosts. Host earnings are only shown to members with the configured staff role (or server administrators).
- `/deal complete` is only available to a deal participant or staff, and only after the host accepts.

## Commands

- `/admin set-roles spender_role host_role staff_role` configures the roles for the current server. A server administrator must run it.
- `/deal create target_user type amount_usd description` creates a deal. The spender and host must have their configured roles.
- `/deal complete deal_id` completes an accepted deal. The spender, host, or staff may complete it.
- `/deal cancel deal_id` force-cancels a pending or accepted deal. Staff only.
- `/admin reset-leaderboard` resets this server's monthly and all-time totals after confirmation. Deal records are preserved; only deals created after the reset count.
- `/admin set-leaderboard-channel channel` posts an auto-updating public all-time spender and host board in the selected text channel. Entries mention users without notifying them; host earnings remain staff-only.
- `/leaderboard` displays monthly/all-time spender or host rankings, with an optional deal-type filter. The **My Rank** button privately shows your rank for the selected view.

## Run locally

Use Python 3.11 or newer. Create a Discord application and bot, then invite it with `applications.commands`, `bot`, and `Send Messages` / `Embed Links` / `Read Message History` permissions. A privileged Server Members intent is not required; uncached members appear by a shortened ID on leaderboards. Then:

```sh
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Set `DISCORD_TOKEN` and `STAFF_LOG_CHANNEL_ID` in `.env`, then run `python bot.py`. The staff log channel must be private to staff. `SQLITE_PATH` defaults to `deals.sqlite3` in the current directory.

## Deploy on Railway

1. Push the bot files to the branch Railway deploys. The repository root must contain `bot.py` and `requirements.txt`; if they are in a subfolder, set that folder as Railway's Root Directory. Create a Railway service from that repository and add a persistent volume mounted at `/data`.
2. Set `DISCORD_TOKEN`, `STAFF_LOG_CHANNEL_ID`, and `SQLITE_PATH=/data/deals.sqlite3` as service variables.
3. Deploy. `railway.toml` selects Railpack, installs `requirements.txt`, and starts `python bot.py`. Clear any dashboard Build/Start Command overrides such as `start.sh`, or set the Start Command to `python bot.py`.
4. In each server, run `/admin set-roles` as an administrator. Commands sync globally on startup and may take a short time to appear in Discord.

Do not deploy multiple replicas against the same SQLite file. The database stores deals and configured role IDs; leaderboard totals are computed from completed deals on demand.