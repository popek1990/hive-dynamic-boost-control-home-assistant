# Changelog

All notable changes to this project are documented here.
This project follows [Semantic Versioning](https://semver.org/).

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

[2.0.1]: https://github.com/popek1990/hive-dynamic-boost-control-home-assistant/releases/tag/v2.0.1
[2.0.0]: https://github.com/popek1990/hive-dynamic-boost-control-home-assistant/releases/tag/v2.0.0
[1.0.0]: https://github.com/popek1990/hive-dynamic-boost-control-home-assistant/releases/tag/v1.0.0
