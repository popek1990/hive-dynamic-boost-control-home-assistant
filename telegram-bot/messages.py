#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Locales and message catalogue for the Hive heating bot.

Everything the user can see lives here. The bot code never contains a
user-facing string: it asks a Locale for one. That keeps the code English and
the output translatable, and it lets the test suite prove that both languages
know exactly the same keys.

Adding a language:
  1. copy a MESSAGES block, translate the values (keep the {placeholders}),
  2. add an entry to WEEKDAYS, PLURALS and DECIMAL_SEPARATOR,
  3. run the tests - a missing key fails the suite.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

DEFAULT_LANGUAGE = "en"

# A comma is the decimal separator in Polish, a dot in English.
DECIMAL_SEPARATOR = {"en": ".", "pl": ","}

WEEKDAYS = {
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
    "pl": ("poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela"),
}

# English needs two forms, Polish three (1 / 2-4 / 5+ with the teens exception).
PLURALS = {
    "en": {
        "period": ("period", "periods"),
        "boost": ("boost", "boosts"),
        "alert": ("alert", "alerts"),
    },
    "pl": {
        "period": ("okresie", "okresach", "okresach"),
        "boost": ("boost", "boosty", "boostów"),
        "alert": ("alert", "alerty", "alertów"),
    },
}

