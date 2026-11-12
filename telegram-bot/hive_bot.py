#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Telegram bot that watches heating and hot water in a Home Assistant + Hive install.

What it does:
  * polls the thermostat temperature and alerts when it crosses a threshold (default 23.0 °C),
  * answers commands (/temperature, /heating, /boosts, /water, /today, /status, ...),
  * has its own health check: it warns when Home Assistant stops answering or when the Hive
    sensor stops refreshing, and serves /health over HTTP on localhost,
  * survives a reboot as a systemd service and keeps its state in state.json.

It only ever READS from Home Assistant. It never changes the heating.

Configuration: .env in this directory (see .env.example).
Logs:          journalctl -u hive-bot -f
Self-test:     python3 hive_bot.py --check
"""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime, timedelta, timezone as utc_timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from messages import Locale, known_languages, make_locale

VERSION = "2.1.0"
DIRECTORY = Path(__file__).resolve().parent
ENV_FILE = DIRECTORY / ".env"
STATE_FILE = DIRECTORY / "state.json"

THRESHOLD_RANGE = (5.0, 35.0)      # sane thermostat thresholds, in °C
MUTE_RANGE = (1, 1440)             # minutes; one day is the longest sensible silence
MAX_DAYS = 31                      # longest range a history command will ask for
TEMPERATURE_HISTORY_DAYS = 7       # above this, /boosts skips per-boost temperatures

log = logging.getLogger("hive-bot")


# ============================================================================================
#  CONFIGURATION
# ============================================================================================

# Old Polish key names kept working, so an existing install does not break on upgrade.
KEY_ALIASES = {
    "TELEGRAM_CHAT_ID": "TELEGRAM_CZAT_ID",
    "BOT_THRESHOLD_C": "BOT_PROG_C",
    "BOT_HYSTERESIS_C": "BOT_HISTEREZA_C",
    "BOT_POLL_SEC": "BOT_POLL_SEK",
    "BOT_REPEAT_MIN": "BOT_POWTORKA_MIN",
    "BOT_STALE_SENSOR_MIN": "BOT_CZUJNIK_STARY_MIN",
    "BOT_HA_DOWN_MIN": "BOT_BRAK_HA_MIN",
    "BOT_NOTIFY_ON_START": "BOT_POWIADOM_START",
}

DEFAULT_ENTITIES = {
    "temperature": "sensor.thermostat_1_current_temperature",
    "target": "sensor.thermostat_1_target_temperature",
    "mode": "sensor.thermostat_1_mode",
    # Hive's own call for heat is the climate attribute hvac_action. Do not use
    # binary_sensor.thermostat_1_state for that: pyhive computes it as
    # current temperature < target, so it misses demand at or above the target.
    "climate": "climate.thermostat_1",
    "boost_heating": "binary_sensor.thermostat_1_boost",
    "boost_water": "binary_sensor.hotwater_boost",
    "water_heater": "water_heater.thermostat_1",
    # A sensor Home Assistant rewrites every 5 minutes (the heating package defines it).
    # If it stops changing, Home Assistant answers but is not running its automations.
    # Set BOT_ENTITY_HEARTBEAT=off to stop watching it.
    "heartbeat": "sensor.ha_heartbeat",
}

FALSE_WORDS = ("0", "no", "false", "off", "nie")
PULSE_EVERY_MIN = 5                # heartbeat check, external ping and sign-of-life cadence
RECORDER_GRACE_MIN = 3             # time Home Assistant gets to write a new state to its database


def strip_inline_comment(value: str) -> str:
    """Drop a trailing ' # comment' but keep a '#' that belongs to the value.

    Without this, 'BOT_THRESHOLD_C=21.0   # my threshold' silently falls back to the
    default, and an HA_URL with a comment produces a broken address.
    """
    quoted = value[:1] in ("'", '"') and value[:1] == value[-1:] and len(value) > 1
    if quoted:
        return value[1:-1]
    for index, character in enumerate(value):
        if character == "#" and (index == 0 or value[index - 1] in " \t"):
            return value[:index].strip()
    return value.strip()


@dataclass
class Config:
    """Bot settings from .env; matching environment variables win."""

    telegram_token: str
    ha_url: str
    ha_token: str
    chat_id: str | None = None          # empty means: the first /start claims the bot
    language: str = "en"
    timezone: str = "Europe/London"
    threshold_c: float = 23.0
    hysteresis_c: float = 0.3           # how far it must drop before the alarm re-arms
    poll_sec: int = 60
    repeat_min: int = 180               # remind while still too warm (0 = never remind)
    # Hive writes a new state only when the value actually changes, so a steady house
    # looks like a silent sensor. Measured on a real install: over 30 days the median gap
    # between changes was 16 min, but 12 gaps were longer than 3 h and the longest was 7.5 h
    # - every one of them a genuinely stable house, not an outage. 45 min would have cried
    # wolf 87 times in a week. 8 h still catches a frozen integration or a hung Home
    # Assistant (those last many hours) without crying wolf at all.
    stale_sensor_min: int = 480
    ha_down_min: int = 10               # no contact with HA = warning
    health_port: int = 8099             # /health, bound to localhost only
    notify_on_start: bool = True        # a message after boot proves the service came back
    gap_stitch_min: float = 5.0         # bridge 'unavailable' blips shorter than this
    hw_schedule_text: str = ""          # optional human description shown by /water
    # The heartbeat sensor ticks every 5 min; three missed ticks plus a margin means
    # Home Assistant is stuck (or not saving history). A bot silence this long is
    # reported once the bot is back.
    heartbeat_max_min: int = 20
    # Optional dead-man's switch outside the house (e.g. healthchecks.io). The bot calls
    # this address every few minutes while everything is fine; when the calls stop - power
    # cut, server down, network gone - that service raises the alarm. Nothing inside the
    # house can report its own power cut.
    ping_url: str = ""
    entities: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_ENTITIES))

    @staticmethod
    def load(env_file: Path | None = None) -> "Config":
        path = env_file or ENV_FILE
        values: dict[str, str] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, raw = line.split("=", 1)
                values[key.strip()] = strip_inline_comment(raw.strip())
        # Environment overrides the file (systemd Environment=, docker -e, shell export).
        values.update({
            key: strip_inline_comment(value)
            for key, value in os.environ.items()
            if key.startswith(("TELEGRAM_", "HA_", "BOT_"))
        })

        def text(key: str, default: str = "") -> str:
            """First non-empty of: the key, its old Polish alias, the default."""
            for candidate in (key, KEY_ALIASES.get(key)):
                if candidate and values.get(candidate, "").strip():
                    if candidate != key:
                        log.info("Using the old setting name %s; %s is the current one",
                                 candidate, key)
                    return values[candidate].strip()
            return default

        def number(key: str, default: float) -> float:
            raw = text(key)
            if not raw:
                return default
            try:
                return float(raw.replace(",", "."))
            except ValueError:
                log.warning("%s=%r is not a number — using %s", key, raw, default)
                return default

        def flag(key: str, default: bool) -> bool:
            raw = text(key)
            return default if not raw else raw.lower() not in FALSE_WORDS

        missing = [k for k in ("TELEGRAM_TOKEN", "HA_URL", "HA_TOKEN") if not text(k)]
        if missing:
            raise SystemExit(
                "Missing required settings in .env: " + ", ".join(missing)
                + f"\nFill in {path} (template: .env.example)."
            )

        language = text("BOT_LANG", "en").lower()
        if language not in known_languages():
            log.warning("BOT_LANG=%r is not a known language %s — falling back to English",
                        language, list(known_languages()))
            language = "en"

        timezone = text("BOT_TZ", "Europe/London")
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            log.warning("BOT_TZ=%r is not a known time zone — falling back to Europe/London "
                        "(on a minimal image install the 'tzdata' package)", timezone)
            timezone = "Europe/London"

        entities = {
            name: text(f"BOT_ENTITY_{name.upper()}", default)
            for name, default in DEFAULT_ENTITIES.items()
        }
        if entities["heartbeat"].lower() in FALSE_WORDS + ("none",):
            entities["heartbeat"] = ""
        if text("BOT_ENTITY_HEATING_STATE"):
            log.warning("BOT_ENTITY_HEATING_STATE is no longer used: heating time now comes from "
                        "the hvac_action attribute of BOT_ENTITY_CLIMATE")

        return Config(
            telegram_token=text("TELEGRAM_TOKEN"),
            ha_url=text("HA_URL").rstrip("/"),
            ha_token=text("HA_TOKEN"),
            chat_id=text("TELEGRAM_CHAT_ID") or None,
            language=language,
            timezone=timezone,
            threshold_c=number("BOT_THRESHOLD_C", 23.0),
            hysteresis_c=number("BOT_HYSTERESIS_C", 0.3),
            poll_sec=int(number("BOT_POLL_SEC", 60)),
            repeat_min=int(number("BOT_REPEAT_MIN", 180)),
            stale_sensor_min=int(number("BOT_STALE_SENSOR_MIN", 480)),
            ha_down_min=int(number("BOT_HA_DOWN_MIN", 10)),
            health_port=int(number("BOT_HEALTH_PORT", 8099)),
            notify_on_start=flag("BOT_NOTIFY_ON_START", True),
            gap_stitch_min=number("BOT_GAP_STITCH_MIN", 5.0),
            hw_schedule_text=text("BOT_HW_SCHEDULE_TEXT"),
            heartbeat_max_min=max(int(number("BOT_HEARTBEAT_MAX_MIN", 20)), 2 * PULSE_EVERY_MIN),
            ping_url=text("BOT_PING_URL"),
            entities=entities,
        )


# ============================================================================================
#  STATE (survives a bot restart and a reboot)
# ============================================================================================

# Field names used by version 1.x of this bot, so an upgrade keeps the bound chat,
# the alert history and — importantly — the Telegram update offset.
STATE_ALIASES = {
    "czat_id": "chat_id",
    "prog_c": "threshold_c",
    "alarm_aktywny": "alarm_active",
    "ostatni_alarm_iso": "last_alert_iso",
    "ostatni_alarm_temp": "last_alert_temp",
    "alarmow_dzis": "alerts_today",
    "dzien_licznika": "counter_day",
    "wyciszenie_do_iso": "muted_until_iso",
    "offset_telegram": "telegram_offset",
    "zgloszony_brak_ha": "reported_ha_down",
    "zgloszony_stary_czujnik": "reported_stale_sensor",
}


@dataclass
class State:
    chat_id: str | None = None
    threshold_c: float | None = None     # /threshold overrides the value from .env
    alarm_active: bool = False
    last_alert_iso: str | None = None
    last_alert_temp: float | None = None
    alerts_today: int = 0
    counter_day: str = ""
    muted_until_iso: str | None = None
    telegram_offset: int = 0
    reported_ha_down: bool = False
    reported_stale_sensor: bool = False
    reported_ha_stalled: bool = False
    reported_recorder_stalled: bool = False
    last_alive_iso: str | None = None    # refreshed every few minutes; a gap = the bot was down

    @staticmethod
    def load(state_file: Path | None = None) -> "State":
        path = state_file or STATE_FILE
        if not path.exists():
            return State()
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as error:
            log.warning("Could not read %s (%s) — starting from scratch", path, error)
            return State()
        if not isinstance(stored, dict):
            log.warning("%s does not contain an object — starting from scratch", path)
            return State()

        known = {f.name for f in fields(State)}
        translated: dict[str, object] = {}
        migrated = []
        for key, value in stored.items():
            name = STATE_ALIASES.get(key, key)
            if name in known:
                translated[name] = value
                if name != key:
                    migrated.append(key)
            else:
                log.info("Ignoring unknown entry %r in %s", key, path)
        if migrated:
            backup = path.with_name(path.name + ".bak")
            try:
                shutil.copy2(path, backup)
                log.info("Migrated %s to the new field names (old copy: %s)", path, backup)
            except OSError as error:
                log.warning("Could not back up %s before migrating: %s", path, error)
        try:
            return State(**translated)
        except TypeError as error:
            log.warning("Could not apply %s (%s) — starting from scratch", path, error)
            return State()

    def save(self, state_file: Path | None = None) -> None:
        path = state_file or STATE_FILE
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False),
                            encoding="utf-8")
        # The file holds the Telegram chat id: keep it to the bot's own user.
        try:
            temporary.chmod(0o600)
        except OSError as error:
            log.debug("Could not tighten permissions on %s: %s", temporary, error)
        temporary.replace(path)


# ============================================================================================
#  HTTP CLIENTS: Home Assistant and Telegram
# ============================================================================================

class HAError(Exception):
    """Home Assistant did not answer properly."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status            # HTTP status, when Home Assistant sent one


