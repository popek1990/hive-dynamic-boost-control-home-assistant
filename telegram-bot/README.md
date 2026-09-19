# Hive heating bot (Telegram)

A small Telegram bot that watches the temperature in a Home Assistant + Hive install, alerts you
when the house gets too warm, and answers questions about what the heating actually did.

Standard library Python only — no pip, no virtualenv, no container. It **only reads** from Home
Assistant; nothing here changes your heating.

## Files

| File | What it is |
|---|---|
| `hive_bot.py` | the bot |
| `messages.py` | every user-visible text, in English and Polish |
| `.env.example` | configuration template — copy to `.env` |
| `hive-bot.service` | systemd unit (user `hivebot`, `/opt/hive-bot`) |
| `watchdog.sh` | cron check that alerts if the bot itself stops answering |
| `tests/` | 82 tests, no dependencies, no network |

## Install

See [Option B in the main README](../README.md#option-b--automation-and-the-telegram-bot) for
the copy-paste version. In short:

1. `useradd --system hivebot`, put the files in `/opt/hive-bot`, `chown` them to `hivebot`.
2. `cp .env.example /opt/hive-bot/.env`, fill it in, `chmod 600`.
3. `python3 hive_bot.py --check` — it tests the Home Assistant token, every entity, the history
   endpoint and the Telegram token.
4. `systemctl enable --now hive-bot`, then send `/start` to your bot.

## Configuration

Everything lives in `.env`; a matching environment variable wins, so `Environment=` in the
systemd unit or `-e` in a container works too. The keys are documented in `.env.example` — the
ones worth knowing:

| Key | Default | Why you would change it |
|---|---|---|
| `TELEGRAM_CHAT_ID` | empty | **Set it.** Empty means the first `/start` claims the bot. |
| `BOT_LANG` | `en` | `pl` for Polish output; add your own in `messages.py`. |
| `BOT_TZ` | `Europe/London` | your zone, so "today" means your today. |
| `BOT_THRESHOLD_C` | `23.0` | the alert threshold; `/threshold` changes it at runtime. |
| `BOT_HYSTERESIS_C` | `0.3` | how far it must fall before the alarm re-arms. |
| `BOT_REPEAT_MIN` | `180` | reminder while still too warm; `0` disables reminders. |
| `BOT_GAP_STITCH_MIN` | `5` | Hive drops out for a minute or two; gaps shorter than this count as one run. |
| `BOT_ENTITY_*` | Hive defaults | if your entity ids differ. |
| `BOT_HW_SCHEDULE_TEXT` | empty | your hot-water window in words, shown by `/water`. |

Settings from version 1 with Polish names (`BOT_PROG_C`, `TELEGRAM_CZAT_ID`, …) are still
accepted, and `state.json` from version 1 is migrated automatically, keeping the bound chat and
the Telegram offset. The old file is left as `state.json.bak`.

## Alerts

One message when the temperature crosses the threshold — not one per poll. It re-arms only after
the temperature drops by the hysteresis, so a reading hovering on the line does not spam you.
While it stays above, you get a reminder every `BOT_REPEAT_MIN` minutes.

The alert carries context: the thermostat target, whether the radiators are actually hot, and
whether hot water is heating. If the house is warming up while the radiators are **not** being
called for, the bot says so — that pattern usually means heat is leaking into the heating
circuit, which is worth knowing before you blame the thermostat.

`/mute 120` silences alerts for two hours without disarming anything: whatever happens while
muted still alerts you afterwards if it is still too warm.

## Watchdogs

The bot watches the house; two things watch the bot.

- **Its own health**: it warns you when Home Assistant stops answering for `BOT_HA_DOWN_MIN`
  minutes, and when the Hive sensor has not refreshed for `BOT_STALE_SENSOR_MIN` minutes — the
  common case where everything looks fine but the data is hours old.
- **`watchdog.sh` from cron**: asks `http://127.0.0.1:8099/health` and messages you directly if
  the bot is not answering — because a dead bot cannot report itself. It reports once per outage
  and once on recovery.

```bash
curl -s http://127.0.0.1:8099/health
```

```json
{
  "ok": true,
  "version": "2.0",
  "language": "en",
  "started_at": "2026-01-12T09:15:00+00:00",
  "uptime_min": 3192.0,
  "ha_ok": true,
  "ha_error": null,
  "temperature_c": 22.8,
  "last_reading": "2026-01-14T18:29:11+00:00",
  "sensor_age_min": 3.0
}
```

The endpoint returns `503` when the Home Assistant link is down, so any uptime monitor can use
it. It binds to localhost only and is not authenticated — do not proxy it to the internet.

## Diagnostics

```bash
python3 hive_bot.py --check          # configuration, entities, history, Telegram token
journalctl -u hive-bot -f            # live log
systemctl status hive-bot
```

Two instances of the same bot token fight over Telegram long polling (`409 Conflict`), so stop
any manual run before starting the service.

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

They cover formatting in both languages, `.env` parsing, state migration, the history analysis
(including Hive dropouts and daylight-saving changes), every command, and the alert and watchdog
flows. Nothing touches the network.

## Adding a language

Translate the `"en"` block in `messages.py`, add your entries to `WEEKDAYS`, `PLURALS` and
`DECIMAL_SEPARATOR`, then run the tests — a missing key fails the suite.