MESSAGES = {
    "en": {
        # --- units and shared words ---
        "no_data": "no data",
        "unit_hour": "h",
        "unit_min": "min",
        "yes_heating": "HEATING",
        "no_heating": "not heating",
        "water_on": "on",
        "water_off": "off",
        "none_today": "none",
        "no_windows": "  (no windows)",
        "truncated": "\n… (message shortened)",
        "mode_schedule": "schedule",
        "mode_manual": "manual",
        "mode_off": "off",
        # --- /help ---
        "help_header": "🏠 Hive heating bot",
        "help_intro": "I watch the house temperature and alert you above {threshold} °C.",
        "help_commands": "Commands:",
        "help_temperature": "/temperature — what the house looks like right now",
        "help_heating": "/heating — how long the radiators ran today (/heating 7 = 7 days)",
        "help_boosts": "/boosts — when Boost was used (/boosts 7 = 7 days)",
        "help_water": "/water — hot water state and today's windows",
        "help_today": "/today — one-screen summary of the day",
        "help_status": "/status — bot health and the link to Home Assistant",
        "help_threshold": "/threshold 23 — show or change the alert threshold",
        "help_mute": "/mute 120 — silence alerts for 120 minutes",
        "help_unmute": "/unmute — turn alerts back on",
        "help_help": "/help — this list",
        "help_aliases": "Polish command names work too (/temperatura, /ogrzewanie, /boosty, /woda, /dzis, /prog, /cicho, /glosno, /pomoc).",
        # --- /temperature ---
        "now_header": "🌡 Right now",
        "now_temperature": "Temperature: {temperature}   (alert threshold {threshold} °C)",
        "now_target": "Thermostat target: {target}",
        "now_radiators": "Radiators: {state}",
        "now_hive_action": " (Hive action: {action})",
        "now_mode": "Heating mode: {mode}",
        "now_water": "Hot water: {state}",
        "now_reading": "Reading from: {time}",
        "now_reading_age": " ({age} ago)",
        "now_reading_none": "Reading from: unknown",
        # --- /heating ---
        "heating_today_header": "🔥 Heating — today ({day})",
        "heating_range_header": "🔥 Heating — last {days} days",
        "heating_total": "Total: {duration} in {periods}",
        "heating_line": "  • {start} – {end}  ({duration})",
        "heating_zero": "Total: 0 min — the radiators did not run today.",
        "heating_last": "Last run: {date}, {start} – {end} ({duration})",
        "heating_day_line": "{date}: {duration}",
        "heating_day_empty": "{date}: —",
        "heating_sum": "Total: {duration}",
        # --- /boosts ---
        "boosts_today_header": "⚡ Boosts — today ({day})",
        "boosts_range_header": "⚡ Boosts — last {days} days",
        "boosts_heating_label": "Heating",
        "boosts_water_label": "Hot water",
        "boosts_none": "{label}: none",
        "boosts_count": "{label} — {count}:",
        "boosts_line": "  • {date}{start} – {end} ({duration}){at}",
        "boosts_at_temperature": ", at {temperature} °C",
        "boosts_range_note": "Temperature at the moment of the Boost is shown for ranges up to 7 days.",
        # --- /water ---
        "water_header": "🚿 Hot water — today ({day})",
        "water_now": "Now: {state}",
        "water_total": "On for: {duration}",
        "water_line": "  • {start} – {end}",
        "water_schedule": "\nSchedule: {schedule}",
        # --- /today ---
        "today_header": "📋 Day summary — {day}",
        "today_temperature": "Temperature: now {temperature}",
        "today_minmax": "  min {min} °C ({min_time}), max {max} °C ({max_time}), average {avg} °C",
        "today_no_history": "  (no temperature history for today)",
        "today_radiators": "Radiators: {duration} in {periods}",
        "today_boost_heating": "Heating Boosts: {count}",
        "today_boost_water": "Hot water Boosts: {count}",
        "today_water": "Hot water on for: {duration}",
        "today_alerts": "Temperature alerts today: {count}",
        # --- /status ---
        "status_header_ok": "🩺 Bot status",
        "status_header_warn": "⚠️ Bot status — attention",
        "status_version": "Version: {version}",
        "status_uptime": "Running since: {since} ({uptime})",
        "status_ha": "Home Assistant: {state}",
        "status_ha_ok": "OK",
        "status_ha_down": "NO CONNECTION ({error})",
        "status_last_reading": "Last temperature reading: {temperature}",
        "status_sensor_age": "Sensor data older than: {age}",
        "status_threshold": "Alert threshold: {threshold} °C (hysteresis {hysteresis} °C)",
        "status_interval": "Checking every: {seconds} s",
        "status_alerts_muted": "Alerts: MUTED until {time}",
        "status_alerts_on": "Alerts: on",
        "status_alerts_today": "Alerts today: {count}",
        "status_last_alert": "Last alert: {when}",
        "status_last_alert_none": "none",
        "status_health_url": "Health over HTTP: http://127.0.0.1:{port}/health",
        "status_language": "Language: {language}",
        # --- /threshold, /mute, /unmute ---
        "threshold_show": "Alert threshold: {threshold} °C.\nTo change it: /threshold 22.5",
        "threshold_set": "✅ New alert threshold: {threshold} °C.",
        "threshold_bad": "Give the threshold in degrees, for example /threshold 23 or /threshold 22.5 (range {low}–{high}).",
        "mute_done": "🔇 Alerts muted for {duration} (until {time}).",
        "mute_bad": "Give the number of minutes, for example /mute 120 (range {low}–{high}).",
        "unmute_done": "🔔 Alerts are back on.",
        # --- alerts ---
        "alert_over_header": "🔥 Temperature threshold exceeded",
        "alert_repeat_header": "🔔 Still too warm",
        "alert_house": "House: {temperature} °C   (threshold {threshold} °C)",
        "alert_time": "Time: {time}",
        "alert_valve_hint": "ℹ️ The radiators are not being called for and the house is still warming up — that looks like a leaking valve.",
        "alert_details_failed": "(could not fetch details from Home Assistant: {error})",
        "alert_back_below": "✅ Temperature is back below the threshold",
        # --- health of the link ---
        "ha_down_header": "⚠️ No contact with Home Assistant",
        "ha_down_since": "Since: {time} ({duration})",
        "ha_down_error": "Error: {error}",
        "ha_down_note": "I cannot watch the temperature until Home Assistant answers.",
        "ha_restored": "✅ Contact with Home Assistant restored ({time}).",
        "sensor_stale_header": "⚠️ The Hive sensor stopped refreshing",
        "sensor_stale_unavailable": "The sensor reports no data (unavailable).",
        "sensor_stale_age": "Last change of value: {age} ago ({temperature} °C).",
        "sensor_stale_note": "Usually a Hive cloud blip. If it lasts, check the integration.",
        "sensor_ok": "✅ The Hive sensor is refreshing again ({temperature} °C).",
        # --- misc ---
        "startup": "🤖 Bot started (version {version}).\nAlert threshold: {threshold} °C. Checking every {seconds} s.\nCommand list: /help",
        "unknown_command": "I do not know that command. Send /help for the list.",
        "not_owner": "⛔ This bot only answers its owner.",
        "ha_error_reply": "⚠️ Home Assistant did not answer: {error}",
        "command_failed": "⚠️ That command broke. Details are in the bot log.",
    },
    "pl": {
        "no_data": "brak danych",
        "unit_hour": "h",
        "unit_min": "min",
        "yes_heating": "GRZEJĄ",
        "no_heating": "nie grzeją",
        "water_on": "włączona",
        "water_off": "wyłączona",
        "none_today": "brak",
        "no_windows": "  (brak okien)",
        "truncated": "\n… (wiadomość skrócona)",
        "mode_schedule": "harmonogram",
        "mode_manual": "ręczny",
        "mode_off": "wyłączony",
        "help_header": "🏠 Bot ogrzewania Hive",
        "help_intro": "Pilnuję temperatury w domu i alarmuję powyżej {threshold} °C.",
        "help_commands": "Komendy:",
        "help_temperature": "/temperatura — co jest teraz w domu",
        "help_heating": "/ogrzewanie — ile dziś grzały grzejniki (/ogrzewanie 7 = 7 dni)",
        "help_boosts": "/boosty — kiedy włączano boosty (/boosty 7 = 7 dni)",
        "help_water": "/woda — stan ciepłej wody i dzisiejsze okna grzania",
        "help_today": "/dzis — skrót całego dnia",
        "help_status": "/status — health check bota i łączności z Home Assistantem",
        "help_threshold": "/prog 23 — pokaż lub ustaw próg alertu",
        "help_mute": "/cicho 120 — wycisz alerty na 120 minut",
        "help_unmute": "/glosno — włącz alerty z powrotem",
        "help_help": "/pomoc — ta lista",
        "help_aliases": "Angielskie nazwy komend też działają (/temperature, /heating, /boosts, /water, /today, /threshold, /mute, /unmute, /help).",
        "now_header": "🌡 Teraz w domu",
        "now_temperature": "Temperatura: {temperature}   (próg alertu {threshold} °C)",
        "now_target": "Cel termostatu: {target}",
        "now_radiators": "Grzejniki: {state}",
        "now_hive_action": " (akcja Hive: {action})",
        "now_mode": "Tryb ogrzewania: {mode}",
        "now_water": "Ciepła woda: {state}",
        "now_reading": "Odczyt z: {time}",
        "now_reading_age": " ({age} temu)",
        "now_reading_none": "Odczyt z: nieznany",
        "heating_today_header": "🔥 Ogrzewanie — dziś ({day})",
        "heating_range_header": "🔥 Ogrzewanie — ostatnie {days} dni",
        "heating_total": "Razem: {duration} w {periods}",
        "heating_line": "  • {start} – {end}  ({duration})",
        "heating_zero": "Razem: 0 min — grzejniki dziś nie grzały.",
        "heating_last": "Ostatnie grzanie: {date}, {start} – {end} ({duration})",
        "heating_day_line": "{date}: {duration}",
        "heating_day_empty": "{date}: —",
        "heating_sum": "Razem: {duration}",
        "boosts_today_header": "⚡ Boosty — dziś ({day})",
        "boosts_range_header": "⚡ Boosty — ostatnie {days} dni",
        "boosts_heating_label": "Ogrzewanie",
        "boosts_water_label": "Ciepła woda",
        "boosts_none": "{label}: brak",
        "boosts_count": "{label} — {count}:",
        "boosts_line": "  • {date}{start} – {end} ({duration}){at}",
        "boosts_at_temperature": ", przy {temperature} °C",
        "boosts_range_note": "Temperaturę w chwili boostu pokazuję dla zakresów do 7 dni.",
        "water_header": "🚿 Ciepła woda — dziś ({day})",
        "water_now": "Teraz: {state}",
        "water_total": "Razem włączona: {duration}",
        "water_line": "  • {start} – {end}",
        "water_schedule": "\nHarmonogram: {schedule}",
        "today_header": "📋 Podsumowanie dnia — {day}",
        "today_temperature": "Temperatura: teraz {temperature}",
        "today_minmax": "  min {min} °C ({min_time}), max {max} °C ({max_time}), średnio {avg} °C",
        "today_no_history": "  (brak historii temperatury z dziś)",
        "today_radiators": "Grzejniki: {duration} w {periods}",
        "today_boost_heating": "Boost ogrzewania: {count}",
        "today_boost_water": "Boost ciepłej wody: {count}",
        "today_water": "Ciepła woda włączona: {duration}",
        "today_alerts": "Alertów temperatury dziś: {count}",
        "status_header_ok": "🩺 Status bota",
        "status_header_warn": "⚠️ Status bota — uwaga",
        "status_version": "Wersja: {version}",
        "status_uptime": "Działa od: {since} ({uptime})",
        "status_ha": "Home Assistant: {state}",
        "status_ha_ok": "OK",
        "status_ha_down": "BRAK ŁĄCZNOŚCI ({error})",
        "status_last_reading": "Ostatni odczyt temperatury: {temperature}",
        "status_sensor_age": "Dane z czujnika starsze o: {age}",
        "status_threshold": "Próg alertu: {threshold} °C (histereza {hysteresis} °C)",
        "status_interval": "Sprawdzam co: {seconds} s",
        "status_alerts_muted": "Alerty: WYCISZONE do {time}",
        "status_alerts_on": "Alerty: włączone",
        "status_alerts_today": "Alertów dziś: {count}",
        "status_last_alert": "Ostatni alert: {when}",
        "status_last_alert_none": "brak",
        "status_health_url": "Health po HTTP: http://127.0.0.1:{port}/health",
        "status_language": "Język: {language}",
        "threshold_show": "Próg alertu: {threshold} °C.\nZmiana: /prog 22,5",
        "threshold_set": "✅ Nowy próg alertu: {threshold} °C.",
        "threshold_bad": "Podaj próg w stopniach, np. /prog 23 albo /prog 22,5 (zakres {low}–{high}).",
        "mute_done": "🔇 Alerty wyciszone na {duration} (do {time}).",
        "mute_bad": "Podaj liczbę minut, np. /cicho 120 (zakres {low}–{high}).",
        "unmute_done": "🔔 Alerty znów włączone.",
        "alert_over_header": "🔥 Przekroczony próg temperatury",
        "alert_repeat_header": "🔔 Nadal za ciepło",
        "alert_house": "Dom: {temperature} °C   (próg {threshold} °C)",
        "alert_time": "Godzina: {time}",
        "alert_valve_hint": "ℹ️ Grzejniki nie są wołane, a dom się nagrzewa — to wygląda na przeciek zaworu.",
        "alert_details_failed": "(nie udało się dociągnąć szczegółów z Home Assistanta: {error})",
        "alert_back_below": "✅ Temperatura wróciła poniżej progu",
        "ha_down_header": "⚠️ Brak kontaktu z Home Assistantem",
        "ha_down_since": "Od: {time} ({duration})",
        "ha_down_error": "Błąd: {error}",
        "ha_down_note": "Nie mogę pilnować temperatury, dopóki Home Assistant nie odpowie.",
        "ha_restored": "✅ Kontakt z Home Assistantem przywrócony ({time}).",
        "sensor_stale_header": "⚠️ Czujnik Hive nie odświeża danych",
        "sensor_stale_unavailable": "Czujnik zgłasza brak danych (unavailable).",
        "sensor_stale_age": "Ostatnia zmiana wartości: {age} temu ({temperature} °C).",
        "sensor_stale_note": "Zwykle to blip chmury Hive. Jeśli utrzyma się dłużej, sprawdź integrację.",
        "sensor_ok": "✅ Czujnik Hive znów odświeża dane ({temperature} °C).",
        "startup": "🤖 Bot wystartował (wersja {version}).\nPróg alertu: {threshold} °C. Sprawdzam co {seconds} s.\nLista komend: /pomoc",
        "unknown_command": "Nie znam takiej komendy. Wpisz /pomoc, żeby zobaczyć listę.",
        "not_owner": "⛔ Ten bot obsługuje tylko właściciela instalacji.",
        "ha_error_reply": "⚠️ Home Assistant nie odpowiedział: {error}",
        "command_failed": "⚠️ Coś się wysypało przy tej komendzie. Szczegóły są w logach bota.",
    },
}