def _fetch_json(request: urllib.request.Request, timeout_sec: int):
    with urllib.request.urlopen(request, timeout=timeout_sec) as response:
        return json.loads(response.read().decode("utf-8"))


class HomeAssistant:
    """Minimal read-only client for the Home Assistant REST API."""

    def __init__(self, url: str, token: str):
        self.url = url
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _get(self, path: str, params: dict[str, str] | None = None, timeout_sec: int = 25):
        address = f"{self.url}{path}"
        if params:
            address += "?" + urllib.parse.urlencode(params)
        try:
            return _fetch_json(urllib.request.Request(address, headers=self.headers), timeout_sec)
        except urllib.error.HTTPError as error:
            raise HAError(str(error), status=error.code) from error
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise HAError(str(error)) from error

    def alive(self) -> bool:
        return isinstance(self._get("/api/", timeout_sec=10), dict)

    def state(self, entity: str) -> dict:
        return self._get("/api/states/" + urllib.parse.quote(entity, safe=""))

    def states(self, entities: list[str]) -> dict[str, dict]:
        return {entity: self.state(entity) for entity in entities}

    def history(self, entities: list[str], since: datetime, until: datetime,
                attributes: bool = False) -> dict[str, list[dict]]:
        """Rows per entity: entity -> [{state, last_changed, last_updated?, attributes?}, ...].

        Home Assistant puts the row in force at `since` first. Without `attributes` only
        state changes come back, stripped to the bare state. With `attributes` every
        recorded update comes back in full: Hive's call for heat (climate hvac_action) is
        an attribute, so it changes while the climate state stays 'heat' or 'auto'.
        """
        params = {"filter_entity_id": ",".join(entities), "end_time": to_utc(until).isoformat()}
        if attributes:
            params["significant_changes_only"] = "0"
        else:
            params.update(no_attributes="true", minimal_response="true")
        result = self._get(f"/api/history/period/{to_utc(since).isoformat()}", params,
                           timeout_sec=40)
        grouped: dict[str, list[dict]] = {entity: [] for entity in entities}
        for series in result or []:
            if not series:
                continue
            entity = series[0].get("entity_id")
            if entity in grouped:
                grouped[entity] = series
        return grouped

    def logbook(self, since: datetime, until: datetime) -> list[dict]:
        """Logbook entries; the 'started'/'stopped' ones mark Home Assistant's own outages."""
        result = self._get(f"/api/logbook/{to_utc(since).isoformat()}",
                           {"end_time": to_utc(until).isoformat()}, timeout_sec=40)
        if not isinstance(result, list):
            raise HAError("the logbook did not return a list")
        return result


def send_ping(url: str, timeout_sec: int = 10) -> str | None:
    """Call the dead-man's-switch address. Returns None on success, else what went wrong.

    The address itself is a secret (anyone holding it can fake 'all is well'), so it is
    never logged or shown - only the error is.
    """
    if urllib.parse.urlsplit(url).scheme.lower() not in ("http", "https"):
        return "not a valid http(s) address"        # no file://, ftp:// or data: here
    try:
        request = urllib.request.Request(url, headers={"User-Agent": f"hive-bot/{VERSION}"})
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            response.read(512)
        return None
    except urllib.error.HTTPError as error:
        return f"HTTP {error.code}"
    except ValueError:
        return "not a valid http(s) address"        # the error text would quote the address
    except urllib.error.URLError as error:
        return f"no connection ({error.reason})"
    except OSError as error:
        return f"no connection ({type(error).__name__})"


class Telegram:
    """Minimal Bot API client (long polling)."""

    CHARACTER_LIMIT = 3800  # Telegram accepts 4096; leave room for the notice

    def __init__(self, token: str, locale: Locale):
        self.base = f"https://api.telegram.org/bot{token}"
        self.locale = locale

    def _call(self, method: str, payload: dict, timeout_sec: int = 30):
        request = urllib.request.Request(
            f"{self.base}/{method}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            response = _fetch_json(request, timeout_sec)
        except urllib.error.HTTPError as error:
            log.error("Telegram %s: HTTP %s %s", method, error.code, error.read()[:200])
            return None
        except (urllib.error.URLError, OSError, ValueError) as error:
            log.error("Telegram %s: %s", method, error)
            return None
        if not response.get("ok"):
            log.error("Telegram %s: %s", method, response)
            return None
        return response.get("result")

    def who_am_i(self):
        return self._call("getMe", {}, timeout_sec=15)

    def send(self, chat_id: str, text: str) -> None:
        if not chat_id:
            log.warning("No chat id — dropping message: %s", text.splitlines()[0])
            return
        if len(text) > self.CHARACTER_LIMIT:
            text = text[: self.CHARACTER_LIMIT] + self.locale.t("truncated")
        self._call("sendMessage",
                   {"chat_id": chat_id, "text": text, "disable_web_page_preview": True})

    def updates(self, offset: int, timeout_sec: int = 25) -> list | None:
        """Messages, or None when Telegram did not answer (then the caller must wait)."""
        return self._call(
            "getUpdates",
            {"offset": offset, "timeout": timeout_sec, "allowed_updates": ["message"]},
            timeout_sec=timeout_sec + 15,
        )


# ============================================================================================
#  PARSING AND HISTORY ANALYSIS
# ============================================================================================

# Every history becomes a timeline: back-to-back segments (start, end, value) that cover the
# asked window exactly. None means "not known": no row yet, the entity said 'unavailable' or
# 'unknown', or Home Assistant itself was not running. Unknown time is never counted as on
# and never as off; every total comes with how much of the window it actually covers.
#
# The arithmetic is done in UTC. Two local times that share one zone object are compared and
# subtracted as wall-clock times, which is wrong across the autumn clock change.

UTC = utc_timezone.utc
ON, OFF = "on", "off"
UNKNOWN_STATES = (None, "unavailable", "unknown")
# Hive's call for heat ('working' in the Hive API) as Home Assistant shows it.
HVAC_ACTION_VALUES = {"heating": ON, "idle": OFF, "off": OFF}
# Home Assistant writes its first states a little before it announces 'started'. Measured on
# a real install over 10 restarts: stop -> start took between 20 s and 3.5 min.
HA_BOOT_MARGIN_MIN = 5.0
# Durations are shown in whole minutes, so less than half a minute of missing data is noise.
COVERAGE_TOLERANCE_MIN = 0.5

Segment = tuple[datetime, datetime, object]


def to_number(value) -> float | None:
    try:
        number = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parse_ha_time(text, timezone) -> datetime | None:
    if text is None or text == "":
        return None
    try:
        if isinstance(text, (int, float)):
            return datetime.fromtimestamp(text, UTC).astimezone(timezone)
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).astimezone(timezone)
    except (ValueError, OverflowError, OSError):
        return None


