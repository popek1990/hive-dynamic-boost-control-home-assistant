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

VERSION = "2.0.1"
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
    "climate": "climate.thermostat_1",
    "heating_state": "binary_sensor.thermostat_1_state",   # are the radiators actually hot
    "boost_heating": "binary_sensor.thermostat_1_boost",
    "boost_water": "binary_sensor.hotwater_boost",
    "water_heater": "water_heater.thermostat_1",
}

FALSE_WORDS = ("0", "no", "false", "off", "nie")


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
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as error:
            raise HAError(str(error)) from error

    def alive(self) -> bool:
        return isinstance(self._get("/api/", timeout_sec=10), dict)

    def state(self, entity: str) -> dict:
        return self._get("/api/states/" + urllib.parse.quote(entity, safe=""))

    def states(self, entities: list[str]) -> dict[str, dict]:
        return {entity: self.state(entity) for entity in entities}

    def history(self, entities: list[str], since: datetime, until: datetime
                ) -> dict[str, list[dict]]:
        """State changes per entity: entity -> [{state, last_changed}, ...]."""
        result = self._get(
            f"/api/history/period/{since.isoformat()}",
            {
                "filter_entity_id": ",".join(entities),
                "end_time": until.isoformat(),
                "no_attributes": "true",
                "minimal_response": "true",
            },
            timeout_sec=40,
        )
        grouped: dict[str, list[dict]] = {entity: [] for entity in entities}
        for series in result or []:
            if not series:
                continue
            entity = series[0].get("entity_id")
            if entity in grouped:
                grouped[entity] = series
        return grouped


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

def to_number(value: str | None) -> float | None:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def parse_ha_time(text: str | None, timezone: ZoneInfo) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone)
    except ValueError:
        return None


UNKNOWN_STATES = (None, "unavailable", "unknown")


def elapsed_minutes(start: datetime, end: datetime) -> float:
    """Real elapsed minutes, also across a daylight-saving change.

    Python subtracts two datetimes that share one tzinfo object as if they were naive, so a
    run spanning the clock change would otherwise be an hour out twice a year.
    """
    return (end.astimezone(utc_timezone.utc)
            - start.astimezone(utc_timezone.utc)).total_seconds() / 60


def on_periods(entries: list[dict], since: datetime, until: datetime, timezone: ZoneInfo,
               stitch_gap_min: float = 0.0) -> list[tuple[datetime, datetime]]:
    """Periods where an on/off entity was 'on', clipped to [since, until].

    'unavailable' closes the current period instead of being skipped: Hive drops out for a
    couple of minutes at a time, and skipping those gaps counted the whole outage as heating.
    A gap shorter than stitch_gap_min that ends back in 'on' is treated as one run.
    """
    periods: list[tuple[datetime, datetime]] = []
    state: str | None = None
    start: datetime | None = None
    gap_started: datetime | None = None

    for entry in entries:
        raw = entry.get("state")
        moment = parse_ha_time(entry.get("last_changed") or entry.get("last_updated"),
                               timezone) or since
        moment = max(since, min(moment, until))

        if raw in UNKNOWN_STATES:
            if state == "on" and start is not None and moment > start:
                periods.append((start, moment))
                gap_started = moment
            state, start = None, None
            continue

        if state is None:
            if raw == "on":
                bridged = (
                    gap_started is not None
                    and periods
                    and elapsed_minutes(gap_started, moment) <= stitch_gap_min
                )
                start = periods.pop()[0] if bridged else moment
                state = "on"
            else:
                state, start = raw, moment
            gap_started = None
            continue

        if raw != state:
            if state == "on" and start is not None and moment > start:
                periods.append((start, moment))
            state, start = raw, moment
            gap_started = None

    if state == "on" and start is not None and until > start:
        periods.append((start, until))
    return [(a, b) for a, b in periods if b > a]


def total_minutes(periods: list[tuple[datetime, datetime]]) -> float:
    return sum(elapsed_minutes(start, end) for start, end in periods)


