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

import math
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
        "heat_demand_on": "Hive is calling for heat",
        "heat_demand_off": "Hive is not calling for heat (hvac_action: {action})",
        "water_control_on": "control on",
        "water_control_off": "control off",
        "water_control_schedule": "on Hive's own schedule (eco)",
        "state_unknown": "unknown ('{state}')",
        "none_today": "none",
        "no_windows": "  (no windows)",
        "truncated": "\n… (message shortened)",
        "mode_schedule": "schedule",
        "mode_manual": "manual",
        "mode_off": "off",
        "and_more": "and {count} more",
        # --- data coverage ---
        "coverage_partial": "⚠️ Data for {known} of {span} ({percent}%). The total covers the known time only.",
        "coverage_none": "⚠️ No data for these {span}: the state is unknown, not 'off'.",
        "coverage_short": " (data for {percent}% of the time)",
        "coverage_ha_down": "Home Assistant was not running: {ranges}. Old values are not counted for that time.",
        "coverage_unchecked": "ℹ️ Could not read the Home Assistant logbook, so outages of Home Assistant itself were not checked.",
        "run_gap": "+{gap} without data, not counted",
        "run_open_start": "no data just before",
        "run_open_end": "no data just after",
        # --- /help ---
        "help_header": "🏠 Hive heating bot",
        "help_intro": "I watch the house temperature and alert you above {threshold} °C.",
        "help_commands": "Commands:",
        "help_temperature": "/temperature — what the house looks like right now",
        "help_heating": "/heating — how long Hive called for heat today (/heating 7 = 7 days)",
        "help_boosts": "/boosts — when Boost was used (/boosts 7 = 7 days)",
        "help_water": "/water — hot water control and today's windows",
        "help_today": "/today — one-screen summary of the day",
        "help_status": "/status — bot health and the link to Home Assistant",
        "help_threshold": "/threshold 23 — show or change the alert threshold",
        "help_mute": "/mute 120 — silence alerts for 120 minutes",
        "help_unmute": "/unmute — turn alerts back on",
        "help_help": "/help — this list",
        "help_signals": "Times come from Hive control signals: the thermostat's call for heat and hot water control. They are not boiler firing time or gas use.",
        "help_aliases": "Polish command names work too (/temperatura, /ogrzewanie, /boosty, /woda, /dzis, /prog, /cicho, /glosno, /pomoc).",
        # --- /temperature ---
        "now_header": "🌡 Right now",
        "now_temperature": "Temperature: {temperature}   (alert threshold {threshold} °C)",
        "now_target": "Thermostat target: {target}",
        "now_heating": "Heating: {state}",
        "now_mode": "Heating mode: {mode}",
        "now_water": "Hot water: {state}",
        "now_reading": "Last change of the reading: {time}",
        "now_reading_age": " ({age} ago)",
        "now_reading_none": "Last change of the reading: unknown",
        # --- /heating ---
        "heating_today_header": "🔥 Heating — today ({day})",
        "heating_range_header": "🔥 Heating — last {days} days",
        "heating_total": "Call for heat: {duration} in {periods}",
        "heating_total_zero": "Call for heat: 0 min",
        "heating_line": "  • {date}{start} – {end}  ({duration}{note})",
        "heating_last": "Last call for heat: {date}{start} – {end} ({duration}{note})",
        "heating_last_after": " Since then, data for {percent}% of the time only.",
        "heating_last_none": "No known call for heat in the last {days} days (data for {percent}% of that time).",
        "heating_day_line": "{date}: {duration}{coverage}",
        "heating_day_none": "{date}: no data",
        "heating_sum": "Total: {duration}",
        "heating_signal_note": "ℹ️ Counted from Hive's call for heat (climate hvac_action). That is a control signal: not boiler firing time, gas use or radiator temperature.",
        # --- /boosts ---
        "boosts_today_header": "⚡ Boosts — today ({day})",
        "boosts_range_header": "⚡ Boosts — last {days} days",
        "boosts_heating_label": "Heating",
        "boosts_water_label": "Hot water",
        "boosts_none": "{label}: none{coverage}",
        "boosts_no_data": "{label}: no data",
        "boosts_count": "{label} — {count}{coverage}:",
        "boosts_line": "  • {date}{start} – {end} ({duration}{note}){at}",
        "boosts_at_temperature": ", at {temperature} °C",
        "boosts_range_note": "Temperature at the moment of the Boost is shown for ranges up to 7 days.",
        # --- /water ---
        "water_header": "🚿 Hot water — today ({day})",
        "water_now": "Now: {state}",
        "water_total": "Control on for: {duration}",
        "water_line": "  • {date}{start} – {end}{note}",
        "water_schedule": "\nSchedule: {schedule}",
        "water_note": "ℹ️ 'Control on' means Hive lets the cylinder heat whenever its own thermostat asks. It is not continuous boiler firing.",
        # --- /today ---
        "today_header": "📋 Day summary — {day}",
        "today_temperature": "Temperature: now {temperature}",
        "today_minmax": "  min {min} °C ({min_time}), max {max} °C ({max_time}), average {avg} °C",
        "today_no_history": "  (no temperature history for today)",
        "today_heating": "Heating, call for heat: {duration} in {periods}{coverage}",
        "today_heating_none": "Heating, call for heat: no data",
        "today_boost_heating": "Heating Boosts: {count}",
        "today_boost_water": "Hot water Boosts: {count}",
        "today_water": "Hot water, control on: {duration}",
        "today_alerts": "Temperature alerts today: {count}",
        "today_signal_note": "ℹ️ Heating and hot water times are Hive control signals, not boiler firing time.",
        # --- /status ---
        "status_header_ok": "🩺 Bot status",
        "status_header_warn": "⚠️ Bot status — attention",
        "status_version": "Version: {version}",
        "status_uptime": "Running since: {since} ({uptime})",
        "status_ha": "Home Assistant: {state}",
        "status_ha_ok": "OK",
        "status_ha_down": "NO CONNECTION ({error})",
        "status_last_reading": "Last temperature reading: {temperature}",
        "status_sensor_age": "Reading last changed: {age} ago (Hive only records changes)",
        "status_sensor_age_none": "Reading last changed: no data",
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
        "alert_recent": "Last {hours} h: {first} → {last} °C; call for heat {heating}; hot water control on {water}.",
        "alert_recent_note": "ℹ️ These are the recorded facts, not a diagnosis: a rise in temperature has many possible causes.",
        "alert_details_failed": "(could not fetch details from Home Assistant: {error})",
        "alert_back_below": "✅ Temperature is back below the threshold",
        # --- health of the link ---
        "ha_down_header": "⚠️ No contact with Home Assistant",
        "ha_down_since": "Since: {time} ({duration})",
        "ha_down_error": "Error: {error}",
        "ha_down_note": "I cannot watch the temperature until Home Assistant answers.",
        "ha_restored": "✅ Contact with Home Assistant restored ({time}).",
        "sensor_unavailable_header": "⚠️ The Hive sensor reports no data",
        "sensor_unchanged_header": "ℹ️ The temperature reading has not changed",
        "sensor_stale_unavailable": "The sensor says '{state}'.",
        "sensor_stale_age": "Unchanged for {age}, still reading {temperature} °C.",
        "sensor_stale_note": "Usually a Hive cloud blip. If it lasts, check the integration.",
        "sensor_unchanged_note": "This alone is not a fault: Hive only records a new value when it changes, so a steady house looks like this. If it stays frozen much longer, take a look at the integration.",
        "sensor_ok": "✅ The temperature reading is back to normal ({temperature} °C).",
        "ha_stalled_header": "⚠️ Home Assistant answers, but it has stopped working",
        "ha_stalled_body": "Its heartbeat {entity} last ticked {age} ago ({time}); it should tick every {every} minutes. Automations — the hot-water schedule too — are most likely not running.",
        "ha_stalled_note": "If it does not recover on its own, restart Home Assistant.",
        "ha_stalled_restored": "✅ Home Assistant is ticking again ({time}).",
        "recorder_stalled_header": "⚠️ Home Assistant is not saving history",
        "recorder_stalled_body": "Nothing new has reached its database since {time} ({age}), although Home Assistant itself is running.",
        "recorder_stalled_note": "/heating, /today and /boosts will show this time as missing data. A restart of Home Assistant usually helps; check its log for 'recorder'.",
        "recorder_restored": "✅ Home Assistant is saving history again ({time}).",
        "downtime_header": "⚠️ The bot was not running",
        "downtime_body": "No sign of life {range} ({duration}). Nobody was watching the house in that time.",
        "downtime_note": "If the whole machine was off, Home Assistant was not running the schedules either — /today shows what is known.",
        "status_pulse_ok": "Home Assistant heartbeat: last tick {age} ago",
        "status_pulse_stalled": "Home Assistant heartbeat: STOPPED ({age} ago)",
        "status_pulse_missing": "Home Assistant heartbeat: {entity} does not exist (see the README)",
        "status_pulse_unknown": "Home Assistant heartbeat: not checked yet",
        "status_pulse_off": "Home Assistant heartbeat: not watched",
        "status_recorder_bad": "History recording: NOTHING NEW in the database",
        "status_ping_ok": "External ping: last sent {time}",
        "status_ping_failing": "External ping: FAILING (see the bot log)",
        "status_ping_held": "External ping: held back until everything is fine",
        "status_ping_off": "External ping: off (BOT_PING_URL)",
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
        "heat_demand_on": "Hive żąda ciepła",
        "heat_demand_off": "Hive nie żąda ciepła (hvac_action: {action})",
        "water_control_on": "sterowanie włączone",
        "water_control_off": "sterowanie wyłączone",
        "water_control_schedule": "wg własnego harmonogramu Hive (eco)",
        "state_unknown": "nieznany („{state}”)",
        "none_today": "brak",
        "no_windows": "  (brak okien)",
        "truncated": "\n… (wiadomość skrócona)",
        "mode_schedule": "harmonogram",
        "mode_manual": "ręczny",
        "mode_off": "wyłączony",
        "and_more": "i jeszcze {count}",
        "coverage_partial": "⚠️ Dane za {known} z {span} ({percent}%). Suma obejmuje tylko znany czas.",
        "coverage_none": "⚠️ Brak danych za te {span}: stan nieznany, a nie „wyłączone”.",
        "coverage_short": " (dane za {percent}% czasu)",
        "coverage_ha_down": "Home Assistant nie działał: {ranges}. Starych wartości z tego czasu nie liczę.",
        "coverage_unchecked": "ℹ️ Nie udało się odczytać dziennika Home Assistanta, więc przerw w pracy samego HA nie sprawdzono.",
        "run_gap": "+{gap} bez danych, nie liczone",
        "run_open_start": "brak danych tuż przed",
        "run_open_end": "brak danych tuż po",
        "help_header": "🏠 Bot ogrzewania Hive",
        "help_intro": "Pilnuję temperatury w domu i alarmuję powyżej {threshold} °C.",
        "help_commands": "Komendy:",
        "help_temperature": "/temperatura — co jest teraz w domu",
        "help_heating": "/ogrzewanie — ile dziś Hive żądał ciepła dla CO (/ogrzewanie 7 = 7 dni)",
        "help_boosts": "/boosty — kiedy włączano boosty (/boosty 7 = 7 dni)",
        "help_water": "/woda — sterowanie ciepłą wodą (CWU) i dzisiejsze okna",
        "help_today": "/dzis — skrót całego dnia",
        "help_status": "/status — health check bota i łączności z Home Assistantem",
        "help_threshold": "/prog 23 — pokaż lub ustaw próg alertu",
        "help_mute": "/cicho 120 — wycisz alerty na 120 minut",
        "help_unmute": "/glosno — włącz alerty z powrotem",
        "help_help": "/pomoc — ta lista",
        "help_signals": "Czasy pochodzą z sygnałów sterowania Hive: żądania ciepła z termostatu i sterowania CWU. To nie czas palenia kotła ani zużycie gazu.",
        "help_aliases": "Angielskie nazwy komend też działają (/temperature, /heating, /boosts, /water, /today, /threshold, /mute, /unmute, /help).",
        "now_header": "🌡 Teraz w domu",
        "now_temperature": "Temperatura: {temperature}   (próg alertu {threshold} °C)",
        "now_target": "Cel termostatu: {target}",
        "now_heating": "Ogrzewanie (CO): {state}",
        "now_mode": "Tryb ogrzewania: {mode}",
        "now_water": "Ciepła woda (CWU): {state}",
        "now_reading": "Ostatnia zmiana odczytu: {time}",
        "now_reading_age": " ({age} temu)",
        "now_reading_none": "Ostatnia zmiana odczytu: nieznana",
        "heating_today_header": "🔥 Ogrzewanie (CO) — dziś ({day})",
        "heating_range_header": "🔥 Ogrzewanie (CO) — ostatnie {days} dni",
        "heating_total": "Żądanie ciepła: {duration} w {periods}",
        "heating_total_zero": "Żądanie ciepła: 0 min",
        "heating_line": "  • {date}{start} – {end}  ({duration}{note})",
        "heating_last": "Ostatnie żądanie ciepła: {date}{start} – {end} ({duration}{note})",
        "heating_last_after": " Od tamtej pory dane tylko za {percent}% czasu.",
        "heating_last_none": "Brak znanego żądania ciepła w ostatnich {days} dniach (dane za {percent}% tego czasu).",
        "heating_day_line": "{date}: {duration}{coverage}",
        "heating_day_none": "{date}: brak danych",
        "heating_sum": "Razem: {duration}",
        "heating_signal_note": "ℹ️ Liczone z żądania ciepła Hive (climate hvac_action). To sygnał sterowania: nie czas palenia kotła, zużycie gazu ani temperatura grzejników.",
        "boosts_today_header": "⚡ Boosty — dziś ({day})",
        "boosts_range_header": "⚡ Boosty — ostatnie {days} dni",
        "boosts_heating_label": "Ogrzewanie",
        "boosts_water_label": "Ciepła woda",
        "boosts_none": "{label}: brak{coverage}",
        "boosts_no_data": "{label}: brak danych",
        "boosts_count": "{label} — {count}{coverage}:",
        "boosts_line": "  • {date}{start} – {end} ({duration}{note}){at}",
        "boosts_at_temperature": ", przy {temperature} °C",
        "boosts_range_note": "Temperaturę w chwili boostu pokazuję dla zakresów do 7 dni.",
        "water_header": "🚿 Ciepła woda (CWU) — dziś ({day})",
        "water_now": "Teraz: {state}",
        "water_total": "Sterowanie włączone: {duration}",
        "water_line": "  • {date}{start} – {end}{note}",
        "water_schedule": "\nHarmonogram: {schedule}",
        "water_note": "ℹ️ „Sterowanie włączone” znaczy, że Hive pozwala grzać zasobnik, gdy jego termostat tego potrzebuje. To nie ciągła praca palnika.",
        "today_header": "📋 Podsumowanie dnia — {day}",
        "today_temperature": "Temperatura: teraz {temperature}",
        "today_minmax": "  min {min} °C ({min_time}), max {max} °C ({max_time}), średnio {avg} °C",
        "today_no_history": "  (brak historii temperatury z dziś)",
        "today_heating": "Ogrzewanie (CO), żądanie ciepła: {duration} w {periods}{coverage}",
        "today_heating_none": "Ogrzewanie (CO), żądanie ciepła: brak danych",
        "today_boost_heating": "Boost ogrzewania: {count}",
        "today_boost_water": "Boost ciepłej wody: {count}",
        "today_water": "Ciepła woda (CWU), sterowanie włączone: {duration}",
        "today_alerts": "Alertów temperatury dziś: {count}",
        "today_signal_note": "ℹ️ Czasy CO i CWU to sygnały sterowania Hive, nie czas palenia kotła.",
        "status_header_ok": "🩺 Status bota",
        "status_header_warn": "⚠️ Status bota — uwaga",
        "status_version": "Wersja: {version}",
        "status_uptime": "Działa od: {since} ({uptime})",
        "status_ha": "Home Assistant: {state}",
        "status_ha_ok": "OK",
        "status_ha_down": "BRAK ŁĄCZNOŚCI ({error})",
        "status_last_reading": "Ostatni odczyt temperatury: {temperature}",
        "status_sensor_age": "Ostatnia zmiana odczytu: {age} temu (Hive zapisuje tylko zmiany)",
        "status_sensor_age_none": "Ostatnia zmiana odczytu: brak danych",
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
        "alert_recent": "Ostatnie {hours} h: {first} → {last} °C; żądanie ciepła CO {heating}; sterowanie CWU włączone {water}.",
        "alert_recent_note": "ℹ️ To zapisane fakty, nie diagnoza: wzrost temperatury może mieć wiele przyczyn.",
        "alert_details_failed": "(nie udało się dociągnąć szczegółów z Home Assistanta: {error})",
        "alert_back_below": "✅ Temperatura wróciła poniżej progu",
        "ha_down_header": "⚠️ Brak kontaktu z Home Assistantem",
        "ha_down_since": "Od: {time} ({duration})",
        "ha_down_error": "Błąd: {error}",
        "ha_down_note": "Nie mogę pilnować temperatury, dopóki Home Assistant nie odpowie.",
        "ha_restored": "✅ Kontakt z Home Assistantem przywrócony ({time}).",
        "sensor_unavailable_header": "⚠️ Czujnik Hive nie podaje danych",
        "sensor_unchanged_header": "ℹ️ Odczyt temperatury się nie zmienia",
        "sensor_stale_unavailable": "Czujnik zgłasza „{state}”.",
        "sensor_stale_age": "Bez zmiany od {age}, nadal {temperature} °C.",
        "sensor_stale_note": "Zwykle to blip chmury Hive. Jeśli utrzyma się dłużej, sprawdź integrację.",
        "sensor_unchanged_note": "Samo w sobie to nie awaria: Hive zapisuje nową wartość tylko wtedy, gdy się zmieni, więc spokojny dom wygląda tak samo. Jeśli odczyt stoi dużo dłużej, warto zajrzeć w integrację.",
        "sensor_ok": "✅ Odczyt temperatury znów wygląda normalnie ({temperature} °C).",
        "ha_stalled_header": "⚠️ Home Assistant odpowiada, ale przestał działać",
        "ha_stalled_body": "Jego puls {entity} ostatnio drgnął {age} temu ({time}); powinien co {every} minut. Automatyzacje — także harmonogram ciepłej wody — najpewniej nie działają.",
        "ha_stalled_note": "Jeśli to samo nie minie, zrestartuj Home Assistanta.",
        "ha_stalled_restored": "✅ Puls Home Assistanta wrócił ({time}).",
        "recorder_stalled_header": "⚠️ Home Assistant nie zapisuje historii",
        "recorder_stalled_body": "Do jego bazy nic nowego nie trafiło od {time} ({age}), choć sam Home Assistant działa.",
        "recorder_stalled_note": "/ogrzewanie, /dzis i /boosty pokażą ten czas jako brak danych. Zwykle pomaga restart Home Assistanta; w jego logu szukaj „recorder”.",
        "recorder_restored": "✅ Home Assistant znów zapisuje historię ({time}).",
        "downtime_header": "⚠️ Bot nie działał",
        "downtime_body": "Brak śladu życia {range} ({duration}). Nikt w tym czasie nie pilnował domu.",
        "downtime_note": "Jeśli wyłączony był cały serwer, Home Assistant też nie prowadził harmonogramów — /dzis pokaże, co wiadomo.",
        "status_pulse_ok": "Puls Home Assistanta: ostatnio {age} temu",
        "status_pulse_stalled": "Puls Home Assistanta: STOI ({age} temu)",
        "status_pulse_missing": "Puls Home Assistanta: brak encji {entity} (zob. README)",
        "status_pulse_unknown": "Puls Home Assistanta: jeszcze nie sprawdzony",
        "status_pulse_off": "Puls Home Assistanta: nie pilnowany",
        "status_recorder_bad": "Zapis historii: NIC NOWEGO w bazie",
        "status_ping_ok": "Ping zewnętrzny: ostatnio {time}",
        "status_ping_failing": "Ping zewnętrzny: NIE DOCHODZI (zob. log bota)",
        "status_ping_held": "Ping zewnętrzny: wstrzymany, dopóki coś jest nie tak",
        "status_ping_off": "Ping zewnętrzny: wyłączony (BOT_PING_URL)",
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
        if isinstance(day, datetime):
            day = day.astimezone(self.timezone)
        return day.strftime("%d.%m")

    def moment(self, moment: datetime | None, reference: datetime) -> str:
        """'13:00' on the reference day, '22.10 13:00' on any other."""
        if moment is None or self.day_month(moment) == self.day_month(reference):
            return self.clock(moment)
        return f"{self.day_month(moment)} {self.clock(moment)}"

    def time_range(self, start: datetime, end: datetime) -> str:
        """'08.10 17:00 – 22.10 08:55', or '22.10 08:15–08:18' within one day."""
        if self.day_month(start) == self.day_month(end):
            return f"{self.day_month(start)} {self.clock(start)}–{self.clock(end)}"
        return f"{self.day_month(start)} {self.clock(start)} – {self.day_month(end)} {self.clock(end)}"

    def percent(self, part: float, whole: float) -> str:
        """Share in whole percent, never rounded up to a misleading '100' or down to '0'."""
        share = 100.0 * part / whole if whole > 0 else 0.0
        if 0 < share < 1:
            return "<1"
        if share >= 100 - 1e-6:          # equal, give or take floating-point sums
            return "100"
        return str(min(math.floor(share), 99))


def make_locale(language: str, timezone: str) -> Locale:
    """Build a Locale, falling back to English for an unknown language."""
    code = (language or DEFAULT_LANGUAGE).strip().lower()
    if code not in MESSAGES:
        code = DEFAULT_LANGUAGE
    return Locale(code=code, timezone=ZoneInfo(timezone))


def known_languages() -> tuple[str, ...]:
    return tuple(sorted(MESSAGES))