def to_utc(moment: datetime) -> datetime:
    return moment.astimezone(UTC)


def elapsed_minutes(start: datetime, end: datetime) -> float:
    """Real elapsed minutes, also across a daylight-saving change."""
    return (to_utc(end) - to_utc(start)).total_seconds() / 60


def row_time(entry: dict) -> datetime | None:
    """When a history row took effect, in UTC.

    last_updated moves with every recorded update, attribute-only ones included;
    last_changed moves only with the state. Hive's call for heat is an attribute, so
    last_updated is the right stamp. Minimal rows carry last_changed only.
    """
    return parse_ha_time(entry.get("last_updated") or entry.get("last_changed"), UTC)


def on_off_value(entry: dict) -> str | None:
    state = entry.get("state")
    return state if state in (ON, OFF) else None


def heat_demand_value(entry: dict) -> str | None:
    """Hive's call for heat from a climate row, None when the row does not say."""
    if entry.get("state") in UNKNOWN_STATES:
        return None
    return HVAC_ACTION_VALUES.get((entry.get("attributes") or {}).get("hvac_action"))


def number_value(entry: dict) -> float | None:
    return to_number(entry.get("state"))


def _blank_out(segments: list[Segment], start: datetime, end: datetime) -> list[Segment]:
    """Mark [start, end) unknown."""
    result: list[Segment] = []
    for a, b, value in segments:
        if b <= start or a >= end or value is None:
            result.append((a, b, value))
            continue
        if a < start:
            result.append((a, start, value))
        result.append((max(a, start), min(b, end), None))
        if b > end:
            result.append((end, b, value))
    return result


def _merge(segments: list[Segment]) -> list[Segment]:
    merged: list[Segment] = []
    for a, b, value in segments:
        if b <= a:
            continue
        if merged and merged[-1][1] == a and merged[-1][2] == value:
            merged[-1] = (merged[-1][0], b, value)
        else:
            merged.append((a, b, value))
    return merged


def timeline(entries: list[dict], since: datetime, until: datetime, value=on_off_value,
             gaps=()) -> list[Segment]:
    """Segments covering [since, until) in UTC; `value` turns a row into on/off/number/None.

    The window starts unknown until the first row (Home Assistant sends the row in force at
    `since` first, when it has one). The last value runs to `until`, except through `gaps`:
    times when Home Assistant itself was not running, which are unknown. After a restart the
    last value resumes: Home Assistant writes no new row for a state that did not change
    (seen on a real install: three restarts in one evening, no new rows for unchanged
    sensors), and it would have written one if the state had changed.
    """
    since, until = to_utc(since), to_utc(until)
    if until <= since:
        return []
    points = [(stamp, value(entry)) for entry in entries
              if (stamp := row_time(entry)) is not None]
    points.sort(key=lambda point: point[0])
    segments: list[Segment] = []
    cursor, current = since, None
    for stamp, new_value in points:
        if stamp >= until:
            break
        if stamp > cursor:
            segments.append((cursor, stamp, current))
            cursor = stamp
        current = new_value
    segments.append((cursor, until, current))
    for start, end in gaps:
        segments = _blank_out(segments, to_utc(start), to_utc(end))
    return _merge(segments)


def clip(segments: list[Segment], start: datetime, end: datetime) -> list[Segment]:
    start, end = to_utc(start), to_utc(end)
    return [(max(a, start), min(b, end), value) for a, b, value in segments
            if b > start and a < end]


def span_minutes(segments: list[Segment]) -> float:
    return sum(elapsed_minutes(a, b) for a, b, _value in segments)


def known_minutes(segments: list[Segment]) -> float:
    return sum(elapsed_minutes(a, b) for a, b, value in segments if value is not None)


def on_minutes(segments: list[Segment]) -> float:
    return sum(elapsed_minutes(a, b) for a, b, value in segments if value == ON)


def is_complete(segments: list[Segment]) -> bool:
    return span_minutes(segments) - known_minutes(segments) < COVERAGE_TOLERANCE_MIN


def has_data(segments: list[Segment]) -> bool:
    """False also for a known sliver of a few seconds: that is a restart artefact, not data."""
    return known_minutes(segments) >= COVERAGE_TOLERANCE_MIN


@dataclass
class Run:
    """One stretch of 'on'. `minutes` counts known 'on' time only."""

    start: datetime
    end: datetime
    minutes: float
    unknown_min: float = 0.0       # short data gaps bridged inside the run, NOT counted
    open_start: bool = False       # data missing just before: it may have started earlier
    open_end: bool = False         # data missing just after: it may have lasted longer


def on_runs(segments: list[Segment], stitch_gap_min: float = 0.0) -> list[Run]:
    """Runs of 'on'. An unknown gap up to stitch_gap_min between two 'on' stretches joins
    them into one run for display, but the gap itself is not counted as 'on'."""
    runs: list[Run] = []
    for index, (start, end, value) in enumerate(segments):
        if value != ON:
            continue
        before = segments[index - 1] if index else None
        after = segments[index + 1] if index + 1 < len(segments) else None
        gap = before if before is not None and before[2] is None else None
        if (gap is not None and runs and runs[-1].end == gap[0]
                and elapsed_minutes(gap[0], gap[1]) <= stitch_gap_min):
            run = runs[-1]
            run.end = end
            run.minutes += elapsed_minutes(start, end)
            run.unknown_min += elapsed_minutes(gap[0], gap[1])
        else:
            run = Run(start, end, elapsed_minutes(start, end), open_start=gap is not None)
            runs.append(run)
        run.open_end = after is not None and after[2] is None
    return runs


def value_at(segments: list[Segment], moment: datetime):
    """The known value at `moment` (the temperature when a Boost started), else None."""
    moment = to_utc(moment)
    for start, end, value in segments:
        if start <= moment < end:
            return value
    return None


def temperature_stats(segments: list[Segment]) -> dict:
    """Min / max / time-weighted average over the known part of a numeric timeline."""
    known = [(a, b, value) for a, b, value in segments if value is not None]
    if not known:
        return {}
    lowest = min(known, key=lambda segment: segment[2])
    highest = max(known, key=lambda segment: segment[2])
    weight = sum(elapsed_minutes(a, b) for a, b, _value in known)
    weighted = sum(value * elapsed_minutes(a, b) for a, b, value in known)
    return {
        "min": lowest[2], "min_time": lowest[0],
        "max": highest[2], "max_time": highest[0],
        "average": weighted / weight if weight else known[-1][2],
        "first": known[0][2], "last": known[-1][2],
    }


def ha_downtime(logbook: list[dict], since: datetime, until: datetime,
                evidence=()) -> list[tuple[datetime, datetime]]:
    """When Home Assistant itself was not running, from its 'stopped'/'started' logbook entries.

    A clean shutdown logs 'stopped'. A crash or a power cut logs nothing, only 'started' on
    the way back; then the outage is taken to begin at the last sign of life before it (any
    history row or logbook entry, ignoring the boot itself). Without any sign it begins at
    `since`. Old values are not readings for those times.
    """
    since, until = to_utc(since), to_utc(until)
    events: list[tuple[datetime, str]] = []
    alive = [to_utc(stamp) for stamp in evidence if stamp is not None]
    for item in logbook:
        stamp = parse_ha_time(item.get("when"), UTC)
        if stamp is None:
            continue
        if item.get("domain") == "homeassistant" and item.get("message") in ("started", "stopped"):
            events.append((stamp, item["message"]))
        else:
            alive.append(stamp)
    events.sort(key=lambda event: event[0])

    gaps: list[tuple[datetime, datetime]] = []
    down_from: datetime | None = None
    running_since = since
    for stamp, kind in events:
        if kind == "stopped":
            down_from = down_from or stamp
            continue
        if down_from is None:
            cutoff = stamp - timedelta(minutes=HA_BOOT_MARGIN_MIN)
            down_from = max((moment for moment in alive if running_since <= moment < cutoff),
                            default=running_since)
        gaps.append((down_from, stamp))
        down_from, running_since = None, stamp
    if down_from is not None:
        gaps.append((down_from, until))

    merged: list[tuple[datetime, datetime]] = []
    for start, end in sorted((max(a, since), min(b, until)) for a, b in gaps):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


