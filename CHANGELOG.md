# Changelog

All notable changes to this project are documented here.
This project follows [Semantic Versioning](https://semver.org/).

## [2.1.0] - 2026-11-12

### Added
- **Home Assistant heartbeat.** The heating package adds `sensor.ha_heartbeat`, rewritten every
  5 minutes. The bot warns when it stops (Home Assistant answers its API but no automation runs —
  the hot-water schedule included) and when the newest tick never reaches the history (the
  recorder stopped saving). Both alert after `BOT_HEARTBEAT_MAX_MIN` (20) minutes, once per
  outage, with a recovery message. `BOT_ENTITY_HEARTBEAT=off` turns it off.
- **External dead-man's switch** (`BOT_PING_URL`, optional, recommended). The bot pings it every
  5 minutes only while Home Assistant answers, ticks and saves history; the outside service
  alerts when the pings stop. This is the only layer that sees a power cut of the whole machine.
  The address is treated as a secret: never logged or shown.
- **Downtime report after a start.** The bot leaves a sign of life every 5 minutes; after a
  start it reports a silence longer than `BOT_HEARTBEAT_MAX_MIN`, with the Home Assistant
  outages the logbook shows in that time.
- `/status`, `/health` and `--check` show the heartbeat, history recording and ping state.

### Fixed
- **`hotwater.csv` logged switches that never happened.** The package wrote ON/OFF right after
  calling the service, so while Hive was unavailable it kept logging a schedule nobody carried
  out (more than two weeks of such rows on the author's install). A new script, `script.hw_set_and_log`, sends
  the command exactly as before, waits up to 2.5 minutes for Hive to report the requested state
  and logs `confirmed`, `not_confirmed` or `hive_unavailable` plus the state Hive reported; a
  failure also goes to the logbook. The schedule, the restart reconciliation and the legionella
  cycle all use it. Control behaviour is unchanged.
- The hot water Boost logger no longer records `BOOST_off`/`BOOST_on` when Hive merely comes back
  from `unavailable`.

## [2.0.3] - 2026-11-12

_Not released on its own: these fixes ship in 2.1.0._

### Fixed
- **Heating time was undercounted.** `/heating`, `/today`, the last run and `/temperature` read
  `binary_sensor.thermostat_1_state`, which the Hive integration (pyhive 1.0.9 and 1.0.10)
  computes as *current temperature < target* — not as Hive's call for heat. On a live install
  it gave 3 h 31 min for two days where Hive reported 8 h 18 min. The bot now uses the climate
  entity's `hvac_action` attribute (`working` in the Hive API), with full attribute history and
  `last_updated` stamps, since that attribute changes while the climate state does not. It is
  still a control signal, and the replies say so: not boiler firing, gas or radiator temperature.
- **Missing data was shown as "off" or zero.** No history, `unavailable` and `unknown` are now
  unknown time: left out of totals, with the coverage shown ("data for 49% of the time", "no
  data" for a day). Home Assistant's own outages are read from the logbook (`stopped`/`started`;
  after a crash or power cut, from the last sign of life before the restart), so an old value is
  no longer carried through them. Temperature min/max/average skip unknown time too.
- A short `unavailable` blip inside a run (`BOT_GAP_STITCH_MIN`) still joins the run for display,
  but its minutes are no longer counted as heating; the line says "+2 min without data".
- **The alert no longer diagnoses a leaking valve.** The hint fired whenever the binary sensor was
  not `on` while hot water was on — including unknown states and real heat demand. The alert now
  gives facts only: Hive's call for heat now, hot water control, and the last 3 hours of
  temperature, call for heat and hot water control with coverage.
- Hot water "on" is described as *control on*, not continuous heating, in both languages.
- Comparisons and day boundaries are done in UTC, so the repeated hour of the autumn clock change
  sorts and splits correctly.
- The "reading has not changed" notice is information, not a fault (`ℹ️`), and `/status` shows
  "reading last changed" instead of "sensor data older than".

### Removed
- `BOT_ENTITY_HEATING_STATE`. If it is still set, the bot logs that it is ignored.

## [2.0.2] - 2026-11-09

### Documentation
- Home Assistant **2026.10.3 or newer** is now a stated requirement. In October 2026 Hive changed
  its sign-in page and the Hive integration in 2026.10.2 and older can no longer parse it: the
  integration stops in `setup_error` with `Failed to get login info: Expecting value: line 1
  column 20 (char 19)`, and every Hive entity goes `unavailable`. A restart does not fix it; the
  update does (`pyhive-integration` 1.0.10). The FAQ in both READMEs explains the symptom and
  the fix.
- "Tested on" badge raised to Home Assistant 2026.10 — the package, the automation and the bot
  run on 2026.10.4.

No code changes apart from the version string.

## [2.0.1] - 2026-09-19

### Fixed
- The "stale sensor" warning fired on a perfectly healthy install. Hive writes a new state only
  when the value changes — `last_reported` does not advance either — so a house sitting at one
  temperature is indistinguishable from a silent sensor. Measured over 30 days on a live install:
  median gap between changes 16 min, twelve gaps over 3 h, longest 7.5 h, all of them a calm
  house. The old 45-minute default would have alerted 87 times in a single week.
  `BOT_STALE_SENSOR_MIN` now defaults to **480** (8 hours), which still catches a frozen
  integration or a hung Home Assistant, since those last far longer.
- An `unavailable` sensor and an unchanged reading are now two different messages instead of one
  that claimed the sensor had stopped refreshing. The unchanged one says what value it is stuck
  at and that a steady house looks the same.

## [2.0.0] - 2026-09-19

### Added
- **Telegram bot** (`telegram-bot/hive_bot.py`): threshold alerts with hysteresis and
  configurable reminders, commands `/temperature`, `/heating`, `/boosts`, `/water`, `/today`,
  `/status`, `/threshold`, `/mute`, `/unmute`, `/help`, a `/health` endpoint on localhost, a
  systemd unit with `Restart=always`, and a cron watchdog that alerts if the bot itself dies.
  Standard library only.
- **Bilingual output** through one message catalogue (`telegram-bot/messages.py`):
  `BOT_LANG=en|pl`, language-aware decimal separator, weekday names and plural forms, Polish
  command aliases.
- **Configuration through `.env`**: alert threshold, intervals, watchdog limits, time zone,
  language and every entity id, so the bot works on installs whose entities are named
  differently.
- **Test suite** (`telegram-bot/tests/`, 82 tests, no dependencies): formatting, `.env` parsing,
  state migration, history analysis, every command in both languages, alert and watchdog flows.
- **Heating package** (`homeassistant/packages/heating.yaml`): hot water schedule, weekly
  legionella cycle, reconciliation after a restart, CSV loggers, core log archiving.
- `homeassistant/configuration.example.yaml`, `LICENSE` (MIT), `SECURITY.md`,
  `CONTRIBUTING.md`, `.gitignore`, `tools/check-secrets.sh`, issue templates.
- `README.md` rewritten; `README.pl.md` added.

### Changed
- The Boost automation now ships as a **list** (`- id: hive_boost_dynamic`), so the file can be
  included directly instead of being retyped into the UI.
- The blocking condition is now the template `{{ temp >= 23 }}` instead of
  `numeric_state above: 23`. With the old version, exactly 23.0 °C fell through to the default
  branch and got the full 10 minutes of Boost; now it is blocked.
- Automation text, comments and log messages are in English.
- `[MON] Thermostat change` runs as `mode: queued` (`max: 10`). With the default `single`,
  simultaneous Hive updates were dropped with "Already running" and never reached the log.

### Fixed
- `.env` values followed by a comment on the same line were silently ignored, so a changed
  threshold or poll interval had no effect and a commented `HA_URL` broke startup.
- Periods of `unavailable` are no longer counted as heating. A Hive dropout used to be treated
  as a continued "on", which overstated `/heating`, `/today` and `/boosts`; history that ends
  during a dropout used to be counted to the end of the window.
- Durations are computed in real elapsed time, so a period spanning a daylight-saving change is
  no longer an hour out.
- The watchdog could never deliver its alert when `TELEGRAM_CHAT_ID` was unset: it now falls
  back to the chat id in `state.json` and fails loudly if there is none. It no longer sources
  `.env` as a shell script, keeps the Telegram token out of the process list, and writes its
  marker file next to the bot instead of a predictable path in `/tmp`.
- `/mute` is bounded to 1–1440 minutes; `/mute 9999999` used to silence alerts for years.
- Upgrading keeps the bound chat, the alert history and the Telegram offset: `state.json` from
  version 1 is migrated to the new field names, with a `.bak` copy left behind.

## [1.0.0] - 2025-11-10

Initial public version: the dynamic Boost automation (`automations.yaml`) and a README.
Tagged on 2026-09-18 as a rollback point before the 2.0.0 work started.

[2.1.0]: https://github.com/popek1990/hive-dynamic-boost-control-home-assistant/releases/tag/v2.1.0
[2.0.2]: https://github.com/popek1990/hive-dynamic-boost-control-home-assistant/releases/tag/v2.0.2
[2.0.1]: https://github.com/popek1990/hive-dynamic-boost-control-home-assistant/releases/tag/v2.0.1
[2.0.0]: https://github.com/popek1990/hive-dynamic-boost-control-home-assistant/releases/tag/v2.0.0
[1.0.0]: https://github.com/popek1990/hive-dynamic-boost-control-home-assistant/releases/tag/v1.0.0