def value_at(entries: list[dict], moment: datetime, timezone: ZoneInfo) -> float | None:
    """Last numeric value no later than `moment` (the temperature when a Boost started)."""
    latest = None
    for entry in entries:
        stamp = parse_ha_time(entry.get("last_changed") or entry.get("last_updated"), timezone)
        if stamp is None or stamp > moment:
            break
        number = to_number(entry.get("state"))
        if number is not None:
            latest = number
    return latest


def temperature_stats(entries: list[dict], since: datetime, until: datetime,
                      timezone: ZoneInfo) -> dict:
    """Min / max / time-weighted average out of a temperature history."""
    points: list[tuple[datetime, float]] = []
    for entry in entries:
        stamp = parse_ha_time(entry.get("last_changed") or entry.get("last_updated"), timezone)
        number = to_number(entry.get("state"))
        if stamp and number is not None:
            points.append((max(stamp, since), number))
    if not points:
        return {}
    lowest = min(points, key=lambda point: point[1])
    highest = max(points, key=lambda point: point[1])
    weight_sum, weighted = 0.0, 0.0
    for index, (stamp, value) in enumerate(points):
        end = points[index + 1][0] if index + 1 < len(points) else until
        weight = max(0.0, (end - stamp).total_seconds())
        weighted += value * weight
        weight_sum += weight
    return {
        "min": lowest[1], "min_time": lowest[0],
        "max": highest[1], "max_time": highest[0],
        "average": (weighted / weight_sum) if weight_sum else points[-1][1],
        "now": points[-1][1],
    }


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

        # One bot per process; the module global is what the HTTP handler reads.
        global HEALTH
        self.health = Health(self.timezone, config.language, clock=lambda: self.now())
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

    def periods(self, entries: list[dict], since: datetime, until: datetime):
        return on_periods(entries, since, until, self.timezone, self.config.gap_stitch_min)

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

        self.check_sensor_freshness(temperature, age_min)
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
        """Context for an alert: target, whether radiators run, whether water is heating."""
        lines = [
            self.t("alert_house", temperature=self.locale.number(temperature),
                   threshold=self.locale.number(self.threshold)),
            self.t("alert_time", time=self.locale.clock(self.now())),
        ]
        try:
            target = to_number(self.ha.state(self.entity["target"]).get("state"))
            radiators = self.ha.state(self.entity["heating_state"]).get("state")
            water = self.ha.state(self.entity["water_heater"]).get("state")
            lines += [
                self.t("now_target", target=self.locale.temperature(target)),
                self.t("now_radiators",
                       state=self.t("yes_heating" if radiators == "on" else "no_heating")),
                self.t("now_water", state=self.t("water_on" if water == "on" else "water_off")),
            ]
            if radiators != "on" and water == "on":
                lines.append(self.t("alert_valve_hint"))
        except HAError as error:
            lines.append(self.t("alert_details_failed", error=error))
        return "\n".join(lines)

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

    def check_sensor_freshness(self, temperature: float | None, age_min: float) -> None:
        brak_danych = temperature is None
        stale = brak_danych or age_min >= self.config.stale_sensor_min
        if stale and not self.state.reported_stale_sensor:
            self.state.reported_stale_sensor = True
            self.save()
            # Two different situations, so two different messages: the sensor saying
            # "unavailable" is a fault, while an unchanged value may be a calm house.
            if brak_danych:
                lines = [self.t("sensor_unavailable_header"),
                         self.t("sensor_stale_unavailable"),
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
            self.t("help_aliases"),
        ]))

    def cmd_temperature(self, _argument: str = "") -> None:
        names = ["temperature", "target", "mode", "heating_state", "water_heater", "climate"]
        states = self.ha.states([self.entity[name] for name in names])
        self.mark_ha_ok()
        get = lambda name: states[self.entity[name]]  # noqa: E731 - short local alias
        temperature = to_number(get("temperature").get("state"))
        reading = self.moment(get("temperature").get("last_changed"))
        age = elapsed_minutes(reading, self.now()) if reading else None
        raw_mode = get("mode").get("state")
        mode = {"schedule": self.t("mode_schedule"), "manual": self.t("mode_manual"),
                "off": self.t("mode_off")}.get(raw_mode, raw_mode)
        action = (get("climate").get("attributes") or {}).get("hvac_action")

        radiators = self.t("now_radiators",
                           state=self.t("yes_heating") if get("heating_state").get("state") == "on"
                           else self.t("no_heating"))
        if action:
            radiators += self.t("now_hive_action", action=action)
        reading_line = (self.t("now_reading", time=self.locale.clock(reading)) if reading
                        else self.t("now_reading_none"))
        if age is not None:
            reading_line += self.t("now_reading_age", age=self.locale.duration(age))

        self.send("\n".join([
            self.t("now_header"),
            self.t("now_temperature", temperature=self.locale.temperature(temperature),
                   threshold=self.locale.number(self.threshold)),
            self.t("now_target", target=self.locale.temperature(to_number(get("target").get("state")))),
            radiators,
            self.t("now_mode", mode=mode),
            self.t("now_water",
                   state=self.t("water_on") if get("water_heater").get("state") == "on"
                   else self.t("water_off")),
            reading_line,
        ]))

    def cmd_heating(self, argument: str = "") -> None:
        days = self._days(argument)
        until = self.now()
        since = self.start_of_day(until) - timedelta(days=days - 1)
        entity = self.entity["heating_state"]
        periods = self.periods(self.ha.history([entity], since, until)[entity], since, until)
        self.mark_ha_ok()

        if days == 1:
            lines = [self.t("heating_today_header", day=self.locale.short_date(until.date())), ""]
            if periods:
                lines.append(self.t("heating_total",
                                    duration=self.locale.duration(total_minutes(periods)),
                                    periods=self.locale.count(len(periods), "period")))
                lines += [
                    self.t("heating_line", start=self.locale.clock(start),
                           end=self.locale.clock(end),
                           duration=self.locale.duration(elapsed_minutes(start, end)))
                    for start, end in periods
                ]
            else:
                lines.append(self.t("heating_zero"))
                last = self._last_heating()
                if last:
                    start, end = last
                    lines.append(self.t("heating_last", date=self.locale.day_month(start),
                                        start=self.locale.clock(start),
                                        end=self.locale.clock(end),
                                        duration=self.locale.duration(elapsed_minutes(start, end))))
            self.send("\n".join(lines))
            return

        lines = [self.t("heating_range_header", days=days), ""]
        for offset in range(days):
            day = (until - timedelta(days=days - 1 - offset)).date()
            day_start, day_end = self.day_bounds(day)
            minutes = total_minutes([
                (max(start, day_start), min(end, day_end))
                for start, end in periods if end > day_start and start < day_end
            ])
            key = "heating_day_line" if minutes else "heating_day_empty"
            lines.append(self.t(key, date=self.locale.day_month(day),
                                duration=self.locale.duration(minutes)))
        lines += ["", self.t("heating_sum", duration=self.locale.duration(total_minutes(periods)))]
        self.send("\n".join(lines))

    def _last_heating(self, days_back: int = 30) -> tuple[datetime, datetime] | None:
        until = self.now()
        since = until - timedelta(days=days_back)
        entity = self.entity["heating_state"]
        periods = self.periods(self.ha.history([entity], since, until)[entity], since, until)
        return periods[-1] if periods else None

    def cmd_boosts(self, argument: str = "") -> None:
        days = self._days(argument)
        until = self.now()
        since = self.start_of_day(until) - timedelta(days=days - 1)
        # For long ranges the temperature history is thousands of rows and adds nothing.
        with_temperature = days <= TEMPERATURE_HISTORY_DAYS
        entities = [self.entity["boost_heating"], self.entity["boost_water"]]
        if with_temperature:
            entities.append(self.entity["temperature"])
        history = self.ha.history(entities, since, until)
        self.mark_ha_ok()

        header = (self.t("boosts_today_header", day=self.locale.short_date(until.date()))
                  if days == 1 else self.t("boosts_range_header", days=days))
        lines = [header, ""]
        for entity, label_key in ((self.entity["boost_heating"], "boosts_heating_label"),
                                  (self.entity["boost_water"], "boosts_water_label")):
            label = self.t(label_key)
            periods = self.periods(history[entity], since, until)
            if not periods:
                lines.append(self.t("boosts_none", label=label))
                continue
            lines.append(self.t("boosts_count", label=label,
                                count=self.locale.count(len(periods), "boost")))
            for start, end in periods:
                temperature = (value_at(history.get(self.entity["temperature"], []), start,
                                        self.timezone)
                               if with_temperature and entity == self.entity["boost_heating"]
                               else None)
                at = (self.t("boosts_at_temperature", temperature=self.locale.number(temperature))
                      if temperature is not None else "")
                lines.append(self.t("boosts_line",
                                    date="" if days == 1 else f"{self.locale.day_month(start)} ",
                                    start=self.locale.clock(start), end=self.locale.clock(end),
                                    duration=self.locale.duration(elapsed_minutes(start, end)),
                                    at=at))
        if not with_temperature:
            lines += ["", self.t("boosts_range_note")]
        self.send("\n".join(lines))

    def cmd_water(self, _argument: str = "") -> None:
        until = self.now()
        since = self.start_of_day(until)
        entity = self.entity["water_heater"]
        periods = self.periods(self.ha.history([entity], since, until)[entity], since, until)
        current = self.ha.state(entity).get("state")
        self.mark_ha_ok()
        lines = [
            self.t("water_header", day=self.locale.short_date(until.date())),
            "",
            self.t("water_now",
                   state=self.t("water_on") if current == "on" else self.t("water_off")),
            self.t("water_total", duration=self.locale.duration(total_minutes(periods))),
        ]
        lines += [self.t("water_line", start=self.locale.clock(start), end=self.locale.clock(end))
                  for start, end in periods] or [self.t("no_windows")]
        if self.config.hw_schedule_text:
            lines.append(self.t("water_schedule", schedule=self.config.hw_schedule_text))
        self.send("\n".join(lines))

    def cmd_today(self, _argument: str = "") -> None:
        until = self.now()
        since = self.start_of_day(until)
        names = ["temperature", "heating_state", "boost_heating", "boost_water", "water_heater"]
        history = self.ha.history([self.entity[name] for name in names], since, until)
        current = to_number(self.ha.state(self.entity["temperature"]).get("state"))
        self.mark_ha_ok()
        stats = temperature_stats(history[self.entity["temperature"]], since, until, self.timezone)
        heating = self.periods(history[self.entity["heating_state"]], since, until)
        boost_heating = self.periods(history[self.entity["boost_heating"]], since, until)
        boost_water = self.periods(history[self.entity["boost_water"]], since, until)
        water = self.periods(history[self.entity["water_heater"]], since, until)

        lines = [self.t("today_header", day=self.locale.short_date(until.date())), ""]
        lines.append(self.t("today_temperature", temperature=self.locale.temperature(current)))
        if stats:
            lines.append(self.t("today_minmax",
                                min=self.locale.number(stats["min"]),
                                min_time=self.locale.clock(stats["min_time"]),
                                max=self.locale.number(stats["max"]),
                                max_time=self.locale.clock(stats["max_time"]),
                                avg=self.locale.number(stats["average"], 2)))
        else:
            lines.append(self.t("today_no_history"))
        lines += [
            self.t("today_radiators", duration=self.locale.duration(total_minutes(heating)),
                   periods=self.locale.count(len(heating), "period")),
            self.t("today_boost_heating", count=len(boost_heating)),
            self.t("today_boost_water", count=len(boost_water)),
            self.t("today_water", duration=self.locale.duration(total_minutes(water))),
            self.t("today_alerts", count=self.state.alerts_today),
        ]
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
            self.t("status_sensor_age",
                   age=self.locale.duration(age) if age is not None else self.t("no_data")),
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

    for name in ("temperature", "target", "heating_state", "boost_heating", "boost_water",
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
        entities = [config.entities[name] for name in
                    ("heating_state", "boost_heating", "boost_water")]
        for entity, entries in ha.history(entities, since, until).items():
            periods = on_periods(entries, since, until, timezone, config.gap_stitch_min)
            print(f"[OK]    history {entity}: {len(entries)} rows, "
                  f"on for {locale.duration(total_minutes(periods))} today")
    except HAError as error:
        print(f"[FAIL]  history (needed by /heating and /boosts): {error}")
        problems += 1

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