# ============================================================================================
#  HEALTH CHECK (HTTP on localhost)
# ============================================================================================

class Health:
    """Collects the bot's own state for /status and for the /health endpoint."""

    def __init__(self, timezone: ZoneInfo, language: str = "en", clock=None):
        self.lock = threading.Lock()
        self.timezone = timezone
        self.language = language
        # One source of time, so /status and /health agree with the bot's own clock.
        self.clock = clock or (lambda: datetime.now(timezone))
        self.started = self.clock()
        self.ha_ok = False
        self.ha_error: str | None = None
        self.ha_failing_since: datetime | None = None
        self.last_reading: datetime | None = None
        self.temperature: float | None = None
        self.sensor_age_min: float | None = None
        # 'off' (not watched), 'unknown' (not checked yet), 'ok', 'stalled', 'missing'
        self.pulse = "off"
        self.pulse_age_min: float | None = None
        self.recorder_ok: bool | None = None
        # 'off' (no BOT_PING_URL), 'ok', 'failing', 'held' (held back: something is wrong)
        self.ping = "off"
        self.last_ping: datetime | None = None
        self.ping_failing_since: datetime | None = None

    def snapshot(self) -> dict:
        with self.lock:
            now = self.clock()
            return {
                "ok": bool(self.ha_ok),
                "version": VERSION,
                "language": self.language,
                "started_at": self.started.isoformat(),
                "uptime_min": round(elapsed_minutes(self.started, now), 1),
                "ha_ok": self.ha_ok,
                "ha_error": self.ha_error,
                "temperature_c": self.temperature,
                "last_reading": self.last_reading.isoformat() if self.last_reading else None,
                "sensor_age_min": self.sensor_age_min,
                "ha_heartbeat": self.pulse,
                "ha_heartbeat_age_min": self.pulse_age_min,
                "recorder_ok": self.recorder_ok,
                "external_ping": self.ping,
                "last_ping": self.last_ping.isoformat() if self.last_ping else None,
            }


HEALTH: Health | None = None


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - name required by the library
        if self.path.rstrip("/") not in ("/health", ""):
            self.send_error(404, "No such resource")
            return
        data = HEALTH.snapshot() if HEALTH else {"ok": False, "version": VERSION}
        body = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        self.send_response(200 if data.get("ok") else 503)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # keep the journal quiet
        return


def start_health_server(port: int) -> bool:
    try:
        server = HTTPServer(("127.0.0.1", port), HealthHandler)
    except OSError as error:
        log.warning("Could not open /health on port %s: %s — is another instance running?",
                    port, error)
        return False
    threading.Thread(target=server.serve_forever, name="health", daemon=True).start()
    log.info("Health check: http://127.0.0.1:%s/health", port)
    return True


# ============================================================================================
#  BOT
# ============================================================================================

ALERT_CONTEXT_HOURS = 3             # an alert shows how the last hours went


@dataclass
class Window:
    """History for one command: rows per entity and what is known about the gaps in it."""

    rows: dict[str, list[dict]]
    gaps: list[tuple[datetime, datetime]]   # Home Assistant itself was not running
    checked: bool                           # False when the logbook could not be read


