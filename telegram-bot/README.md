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
| `tests/` | 138 tests, no dependencies, no network |

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
| `BOT_STALE_SENSOR_MIN` | `480` | warn when the reading has not changed for this long - see below. |
| `BOT_GAP_STITCH_MIN` | `5` | Hive drops out for a minute or two; gaps shorter than this count as one run. |
| `BOT_PING_URL` | empty | **Recommended.** A dead-man's-switch address outside your house — see Watchdogs. |
| `BOT_HEARTBEAT_MAX_MIN` | `20` | how long the Home Assistant heartbeat may stand still (minimum 10). |
| `BOT_ENTITY_HEARTBEAT` | `sensor.ha_heartbeat` | the heartbeat from the heating package; `off` stops watching it. |
| `BOT_ENTITY_*` | Hive defaults | if your entity ids differ. |
| `BOT_HW_SCHEDULE_TEXT` | empty | your hot-water window in words, shown by `/water`. |

Settings from version 1 with Polish names (`BOT_PROG_C`, `TELEGRAM_CZAT_ID`, …) are still
accepted, and `state.json` from version 1 is migrated automatically, keeping the bound chat and
the Telegram offset. The old file is left as `state.json.bak`.

## Alerts

One message when the temperature crosses the threshold — not one per poll. It re-arms only after
the temperature drops by the hysteresis, so a reading hovering on the line does not spam you.
While it stays above, you get a reminder every `BOT_REPEAT_MIN` minutes.

The alert carries context: the thermostat target, whether Hive is calling for heat right now
(climate `hvac_action`), hot water control, and how the last 3 hours went — temperature from
→ to, minutes of call for heat and of hot water control, each with its data coverage. It states
facts, not a diagnosis: a single reading and the hot-water state cannot tell a fault from
sunshine, cooking or a full house.

## What the numbers mean

- **Heating** time is Hive's own call for heat: the `hvac_action` attribute of
  `BOT_ENTITY_CLIMATE`, read with its `last_updated` stamps (the state stays `heat` while the
  attribute changes). It is a control signal — not boiler firing, gas or radiator temperature.
  `binary_sensor.thermostat_1_state` is not used: the integration computes it as
  *current < target*, so it misses demand at or above the target.
- **Hot water** "control on" means the cylinder may heat when its own thermostat asks; it is not
  continuous boiler firing.
- **Missing data is unknown, never zero.** `unavailable`/`unknown`, no history, and the times when
  Home Assistant itself was not running (logbook `stopped`/`started`; after a crash, from the
  last sign of life before the restart) are left out of every total, and the reply says how much
  of the period is covered. A short gap inside a run (`BOT_GAP_STITCH_MIN`) joins the run for
  display but is not counted.
- **Limit:** Hive only reports changes, so a Hive cloud that silently stops sending updates
  looks the same as a steady house. The bot cannot detect that from history alone.

`/mute 120` silences alerts for two hours without disarming anything: whatever happens while
muted still alerts you afterwards if it is still too warm.

## Watchdogs

The bot watches the house, and four layers watch the parts that can fail. Each one covers a
failure the others cannot see:

| What fails | Who notices | How fast |
|---|---|---|
| Home Assistant stops answering | the bot (`BOT_HA_DOWN_MIN`) | 10 min |
| Home Assistant answers but is stuck — no automation runs, the hot-water schedule included | the bot, from the heartbeat sensor | 20 min |
| Home Assistant runs but stops saving history | the bot, from the heartbeat history | 20 min |
| The bot itself dies | `watchdog.sh` from cron | 15 min |
| **The whole machine loses power or network** | only something outside the house: `BOT_PING_URL` | your choice |
| …and afterwards | the bot, on its next start: "not running from … to …" | at once |

- **Heartbeat** — the heating package adds `sensor.ha_heartbeat`, which Home Assistant rewrites
  every 5 minutes. If its last tick is older than `BOT_HEARTBEAT_MAX_MIN`, Home Assistant still
  answers the API but is not running its automations. If the newest tick never reaches the
  history (checked 3 minutes after the tick, alerting after the same 20 minutes, so a slow
  nightly purge does not count), the recorder has stopped: every history command would show a
  gap. The bot reads the history API, which serves only what is in the database — verified on a
  live install: a tick is missing for the first seconds and present after the 30 s commit.
  Without the sensor the bot logs one hint and carries on; `BOT_ENTITY_HEARTBEAT=off` silences it.
- **External ping** — nothing in the house can report its own power cut. Create a check at a
  dead-man's-switch service (for example [healthchecks.io](https://healthchecks.io), free tier:
  period 5 minutes, grace 15–30 minutes, Telegram or e-mail integration) and put its ping
  address in `BOT_PING_URL`. The bot calls it every 5 minutes **only while everything above is
  fine**; when the calls stop — power cut, server off, network down, Home Assistant stuck — the
  service alerts you. The address is a secret (anyone holding it can fake "all is well"), so it
  stays in `.env` and never appears in the log, `/status` or `/health`.
- **After an outage** the bot reports how long it was gone ("no sign of life from 03.01 02:15
  to 04.01 09:40") and which Home Assistant outages the logbook shows in that time. It leaves a
  sign of life every 5 minutes, so a normal restart is not reported.
- **The temperature reading** is watched too: the bot warns when it has not changed for
  `BOT_STALE_SENSOR_MIN` minutes — the case where everything looks fine but the data is stale.

  A word on that second one: Hive writes a new state **only when the value changes**, so there is
  no heartbeat to watch (`last_reported` does not move either). A quiet reading therefore means
  either a calm house or a frozen integration, and only the length of the silence tells them
  apart. On a real install, measured over 30 days: median gap between changes 16 minutes, twelve
  gaps longer than 3 hours, longest 7.5 hours — every one of them a stable house. That is why the
  default is 8 hours; a hung Home Assistant or a dead integration lasts much longer, and an
  `unavailable` sensor is reported immediately instead, as a separate message.
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
  "sensor_age_min": 3.0,
  "ha_heartbeat": "ok",
  "ha_heartbeat_age_min": 2.1,
  "recorder_ok": true,
  "external_ping": "ok",
  "last_ping": "2026-01-14T18:25:00+00:00"
}
```

`ha_heartbeat` is `ok`, `stalled`, `missing` (no such entity), `unknown` (not checked yet) or
`off`; `external_ping` is `ok`, `failing`, `held` (held back because something is wrong) or
`off`. The endpoint returns `503` when the Home Assistant link is down, so any uptime monitor
can use it. It binds to localhost only and is not authenticated — do not proxy it to the internet.

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
flows — a stuck heartbeat, a recorder that stopped, the external ping and the report after an
outage. Nothing touches the network.

## Adding a language

Translate the `"en"` block in `messages.py`, add your entries to `WEEKDAYS`, `PLURALS` and
`DECIMAL_SEPARATOR`, then run the tests — a missing key fails the suite.
