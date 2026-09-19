# Security

## Reporting a vulnerability

Please report anything security-relevant through GitHub: open a
[private security advisory](https://github.com/popek1990/hive-dynamic-boost-control-home-assistant/security/advisories/new)
if that is enabled, otherwise open a normal issue and leave out the sensitive detail — say that
you have it and we will find a private channel. Please do not put tokens, addresses or logs in
a public issue.

## What this project touches

This repository configures a system that controls heating and hot water in a real house and
holds two secrets. Understand both before you deploy it.

**The Home Assistant long-lived access token gives full API access.** Home Assistant has no
read-only tokens. The bot only reads states and history, but the token it carries could switch
anything in your house. Therefore:

- keep `.env` readable only by the bot's own user (`chmod 600`),
- create a **separate, non-administrator Home Assistant user** for the bot and generate the
  token on that account,
- revoke the token in Home Assistant the moment you suspect it leaked (profile → Security →
  Long-lived access tokens),
- never paste `--check` output into an issue: it lists your entity ids and your Home Assistant
  address.

**The Telegram bot token controls the bot.** Anyone holding it can read messages sent to the bot
and send messages as the bot. Revoke it with `/revoke` in [@BotFather](https://t.me/BotFather).

**Leave `TELEGRAM_CHAT_ID` empty and the first `/start` wins.** That is deliberate — it makes
the first run easy — but it means a stranger who guesses your bot's name before you do would
receive your house data. Pin your own chat id in `.env`. `hive_bot.py --check` warns when it is
not set.

## What must never be committed

The `.gitignore` in this repository already blocks these, and
[`tools/check-secrets.sh`](tools/check-secrets.sh) scans the working tree, the staged changes
and the new commits for them:

- `.env` and anything else holding a token,
- `state.json` (it contains your Telegram chat id),
- `*.csv` and `*.log` from your install — temperature samples and hot water events show when
  people are at home,
- Home Assistant core log archives (`ha_core_*.log`): they contain tokens, internal addresses
  and user ids,
- database copies (`home-assistant_v2.db*`) and `.storage/`.

If a secret does reach a public repository, rotate it first and clean the history second. A
force push does not un-publish a blob: it stays reachable by its hash, in forks and in caches
until GitHub Support removes it.

## Health endpoint

`/health` binds to `127.0.0.1` only and is not authenticated. Do not expose it through a
reverse proxy — it reports your indoor temperature and the state of your Home Assistant link.