class Bot:
    def __init__(self, config: Config, state_file: Path | None = None):
        self.config = config
        self.locale = make_locale(config.language, config.timezone)
        self.timezone = self.locale.timezone
        self.state_file = state_file or STATE_FILE
        self.state = State.load(self.state_file)
        self.ha = HomeAssistant(config.ha_url, config.ha_token)
        self.tg = Telegram(config.telegram_token, self.locale)
        self.entity = config.entities
        if config.chat_id:
            self.state.chat_id = config.chat_id
        self.next_measurement = self.now()
        self.next_pulse = self.now()
        self.ping = send_ping                       # replaceable in tests
        self.recorder_failing_since: datetime | None = None
        self.reported_missing_pulse = False

        # One bot per process; the module global is what the HTTP handler reads.
        global HEALTH
        self.health = Health(self.timezone, config.language, clock=lambda: self.now())
        self.health.pulse = "unknown" if self.entity.get("heartbeat") else "off"
        self.health.ping = "held" if config.ping_url else "off"
        HEALTH = self.health

    # --- small helpers ---------------------------------------------------------------------

    def now(self) -> datetime:
        return datetime.now(self.timezone)

    def t(self, key: str, **kwargs) -> str:
        return self.locale.t(key, **kwargs)

    def start_of_day(self, moment: datetime | None = None) -> datetime:
        moment = moment or self.now()
        return datetime(moment.year, moment.month, moment.day, tzinfo=self.timezone)

    def day_bounds(self, day: date) -> tuple[datetime, datetime]:
        start = datetime(day.year, day.month, day.day, tzinfo=self.timezone)
        return start, min(start + timedelta(days=1), self.now())

    def moment(self, text: str | None) -> datetime | None:
        return parse_ha_time(text, self.timezone)

    # --- history with its coverage ---------------------------------------------------------

    def fetch(self, names: list[str], since: datetime, until: datetime) -> Window:
        """History of the named entities plus Home Assistant's own outages over the window."""
        wanted = {name: self.entity[name] for name in names}
        plain = [entity for name, entity in wanted.items() if name != "climate"]
        rows: dict[str, list[dict]] = {}
        if plain:
            rows.update(self.ha.history(plain, since, until))
        if "climate" in wanted:
            rows.update(self.ha.history([wanted["climate"]], since, until, attributes=True))
        evidence = [stamp for series in rows.values() for stamp in map(row_time, series) if stamp]
        try:
            logbook, checked = self.ha.logbook(since, until), True
        except HAError as error:
            log.warning("Could not read the logbook, so Home Assistant outages are not "
                        "checked: %s", error)
            logbook, checked = [], False
        return Window(rows={name: rows.get(entity, []) for name, entity in wanted.items()},
                      gaps=ha_downtime(logbook, since, until, evidence), checked=checked)

    def signal(self, window: Window, name: str, since: datetime, until: datetime,
               value=on_off_value) -> list[Segment]:
        return timeline(window.rows.get(name, []), since, until, value, window.gaps)

    def runs(self, segments: list[Segment]) -> list[Run]:
        return on_runs(segments, self.config.gap_stitch_min)

    def coverage_lines(self, segments: list[Segment]) -> list[str]:
        """Nothing when the data is complete; otherwise how much of the window is known."""
        span, known = span_minutes(segments), known_minutes(segments)
        if span <= 0 or is_complete(segments):
            return []
        if not has_data(segments):
            return [self.t("coverage_none", span=self.locale.duration(span))]
        return [self.t("coverage_partial", known=self.locale.duration(known),
                       span=self.locale.duration(span),
                       percent=self.locale.percent(known, span))]

    def coverage_suffix(self, segments: list[Segment]) -> str:
        if not segments or is_complete(segments):
            return ""
        return self.t("coverage_short",
                      percent=self.locale.percent(known_minutes(segments),
                                                  span_minutes(segments)))

    def measured(self, segments: list[Segment], minutes: float) -> str:
        """A duration with its coverage, or 'no data' when nothing in the window is known."""
        if not has_data(segments):
            return self.t("no_data")
        return self.locale.duration(minutes) + self.coverage_suffix(segments)

    def window_notes(self, window: Window) -> list[str]:
        lines = []
        if window.gaps:
            shown = [self.locale.time_range(start, end) for start, end in window.gaps[:4]]
            if len(window.gaps) > 4:
                shown.append(self.t("and_more", count=len(window.gaps) - 4))
            lines.append(self.t("coverage_ha_down", ranges=", ".join(shown)))
        if not window.checked:
            lines.append(self.t("coverage_unchecked"))
        return lines

    def run_note(self, run: Run) -> str:
        notes = []
        if run.unknown_min >= COVERAGE_TOLERANCE_MIN:
            notes.append(self.t("run_gap", gap=self.locale.duration(run.unknown_min)))
        if run.open_start:
            notes.append(self.t("run_open_start"))
        if run.open_end:
            notes.append(self.t("run_open_end"))
        return "".join("; " + note for note in notes)

    def heat_demand_text(self, data: dict) -> str:
        """Hive's call for heat right now, from the climate entity's current state."""
        value = heat_demand_value(data)
        action = (data.get("attributes") or {}).get("hvac_action")
        if value == ON:
            return self.t("heat_demand_on")
        if value == OFF:
            return self.t("heat_demand_off", action=action)
        return self.t("state_unknown", state=action or data.get("state") or "?")

    def water_text(self, state: str | None) -> str:
        if state == ON:
            return self.t("water_control_on")
        if state == OFF:
            return self.t("water_control_off")
        if state == "eco":
            return self.t("water_control_schedule")
        return self.t("state_unknown", state=state or "?")

    @property
    def threshold(self) -> float:
        return (self.state.threshold_c if self.state.threshold_c is not None
                else self.config.threshold_c)

    @property
    def muted(self) -> bool:
        until = self.moment(self.state.muted_until_iso)
        return bool(until and until > self.now())

    def send(self, text: str) -> None:
        self.tg.send(self.state.chat_id or "", text)

    def save(self) -> None:
        self.state.save(self.state_file)

    # --- temperature alerts ----------------------------------------------------------------

    def check_temperature(self) -> None:
        try:
            data = self.ha.state(self.entity["temperature"])
            self.mark_ha_ok()
        except HAError as error:
            self.mark_ha_down(str(error))
            return

        temperature = to_number(data.get("state"))
        reading = self.moment(data.get("last_changed")) or self.now()
        age_min = elapsed_minutes(reading, self.now())
        with self.health.lock:
            self.health.temperature = temperature
            self.health.last_reading = self.now()
            self.health.sensor_age_min = round(age_min, 1)

        self.check_sensor_freshness(temperature, age_min, data.get("state"))
        if temperature is None:
            return

        self.roll_daily_counter()
        last_alert = self.moment(self.state.last_alert_iso)
        since_alert = elapsed_minutes(last_alert, self.now()) if last_alert else None

        if temperature >= self.threshold:
            if not self.state.alarm_active:
                self.report_exceeded(temperature, repeat=False)
            elif self.config.repeat_min and since_alert and since_alert >= self.config.repeat_min:
                self.report_exceeded(temperature, repeat=True)
        elif self.state.alarm_active and temperature <= self.threshold - self.config.hysteresis_c:
            self.state.alarm_active = False
            self.save()
            if not self.muted:
                self.send("\n".join([
                    self.t("alert_back_below"),
                    self.t("alert_house", temperature=self.locale.number(temperature),
                           threshold=self.locale.number(self.threshold)),
                    self.t("alert_time", time=self.locale.clock(self.now())),
                ]))

    def report_exceeded(self, temperature: float, repeat: bool) -> None:
        if self.muted:
            # Do not latch the alarm: once the silence ends, the alert must still fire.
            log.info("Threshold exceeded (%s °C) but alerts are muted",
                     self.locale.number(temperature))
            return
        self.state.alarm_active = True
        self.state.last_alert_iso = self.now().isoformat()
        self.state.last_alert_temp = temperature
        self.state.alerts_today += 1
        self.save()
        header = self.t("alert_repeat_header" if repeat else "alert_over_header")
        self.send(f"{header}\n{self.describe_situation(temperature)}")

    def describe_situation(self, temperature: float) -> str:
        """Context for an alert: the facts the data supports, without a diagnosis.

        A single reading plus the hot-water state cannot tell a valve fault from sunshine,
        cooking or a full house, so the bot shows how the temperature, Hive's call for heat
        and hot-water control moved over the last hours, with their data coverage.
        """
        lines = [
            self.t("alert_house", temperature=self.locale.number(temperature),
                   threshold=self.locale.number(self.threshold)),
            self.t("alert_time", time=self.locale.clock(self.now())),
        ]
        try:
            states = self.ha.states([self.entity[name]
                                     for name in ("target", "climate", "water_heater")])
            lines += [
                self.t("now_target", target=self.locale.temperature(
                    to_number(states[self.entity["target"]].get("state")))),
                self.t("now_heating",
                       state=self.heat_demand_text(states[self.entity["climate"]])),
                self.t("now_water",
                       state=self.water_text(states[self.entity["water_heater"]].get("state"))),
            ]
            lines += self.recent_context()
        except HAError as error:
            lines.append(self.t("alert_details_failed", error=error))
        return "\n".join(lines)

    def recent_context(self, hours: int = ALERT_CONTEXT_HOURS) -> list[str]:
        until = self.now()
        since = until - timedelta(hours=hours)
        window = self.fetch(["temperature", "climate", "water_heater"], since, until)
        stats = temperature_stats(self.signal(window, "temperature", since, until, number_value))
        demand = self.signal(window, "climate", since, until, heat_demand_value)
        water = self.signal(window, "water_heater", since, until)
        return [
            self.t("alert_recent", hours=hours,
                   first=self.locale.number(stats.get("first")),
                   last=self.locale.number(stats.get("last")),
                   heating=self.measured(demand, on_minutes(demand)),
                   water=self.measured(water, on_minutes(water))),
            *self.window_notes(window),
            self.t("alert_recent_note"),
        ]

    def roll_daily_counter(self) -> None:
        today = self.now().date().isoformat()
        if self.state.counter_day != today:
            self.state.counter_day = today
            self.state.alerts_today = 0
            self.save()

    # --- health of the link ----------------------------------------------------------------

    def mark_ha_ok(self) -> None:
        with self.health.lock:
            self.health.ha_ok = True
            self.health.ha_error = None
            self.health.ha_failing_since = None
        if self.state.reported_ha_down:
            self.state.reported_ha_down = False
            self.save()
            self.send(self.t("ha_restored", time=self.locale.clock(self.now())))

    def mark_ha_down(self, description: str) -> None:
        with self.health.lock:
            self.health.ha_ok = False
            self.health.ha_error = description
            self.health.ha_failing_since = self.health.ha_failing_since or self.now()
            since = self.health.ha_failing_since
        log.warning("Home Assistant is not answering: %s", description)
        minutes = elapsed_minutes(since, self.now())
        if minutes >= self.config.ha_down_min and not self.state.reported_ha_down:
            self.state.reported_ha_down = True
            self.save()
            self.send("\n".join([
                self.t("ha_down_header"),
                self.t("ha_down_since", time=self.locale.clock(since),
                       duration=self.locale.duration(minutes)),
                self.t("ha_down_error", error=description),
                self.t("ha_down_note"),
            ]))

    def check_sensor_freshness(self, temperature: float | None, age_min: float,
                               raw_state: str | None = None) -> None:
        brak_danych = temperature is None
        stale = brak_danych or age_min >= self.config.stale_sensor_min
        if stale and not self.state.reported_stale_sensor:
            self.state.reported_stale_sensor = True
            self.save()
            # Two different situations, so two different messages: the sensor saying
            # "unavailable" is a fault, while an unchanged value is usually a calm house -
            # Hive only records a value when it changes - and is reported as information.
            if brak_danych:
                lines = [self.t("sensor_unavailable_header"),
                         self.t("sensor_stale_unavailable", state=raw_state or "?"),
                         self.t("sensor_stale_note")]
            else:
                lines = [self.t("sensor_unchanged_header"),
                         self.t("sensor_stale_age", age=self.locale.duration(age_min),
                                temperature=self.locale.number(temperature)),
                         self.t("sensor_unchanged_note")]
            self.send("\n".join(lines))
        elif not stale and self.state.reported_stale_sensor:
            self.state.reported_stale_sensor = False
            self.save()
            self.send(self.t("sensor_ok", temperature=self.locale.number(temperature)))

    # --- is Home Assistant actually working? ----------------------------------------------
    #
    # Answering the API is not enough: a stuck Home Assistant can still serve its last
    # states while no automation runs and nothing reaches the database. So the bot also
    # watches a heartbeat sensor that Home Assistant rewrites every 5 minutes, and checks
    # that the newest tick made it into the history. Neither check can see a power cut of
    # the machine the bot itself runs on - that is what the external ping is for.

    def check_pulse(self) -> None:
        """Every few minutes: heartbeat, history recording, external ping, sign of life."""
        entity = self.entity.get("heartbeat")
        if not entity:
            with self.health.lock:
                self.health.pulse = "off"
        elif self.health.ha_ok:
            self._check_heartbeat(entity)
        self._send_ping()
        self.state.last_alive_iso = self.now().isoformat()
        self.save()

    def _check_heartbeat(self, entity: str) -> None:
        try:
            data = self.ha.state(entity)
        except HAError as error:
            if error.status == 404:
                with self.health.lock:
                    self.health.pulse, self.health.pulse_age_min = "missing", None
                    self.health.recorder_ok = None
                if not self.reported_missing_pulse:
                    self.reported_missing_pulse = True
                    log.warning("Heartbeat entity %s does not exist - add the template sensor "
                                "from the heating package, or set BOT_ENTITY_HEARTBEAT=off",
                                entity)
            else:
                log.warning("Could not read the heartbeat %s: %s", entity, error)
            return
        self.reported_missing_pulse = False
        tick = self.moment(data.get("last_updated") or data.get("last_changed"))
        if tick is None or data.get("state") in UNKNOWN_STATES:
            age = None
            stalled = False         # just restarted: the sensor has no value yet
        else:
            age = max(elapsed_minutes(tick, self.now()), 0.0)
            stalled = age > self.config.heartbeat_max_min
        with self.health.lock:
            self.health.pulse = "stalled" if stalled else ("ok" if age is not None else "unknown")
            self.health.pulse_age_min = None if age is None else round(age, 1)

        if stalled and not self.state.reported_ha_stalled:
            self.state.reported_ha_stalled = True
            self.save()
            self.send("\n".join([
                self.t("ha_stalled_header"),
                self.t("ha_stalled_body", entity=entity, age=self.locale.duration(age),
                       time=self.locale.moment(tick, self.now()), every=PULSE_EVERY_MIN),
                self.t("ha_stalled_note"),
            ]))
        elif not stalled and age is not None and self.state.reported_ha_stalled:
            self.state.reported_ha_stalled = False
            self.save()
            self.send(self.t("ha_stalled_restored", time=self.locale.clock(self.now())))

        if age is not None and not stalled and age >= RECORDER_GRACE_MIN:
            self._check_recorder(entity, data.get("state"), tick)

    def _check_recorder(self, entity: str, value: str, tick: datetime) -> None:
        """Is the newest heartbeat in the database? History is read from there only."""
        try:
            rows = self.ha.history([entity], tick - timedelta(minutes=1), self.now())[entity]
        except HAError as error:
            log.warning("Could not read the heartbeat history: %s", error)
            return
        recorded = any(row.get("state") == value for row in rows)
        now = self.now()
        with self.health.lock:
            self.health.recorder_ok = recorded
        if recorded:
            self.recorder_failing_since = None
            if self.state.reported_recorder_stalled:
                self.state.reported_recorder_stalled = False
                self.save()
                self.send(self.t("recorder_restored", time=self.locale.clock(now)))
            return
        # Count from the tick that never arrived, so a recorder that is merely slow
        # (a nightly purge) gets the same margin as the heartbeat itself.
        self.recorder_failing_since = self.recorder_failing_since or tick
        minutes = elapsed_minutes(self.recorder_failing_since, now)
        if minutes > self.config.heartbeat_max_min and not self.state.reported_recorder_stalled:
            self.state.reported_recorder_stalled = True
            self.save()
            self.send("\n".join([
                self.t("recorder_stalled_header"),
                self.t("recorder_stalled_body",
                       time=self.locale.moment(self.recorder_failing_since, now),
                       age=self.locale.duration(minutes)),
                self.t("recorder_stalled_note"),
            ]))

    def all_is_well(self) -> bool:
        """What the external ping vouches for: HA answers, ticks and saves history."""
        with self.health.lock:
            return (self.health.ha_ok and self.health.pulse not in ("stalled", "unknown")
                    and self.health.recorder_ok is not False
                    and not self.state.reported_recorder_stalled)

    def _send_ping(self) -> None:
        if not self.config.ping_url:
            return
        now = self.now()
        if not self.all_is_well():
            # Staying silent is the alarm: the outside service notices the missing ping.
            with self.health.lock:
                self.health.ping = "held"
            return
        error = self.ping(self.config.ping_url)
        with self.health.lock:
            if error is None:
                if self.health.ping == "failing":
                    log.info("External ping works again")
                self.health.ping, self.health.last_ping = "ok", now
                self.health.ping_failing_since = None
            else:
                if self.health.ping != "failing":
                    log.warning("External ping failed: %s", error)
                self.health.ping = "failing"
                self.health.ping_failing_since = self.health.ping_failing_since or now

    def report_downtime(self) -> None:
        """After a start: say so if the bot itself was gone for longer than a restart."""
        last = self.moment(self.state.last_alive_iso)
        now = self.now()
        if last is None or elapsed_minutes(last, now) < self.config.heartbeat_max_min:
            return
        lines = [
            self.t("downtime_header"),
            self.t("downtime_body", range=self.locale.time_range(last, now),
                   duration=self.locale.duration(elapsed_minutes(last, now))),
        ]
        try:
            gaps = ha_downtime(self.ha.logbook(last, now), last, now, [])
            lines += self.window_notes(Window(rows={}, gaps=gaps, checked=True))
        except HAError as error:
            log.warning("Could not read the logbook for the downtime report: %s", error)
        lines.append(self.t("downtime_note"))
        self.send("\n".join(lines))

    # --- commands --------------------------------------------------------------------------

    def command_table(self) -> dict:
        english = {
            "/start": self.cmd_help,
            "/help": self.cmd_help,
            "/temperature": self.cmd_temperature,
            "/temp": self.cmd_temperature,
            "/heating": self.cmd_heating,
            "/boosts": self.cmd_boosts,
            "/water": self.cmd_water,
            "/today": self.cmd_today,
            "/status": self.cmd_status,
            "/threshold": self.cmd_threshold,
            "/mute": self.cmd_mute,
            "/unmute": self.cmd_unmute,
        }
        polish = {
            "/pomoc": self.cmd_help,
            "/temperatura": self.cmd_temperature,
            "/ogrzewanie": self.cmd_heating,
            "/boosty": self.cmd_boosts,
            "/woda": self.cmd_water,
            "/dzis": self.cmd_today,
            "/prog": self.cmd_threshold,
            "/cicho": self.cmd_mute,
            "/glosno": self.cmd_unmute,
        }
        return {**english, **polish}

    def handle_command(self, chat_id: str, text: str) -> None:
        words = text.strip().split()
        command = words[0].lower().split("@")[0]
        argument = words[1] if len(words) > 1 else ""

        if self.state.chat_id and str(chat_id) != str(self.state.chat_id):
            self.tg.send(chat_id, self.t("not_owner"))
            log.warning("Rejected %s from unknown chat %s", command, chat_id)
            return
        if not self.state.chat_id:
            self.state.chat_id = str(chat_id)
            self.save()
            log.info("Bot bound to chat %s", chat_id)

        handler = self.command_table().get(command)
        if not handler:
            self.send(self.t("unknown_command"))
            return
        try:
            handler(argument)
        except HAError as error:
            self.send(self.t("ha_error_reply", error=error))
        except Exception:  # noqa: BLE001 - one bad command must not kill the bot
            log.exception("Command %s failed", command)
            self.send(self.t("command_failed"))

    def cmd_help(self, _argument: str = "") -> None:
        self.send("\n".join([
            self.t("help_header"),
            self.t("help_intro", threshold=self.locale.number(self.threshold)),
            "",
            self.t("help_commands"),
            self.t("help_temperature"), self.t("help_heating"), self.t("help_boosts"),
            self.t("help_water"), self.t("help_today"), self.t("help_status"),
            self.t("help_threshold"), self.t("help_mute"), self.t("help_unmute"),
            self.t("help_help"),
            "",
            self.t("help_signals"),
            self.t("help_aliases"),
        ]))

    def cmd_temperature(self, _argument: str = "") -> None:
        names = ["temperature", "target", "mode", "water_heater", "climate"]
        states = self.ha.states([self.entity[name] for name in names])
        self.mark_ha_ok()
        get = lambda name: states[self.entity[name]]  # noqa: E731 - short local alias
        temperature = to_number(get("temperature").get("state"))
        reading = self.moment(get("temperature").get("last_changed"))
        age = max(0.0, elapsed_minutes(reading, self.now())) if reading else None
        raw_mode = get("mode").get("state")
        mode = {"schedule": self.t("mode_schedule"), "manual": self.t("mode_manual"),
                "off": self.t("mode_off")}.get(raw_mode) or self.t("state_unknown",
                                                                    state=raw_mode or "?")
        reading_line = (self.t("now_reading", time=self.locale.clock(reading)) if reading
                        else self.t("now_reading_none"))
        if age is not None:
            reading_line += self.t("now_reading_age", age=self.locale.duration(age))

        self.send("\n".join([
            self.t("now_header"),
            self.t("now_temperature", temperature=self.locale.temperature(temperature),
                   threshold=self.locale.number(self.threshold)),
            self.t("now_target", target=self.locale.temperature(to_number(get("target").get("state")))),
            self.t("now_heating", state=self.heat_demand_text(get("climate"))),
            self.t("now_mode", mode=mode),
            self.t("now_water", state=self.water_text(get("water_heater").get("state"))),
            reading_line,
        ]))

    def cmd_heating(self, argument: str = "") -> None:
        days = self._days(argument)
        until = self.now()
        since = self.start_of_day(until) - timedelta(days=days - 1)
        window = self.fetch(["climate"], since, until)
        self.mark_ha_ok()
        demand = self.signal(window, "climate", since, until, heat_demand_value)
        runs = self.runs(demand)

        if days == 1:
            lines = [self.t("heating_today_header", day=self.locale.short_date(until.date())), ""]
            if has_data(demand):
                lines.append(self.t("heating_total",
                                    duration=self.locale.duration(on_minutes(demand)),
                                    periods=self.locale.count(len(runs), "period"))
                             if runs else self.t("heating_total_zero"))
                lines += [self.run_line("heating_line", run) for run in runs]
            lines += self.coverage_lines(demand)
            if not runs:
                lines.append(self._last_heating_line())
        else:
            lines = [self.t("heating_range_header", days=days), ""]
            for offset in range(days):
                day = (until - timedelta(days=days - 1 - offset)).date()
                part = clip(demand, *self.day_bounds(day))
                if not has_data(part):
                    lines.append(self.t("heating_day_none", date=self.locale.day_month(day)))
                    continue
                lines.append(self.t("heating_day_line", date=self.locale.day_month(day),
                                    duration=self.locale.duration(on_minutes(part)),
                                    coverage=self.coverage_suffix(part)))
            lines += ["", self.t("heating_sum", duration=self.locale.duration(on_minutes(demand)))]
            lines += self.coverage_lines(demand)
        lines += self.window_notes(window)
        lines += ["", self.t("heating_signal_note")]
        self.send("\n".join(lines))

    def run_line(self, key: str, run: Run, with_date: bool = False) -> str:
        return self.t(key, date=f"{self.locale.day_month(run.start)} " if with_date else "",
                      start=self.locale.clock(run.start), end=self.locale.clock(run.end),
                      duration=self.locale.duration(run.minutes), note=self.run_note(run))

    def _last_heating(self, days_back: int = 30) -> tuple[Run | None, list[Segment]]:
        """The latest known call for heat in the last `days_back` days, and that timeline."""
        until = self.now()
        since = until - timedelta(days=days_back)
        window = self.fetch(["climate"], since, until)
        demand = self.signal(window, "climate", since, until, heat_demand_value)
        runs = self.runs(demand)
        return (runs[-1] if runs else None), demand

    def _last_heating_line(self, days_back: int = 30) -> str:
        run, demand = self._last_heating(days_back)
        if run:
            line = self.run_line("heating_last", run, with_date=True)
            after = clip(demand, run.end, self.now())
            if after and not is_complete(after):
                # Missing data after the run: a later call for heat cannot be ruled out.
                line += self.t("heating_last_after", percent=self.locale.percent(
                    known_minutes(after), span_minutes(after)))
            return line
        span = span_minutes(demand)
        return self.t("heating_last_none", days=days_back,
                      percent=self.locale.percent(known_minutes(demand), span) if span else "0")

    def cmd_boosts(self, argument: str = "") -> None:
        days = self._days(argument)
        until = self.now()
        since = self.start_of_day(until) - timedelta(days=days - 1)
        # For long ranges the temperature history is thousands of rows and adds nothing.
        with_temperature = days <= TEMPERATURE_HISTORY_DAYS
        names = ["boost_heating", "boost_water"] + (["temperature"] if with_temperature else [])
        window = self.fetch(names, since, until)
        self.mark_ha_ok()
        temperature = (self.signal(window, "temperature", since, until, number_value)
                       if with_temperature else [])

        header = (self.t("boosts_today_header", day=self.locale.short_date(until.date()))
                  if days == 1 else self.t("boosts_range_header", days=days))
        lines = [header, ""]
        for name, label_key in (("boost_heating", "boosts_heating_label"),
                                ("boost_water", "boosts_water_label")):
            label = self.t(label_key)
            segments = self.signal(window, name, since, until)
            runs = self.runs(segments)
            if not has_data(segments):
                lines.append(self.t("boosts_no_data", label=label))
                continue
            if not runs:
                lines.append(self.t("boosts_none", label=label,
                                    coverage=self.coverage_suffix(segments)))
                continue
            lines.append(self.t("boosts_count", label=label,
                                count=self.locale.count(len(runs), "boost"),
                                coverage=self.coverage_suffix(segments)))
            for run in runs:
                at_temperature = (value_at(temperature, run.start)
                                  if name == "boost_heating" else None)
                at = (self.t("boosts_at_temperature",
                             temperature=self.locale.number(at_temperature))
                      if at_temperature is not None else "")
                lines.append(self.t("boosts_line",
                                    date="" if days == 1 else f"{self.locale.day_month(run.start)} ",
                                    start=self.locale.clock(run.start),
                                    end=self.locale.clock(run.end),
                                    duration=self.locale.duration(run.minutes),
                                    note=self.run_note(run), at=at))
        lines += self.window_notes(window)
        if not with_temperature:
            lines += ["", self.t("boosts_range_note")]
        self.send("\n".join(lines))

    def cmd_water(self, _argument: str = "") -> None:
        until = self.now()
        since = self.start_of_day(until)
        window = self.fetch(["water_heater"], since, until)
        current = self.ha.state(self.entity["water_heater"]).get("state")
        self.mark_ha_ok()
        segments = self.signal(window, "water_heater", since, until)
        runs = self.runs(segments)
        lines = [
            self.t("water_header", day=self.locale.short_date(until.date())),
            "",
            self.t("water_now", state=self.water_text(current)),
            self.t("water_total", duration=self.measured(segments, on_minutes(segments))),
        ]
        if has_data(segments):
            lines += [self.run_line("water_line", run) for run in runs] or [self.t("no_windows")]
        lines += self.coverage_lines(segments)
        lines += self.window_notes(window)
        if self.config.hw_schedule_text:
            lines.append(self.t("water_schedule", schedule=self.config.hw_schedule_text))
        lines += ["", self.t("water_note")]
        self.send("\n".join(lines))

    def cmd_today(self, _argument: str = "") -> None:
        until = self.now()
        since = self.start_of_day(until)
        window = self.fetch(["temperature", "climate", "boost_heating", "boost_water",
                             "water_heater"], since, until)
        current = to_number(self.ha.state(self.entity["temperature"]).get("state"))
        self.mark_ha_ok()
        temperature = self.signal(window, "temperature", since, until, number_value)
        stats = temperature_stats(temperature)
        demand = self.signal(window, "climate", since, until, heat_demand_value)
        boost_heating = self.signal(window, "boost_heating", since, until)
        boost_water = self.signal(window, "boost_water", since, until)
        water = self.signal(window, "water_heater", since, until)

        def boosts(segments: list[Segment]) -> str:
            if not has_data(segments):
                return self.t("no_data")
            return str(len(self.runs(segments))) + self.coverage_suffix(segments)

        lines = [self.t("today_header", day=self.locale.short_date(until.date())), ""]
        lines.append(self.t("today_temperature", temperature=self.locale.temperature(current)))
        if stats:
            lines.append(self.t("today_minmax",
                                min=self.locale.number(stats["min"]),
                                min_time=self.locale.clock(stats["min_time"]),
                                max=self.locale.number(stats["max"]),
                                max_time=self.locale.clock(stats["max_time"]),
                                avg=self.locale.number(stats["average"], 2))
                         + self.coverage_suffix(temperature))
        else:
            lines.append(self.t("today_no_history"))
        heating_runs = self.runs(demand)
        lines += [
            (self.t("today_heating", duration=self.locale.duration(on_minutes(demand)),
                    periods=self.locale.count(len(heating_runs), "period"),
                    coverage=self.coverage_suffix(demand))
             if has_data(demand) else self.t("today_heating_none")),
            self.t("today_boost_heating", count=boosts(boost_heating)),
            self.t("today_boost_water", count=boosts(boost_water)),
            self.t("today_water", duration=self.measured(water, on_minutes(water))),
            self.t("today_alerts", count=self.state.alerts_today),
        ]
        lines += self.window_notes(window)
        lines += ["", self.t("today_signal_note")]
        self.send("\n".join(lines))

    def cmd_status(self, _argument: str = "") -> None:
        data = self.health.snapshot()
        try:
            self.ha.alive()
            ha_state = self.t("status_ha_ok")
            self.mark_ha_ok()
            if data["temperature_c"] is None:  # e.g. the first /status right after a start
                data["temperature_c"] = to_number(
                    self.ha.state(self.entity["temperature"]).get("state"))
        except HAError as error:
            ha_state = self.t("status_ha_down", error=error)
        last_alert = self.moment(self.state.last_alert_iso)
        muted_until = self.moment(self.state.muted_until_iso)
        age = data["sensor_age_min"]
        when_alert = (
            f"{last_alert.strftime('%d.%m %H:%M')}"
            f" ({self.locale.number(self.state.last_alert_temp)} °C)" if last_alert
            else self.t("status_last_alert_none")
        )
        self.send("\n".join([
            self.t("status_header_ok" if data["ok"] else "status_header_warn"),
            "",
            self.t("status_version", version=data["version"]),
            self.t("status_uptime", since=self.locale.clock(self.moment(data["started_at"])),
                   uptime=self.locale.duration(data["uptime_min"])),
            self.t("status_ha", state=ha_state),
            self.t("status_last_reading",
                   temperature=self.locale.temperature(data["temperature_c"])),
            (self.t("status_sensor_age", age=self.locale.duration(age)) if age is not None
             else self.t("status_sensor_age_none")),
            *self.status_watch_lines(data),
            self.t("status_threshold", threshold=self.locale.number(self.threshold),
                   hysteresis=self.locale.number(self.config.hysteresis_c)),
            self.t("status_interval", seconds=self.config.poll_sec),
            (self.t("status_alerts_muted", time=self.locale.clock(muted_until)) if self.muted
             else self.t("status_alerts_on")),
            self.t("status_alerts_today", count=self.state.alerts_today),
            self.t("status_last_alert", when=when_alert),
            self.t("status_language", language=self.locale.code),
            self.t("status_health_url", port=self.config.health_port),
        ]))

    def status_watch_lines(self, data: dict) -> list[str]:
        pulse, age = data["ha_heartbeat"], data["ha_heartbeat_age_min"]
        entity = self.entity.get("heartbeat") or ""
        if pulse == "ok":
            lines = [self.t("status_pulse_ok", age=self.locale.duration(age))]
        elif pulse == "stalled":
            lines = [self.t("status_pulse_stalled", age=self.locale.duration(age))]
        elif pulse == "missing":
            lines = [self.t("status_pulse_missing", entity=entity)]
        elif pulse == "unknown":
            lines = [self.t("status_pulse_unknown")]
        else:
            lines = [self.t("status_pulse_off")]
        if data["recorder_ok"] is False:
            lines.append(self.t("status_recorder_bad"))
        ping, last_ping = data["external_ping"], self.moment(data["last_ping"])
        if ping == "ok":
            lines.append(self.t("status_ping_ok", time=self.locale.clock(last_ping)))
        elif ping == "failing":
            lines.append(self.t("status_ping_failing"))
        elif ping == "held":
            lines.append(self.t("status_ping_held"))
        else:
            lines.append(self.t("status_ping_off"))
        return lines

    def cmd_threshold(self, argument: str = "") -> None:
        if not argument:
            self.send(self.t("threshold_show", threshold=self.locale.number(self.threshold)))
            return
        value = to_number(argument)
        low, high = THRESHOLD_RANGE
        if value is None or not low <= value <= high:
            self.send(self.t("threshold_bad", low=self.locale.number(low, 0),
                             high=self.locale.number(high, 0)))
            return
        self.state.threshold_c = value
        self.state.alarm_active = False
        self.save()
        self.send(self.t("threshold_set", threshold=self.locale.number(value)))

    def cmd_mute(self, argument: str = "") -> None:
        low, high = MUTE_RANGE
        minutes = to_number(argument) if argument else 60.0
        if minutes is None or not low <= minutes <= high:
            self.send(self.t("mute_bad", low=low, high=high))
            return
        until = self.now() + timedelta(minutes=minutes)
        self.state.muted_until_iso = until.isoformat()
        self.save()
        self.send(self.t("mute_done", duration=self.locale.duration(minutes),
                         time=self.locale.clock(until)))

    def cmd_unmute(self, _argument: str = "") -> None:
        self.state.muted_until_iso = None
        self.save()
        self.send(self.t("unmute_done"))

    @staticmethod
    def _days(argument: str) -> int:
        value = to_number(argument)
        return max(1, min(int(value), MAX_DAYS)) if value else 1

    # --- main loop -------------------------------------------------------------------------

    def run(self) -> None:
        start_health_server(self.config.health_port)
        me = self.tg.who_am_i()
        log.info("Starting bot %s (version %s), threshold %s °C, language %s",
                 (me or {}).get("username", "?"), VERSION,
                 self.locale.number(self.threshold), self.locale.code)
        if self.config.notify_on_start and self.state.chat_id:
            self.send(self.t("startup", version=VERSION,
                             threshold=self.locale.number(self.threshold),
                             seconds=self.config.poll_sec))

        # Measure immediately, so /health and the alerts are current without waiting
        # for the first Telegram poll to come back.
        self.check_temperature()
        self.next_measurement = self.now() + timedelta(seconds=self.config.poll_sec)
        self.report_downtime()
        self.check_pulse()
        self.next_pulse = self.now() + timedelta(minutes=PULSE_EVERY_MIN)

        while True:
            try:
                updates = self.tg.updates(self.state.telegram_offset, timeout_sec=25)
                if updates is None:
                    log.warning("Telegram is not answering — waiting 15 s")
                    threading.Event().wait(15)
                    updates = []
                for update in updates:
                    self.state.telegram_offset = max(self.state.telegram_offset,
                                                     update.get("update_id", 0) + 1)
                    self.save()
                    message = update.get("message") or {}
                    text = (message.get("text") or "").strip()
                    chat_id = str((message.get("chat") or {}).get("id") or "")
                    if text.startswith("/") and chat_id:
                        log.info("Command %r from chat %s", text, chat_id)
                        self.handle_command(chat_id, text)

                if self.now() >= self.next_measurement:
                    self.next_measurement = self.now() + timedelta(seconds=self.config.poll_sec)
                    self.check_temperature()
                if self.now() >= self.next_pulse:
                    self.next_pulse = self.now() + timedelta(minutes=PULSE_EVERY_MIN)
                    self.check_pulse()
            except KeyboardInterrupt:
                log.info("Stopping the bot (Ctrl+C)")
                return
            except Exception:  # noqa: BLE001 - the loop must not die
                log.exception("Unexpected error in the main loop — retrying in 15 s")
                threading.Event().wait(15)