@dataclass(frozen=True)
class Locale:
    """Formatting rules and texts for one language."""

    code: str
    timezone: ZoneInfo

    # --- texts -----------------------------------------------------------------------------

    def t(self, key: str, **kwargs) -> str:
        """Translated text. Falls back to English, then to the key itself."""
        catalogue = MESSAGES.get(self.code) or MESSAGES[DEFAULT_LANGUAGE]
        template = catalogue.get(key) or MESSAGES[DEFAULT_LANGUAGE].get(key) or key
        try:
            return template.format(**kwargs) if kwargs else template
        except (KeyError, IndexError):
            # A broken placeholder must never take the bot down.
            return template

    # --- numbers, temperatures, durations --------------------------------------------------

    def number(self, value: float | None, places: int = 1) -> str:
        if value is None:
            return self.t("no_data")
        separator = DECIMAL_SEPARATOR.get(self.code, ".")
        return f"{value:.{places}f}".replace(".", separator)

    def temperature(self, value: float | None) -> str:
        """'22.6 °C' / '22,6 °C', or 'no data' without an orphaned degree sign."""
        return self.t("no_data") if value is None else f"{self.number(value)} °C"

    def duration(self, minutes: float) -> str:
        minutes = int(round(minutes))
        if minutes < 60:
            return f"{minutes} {self.t('unit_min')}"
        return f"{minutes // 60} {self.t('unit_hour')} {minutes % 60:02d} {self.t('unit_min')}"

    def count(self, number: int, noun: str) -> str:
        """Plural-aware count: '2 periods' / '2 okresach' / '5 okresach'."""
        forms = (PLURALS.get(self.code) or PLURALS[DEFAULT_LANGUAGE]).get(noun)
        if not forms:
            return f"{number} {noun}"
        if self.code == "pl":
            if number == 1:
                form = forms[0]
            elif 2 <= number % 10 <= 4 and not 12 <= number % 100 <= 14:
                form = forms[1]
            else:
                form = forms[2]
        else:
            form = forms[0] if number == 1 else forms[1]
        return f"{number} {form}"

    # --- dates and times -------------------------------------------------------------------

    def clock(self, moment: datetime | None) -> str:
        if moment is None:
            return self.t("no_data")
        return moment.astimezone(self.timezone).strftime("%H:%M")

    def short_date(self, day: date) -> str:
        names = WEEKDAYS.get(self.code) or WEEKDAYS[DEFAULT_LANGUAGE]
        return f"{names[day.weekday()]} {day.strftime('%d.%m')}"

    def day_month(self, day: date | datetime) -> str:
        return day.strftime("%d.%m")


def make_locale(language: str, timezone: str) -> Locale:
    """Build a Locale, falling back to English for an unknown language."""
    code = (language or DEFAULT_LANGUAGE).strip().lower()
    if code not in MESSAGES:
        code = DEFAULT_LANGUAGE
    return Locale(code=code, timezone=ZoneInfo(timezone))


def known_languages() -> tuple[str, ...]:
    return tuple(sorted(MESSAGES))