# ============================================================================================
#  SELF-TEST (--check)
# ============================================================================================

def run_self_test(config: Config) -> int:
    locale = make_locale(config.language, config.timezone)
    print(f"Configuration file: {ENV_FILE}")
    print(f"Home Assistant:     {config.ha_url}")
    print(f"Language / zone:    {locale.code} / {config.timezone}")
    problems = 0

    ha = HomeAssistant(config.ha_url, config.ha_token)
    try:
        ha.alive()
        print("[OK]    Home Assistant answers and the token works.")
    except HAError as error:
        print(f"[FAIL]  Home Assistant: {error}")
        problems += 1

    for name in ("temperature", "target", "climate", "boost_heating", "boost_water",
                 "water_heater"):
        entity = config.entities[name]
        try:
            print(f"[OK]    {entity} = {ha.state(entity).get('state')}")
        except HAError as error:
            print(f"[FAIL]  {entity}: {error}")
            problems += 1

    # History is the most fragile call (/heating and /boosts depend on it), so test it too.
    try:
        timezone = ZoneInfo(config.timezone)
        until = datetime.now(timezone)
        since = until.replace(hour=0, minute=0, second=0, microsecond=0)
        entities = [config.entities[name] for name in ("boost_heating", "boost_water")]
        for entity, entries in ha.history(entities, since, until).items():
            segments = timeline(entries, since, until)
            print(f"[OK]    history {entity}: {len(entries)} rows, "
                  f"on for {locale.duration(on_minutes(segments))} today")
        climate = config.entities["climate"]
        entries = ha.history([climate], since, until, attributes=True)[climate]
        with_action = sum(1 for entry in entries
                          if (entry.get("attributes") or {}).get("hvac_action"))
        segments = timeline(entries, since, until, heat_demand_value)
        status = "[OK]  " if with_action else "[FAIL]"
        problems += 0 if with_action else 1
        print(f"{status}  history {climate}: {len(entries)} rows, {with_action} with "
              f"hvac_action; call for heat {locale.duration(on_minutes(segments))} today, "
              f"data for {locale.percent(known_minutes(segments), span_minutes(segments))}% "
              f"of the day")
    except HAError as error:
        print(f"[FAIL]  history (needed by /heating, /today and /boosts): {error}")
        problems += 1

    try:
        timezone = ZoneInfo(config.timezone)
        until = datetime.now(timezone)
        entries = ha.logbook(until - timedelta(days=1), until)
        print(f"[OK]    logbook: {len(entries)} entries in 24 h (used to find Home Assistant "
              f"outages)")
    except HAError as error:
        print(f"[WARN]  logbook: {error} - Home Assistant outages will not be detected")

    heartbeat = config.entities.get("heartbeat")
    if not heartbeat:
        print("[INFO]  Heartbeat not watched (BOT_ENTITY_HEARTBEAT=off): a stuck Home Assistant "
              "is only noticed when the temperature stops changing for hours.")
    else:
        try:
            data = ha.state(heartbeat)
            tick = parse_ha_time(data.get("last_updated") or data.get("last_changed"),
                                 ZoneInfo(config.timezone))
            age = elapsed_minutes(tick, datetime.now(ZoneInfo(config.timezone))) if tick else None
            fresh = age is not None and age <= config.heartbeat_max_min
            print(f"{'[OK]  ' if fresh else '[WARN]'}  heartbeat {heartbeat}: last tick "
                  f"{locale.duration(age) if age is not None else '?'} ago "
                  f"(alert after {config.heartbeat_max_min} min)")
            until = datetime.now(ZoneInfo(config.timezone))
            rows = ha.history([heartbeat], until - timedelta(minutes=30), until)[heartbeat]
            print(f"{'[OK]  ' if len(rows) > 1 else '[WARN]'}  heartbeat history: {len(rows)} rows "
                  f"in the last 30 min (Home Assistant is saving history)")
        except HAError as error:
            hint = (" - add the 'HA heartbeat' template sensor from the heating package, or set "
                    "BOT_ENTITY_HEARTBEAT=off" if error.status == 404 else "")
            print(f"[WARN]  heartbeat {heartbeat}: {error}{hint}")

    if config.ping_url:
        error = send_ping(config.ping_url)
        print("[OK]    external ping answered" if error is None
              else f"[FAIL]  external ping: {error}")
        problems += 0 if error is None else 1
    else:
        print("[INFO]  No BOT_PING_URL: nobody outside the house will notice if this machine "
              "loses power. See the README, 'Watchdogs'.")

    me = Telegram(config.telegram_token, locale).who_am_i()
    if me:
        print(f"[OK]    Telegram bot reachable (id {me.get('id')})")
    else:
        print("[FAIL]  The Telegram token does not work (check TELEGRAM_TOKEN).")
        problems += 1

    if config.chat_id:
        print("[OK]    Target chat is pinned in the configuration.")
    else:
        stored = State.load()
        if stored.chat_id:
            print("[INFO]  No TELEGRAM_CHAT_ID; the bot is bound to the chat stored in state.json.")
        else:
            print("[WARN]  No TELEGRAM_CHAT_ID and no stored chat: the FIRST person to send "
                  "/start claims this bot. Set TELEGRAM_CHAT_ID to your own chat id.")

    print(f"\nAlert threshold: {locale.number(config.threshold_c)} °C, "
          f"checking every {config.poll_sec} s.")
    print("Result: " + ("all good." if not problems else f"{problems} problem(s) to fix."))
    return 1 if problems else 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    config = Config.load()
    if "--check" in sys.argv or "--sprawdz" in sys.argv:   # --sprawdz: old name, kept working
        return run_self_test(config)
    Bot(config).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
