#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Command and alert tests on fakes: no network, no Home Assistant, no Telegram.

Run from the telegram-bot directory:
    python3 -m unittest discover -s tests -t .
"""

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hive_bot as bot  # noqa: E402
import messages as msg  # noqa: E402

LONDON = ZoneInfo("Europe/London")
NOW = datetime(2026, 9, 19, 18, 0, tzinfo=LONDON)
OWNER = "1000"
STRANGER = "2000"


def stamp(hour, minute, day=19):
    return datetime(2026, 9, day, hour, minute, tzinfo=LONDON).isoformat()


def row(hour, minute, state, day=19):
    return {"state": state, "last_changed": stamp(hour, minute, day)}


class FakeHA:
    """Stands in for the Home Assistant REST client."""

    def __init__(self, states=None, history=None, error=None):
        self.values = states or {}
        self.series = history or {}
        self.error = error
        self.history_calls = []

    def _raise_if_needed(self):
        if self.error:
            raise bot.HAError(self.error)

    def alive(self):
        self._raise_if_needed()
        return True

    def state(self, entity):
        self._raise_if_needed()
        value = self.values.get(entity, "unknown")
        if isinstance(value, dict):
            return value
        return {"state": value, "last_changed": stamp(17, 55), "attributes": {}}

    def states(self, entities):
        return {entity: self.state(entity) for entity in entities}

    def history(self, entities, since, until):
        self._raise_if_needed()
        self.history_calls.append((list(entities), since, until))
        return {entity: list(self.series.get(entity, [])) for entity in entities}


class FakeTelegram:
    """Captures what the bot would send."""

    def __init__(self, locale):
        self.locale = locale
        self.sent: list[tuple[str, str]] = []

    CHARACTER_LIMIT = bot.Telegram.CHARACTER_LIMIT

    def send(self, chat_id, text):
        if not chat_id:
            return
        if len(text) > self.CHARACTER_LIMIT:
            text = text[: self.CHARACTER_LIMIT] + self.locale.t("truncated")
        self.sent.append((str(chat_id), text))

    def who_am_i(self):
        return {"username": "test_bot", "id": 42}

    def updates(self, offset, timeout_sec=25):
        return []

    @property
    def last(self):
        return self.sent[-1][1] if self.sent else ""


def make_bot(language="en", states=None, history=None, error=None, chat_id=OWNER, now=NOW,
             **settings):
    directory = Path(tempfile.mkdtemp())
    config = bot.Config(
        telegram_token="1:AA", ha_url="http://ha.local:8123", ha_token="token",
        chat_id=chat_id, language=language, timezone="Europe/London", **settings,
    )
    instance = bot.Bot(config, state_file=directory / "state.json")
    instance.ha = FakeHA(states=states, history=history, error=error)
    instance.tg = FakeTelegram(instance.locale)
    instance.clock = now
    instance.now = lambda: instance.clock          # a controllable clock for the tests
    return instance


def entities(instance, **overrides):
    """Handy map of entity id -> value for FakeHA, with defaults that look realistic."""
    names = instance.entity
    values = {
        names["temperature"]: "22.6",
        names["target"]: "21.0",
        names["mode"]: "schedule",
        names["climate"]: {"state": "heat", "last_changed": stamp(17, 55),
                           "attributes": {"hvac_action": "idle"}},
        names["heating_state"]: "off",
        names["boost_heating"]: "off",
        names["boost_water"]: "on",
        names["water_heater"]: "on",
    }
    for key, value in overrides.items():
        values[names[key]] = value
    return values


class TestCommands(unittest.TestCase):
    def test_help_is_in_the_configured_language(self):
        english = make_bot("en")
        english.cmd_help()
        self.assertIn("/temperature", english.tg.last)
        self.assertIn("Commands:", english.tg.last)

        polish = make_bot("pl")
        polish.cmd_help()
        self.assertIn("/temperatura", polish.tg.last)
        self.assertIn("Komendy:", polish.tg.last)

    def test_temperature_now(self):
        instance = make_bot("en")
        instance.ha = FakeHA(states=entities(instance))
        instance.cmd_temperature()
        message = instance.tg.last
        self.assertIn("22.6 °C", message)
        self.assertIn("Thermostat target: 21.0 °C", message)
        self.assertIn("not heating", message)
        self.assertIn("Hive action: idle", message)
        self.assertIn("Hot water: on", message)

    def test_temperature_uses_the_polish_decimal_comma(self):
        instance = make_bot("pl")
        instance.ha = FakeHA(states=entities(instance))
        instance.cmd_temperature()
        self.assertIn("22,6 °C", instance.tg.last)
        self.assertIn("Ciepła woda: włączona", instance.tg.last)

    def test_temperature_without_a_reading_says_no_data(self):
        instance = make_bot("en")
        instance.ha = FakeHA(states=entities(instance, temperature="unavailable"))
        instance.cmd_temperature()
        self.assertIn("no data", instance.tg.last)
        self.assertNotIn("no data °C", instance.tg.last)

    def test_heating_today_lists_the_periods(self):
        instance = make_bot("en")
        heating = instance.entity["heating_state"]
        instance.ha = FakeHA(states=entities(instance),
                             history={heating: [row(8, 0, "on"), row(8, 25, "off"),
                                                row(14, 0, "on"), row(14, 10, "off")]})
        instance.cmd_heating()
        message = instance.tg.last
        self.assertIn("35 min", message)
        self.assertIn("2 periods", message)
        self.assertIn("08:00 – 08:25", message)

    def test_heating_today_with_nothing_shows_the_last_run(self):
        instance = make_bot("en")
        heating = instance.entity["heating_state"]
        instance.ha = FakeHA(states=entities(instance),
                             history={heating: [row(11, 0, "on", day=17),
                                                row(11, 20, "off", day=17)]})
        instance.cmd_heating()
        self.assertIn("0 min", instance.tg.last)
        self.assertIn("17.09", instance.tg.last)

    def test_heating_range_shows_one_line_per_day(self):
        instance = make_bot("en")
        heating = instance.entity["heating_state"]
        instance.ha = FakeHA(states=entities(instance),
                             history={heating: [row(9, 0, "on", day=17), row(9, 30, "off", day=17),
                                                row(9, 0, "on"), row(9, 15, "off")]})
        instance.cmd_heating("3")
        message = instance.tg.last
        self.assertIn("last 3 days", message)
        self.assertIn("17.09: 30 min", message)
        self.assertIn("19.09: 15 min", message)
        self.assertIn("45 min", message)

    def test_heating_range_is_capped(self):
        instance = make_bot("en")
        instance.ha = FakeHA(states=entities(instance))
        instance.cmd_heating("999")
        self.assertIn(f"last {bot.MAX_DAYS} days", instance.tg.last)

    def test_boosts_today_with_the_temperature_at_the_time(self):
        instance = make_bot("en")
        instance.ha = FakeHA(states=entities(instance), history={
            instance.entity["boost_heating"]: [row(11, 25, "on"), row(11, 28, "off")],
            instance.entity["boost_water"]: [row(11, 25, "on"), row(11, 39, "off")],
            instance.entity["temperature"]: [row(11, 0, "22.6")],
        })
        instance.cmd_boosts()
        message = instance.tg.last
        self.assertIn("Heating — 1 boost", message)
        self.assertIn("at 22.6 °C", message)
        self.assertIn("Hot water — 1 boost", message)

    def test_boosts_over_a_long_range_skips_temperature_history(self):
        instance = make_bot("en")
        instance.ha = FakeHA(states=entities(instance), history={
            instance.entity["boost_heating"]: [row(11, 25, "on"), row(11, 28, "off")],
        })
        instance.cmd_boosts("30")
        asked = instance.ha.history_calls[0][0]
        self.assertNotIn(instance.entity["temperature"], asked,
                         "a month of temperature history is pointless and slow")
        self.assertIn("up to 7 days", instance.tg.last)

    def test_boosts_without_any(self):
        instance = make_bot("pl")
        instance.ha = FakeHA(states=entities(instance))
        instance.cmd_boosts()
        self.assertIn("Ogrzewanie: brak", instance.tg.last)
        self.assertIn("Ciepła woda: brak", instance.tg.last)

    def test_water_shows_windows_and_optional_schedule(self):
        instance = make_bot("en", hw_schedule_text="04:30-23:00, Sundays also 02:00-04:00")
        water = instance.entity["water_heater"]
        instance.ha = FakeHA(states=entities(instance),
                             history={water: [row(4, 30, "on"), row(17, 0, "off")]})
        instance.cmd_water()
        message = instance.tg.last
        self.assertIn("04:30 – 17:00", message)
        self.assertIn("Sundays also", message)

    def test_water_without_a_schedule_text_stays_quiet_about_it(self):
        instance = make_bot("en")
        instance.ha = FakeHA(states=entities(instance))
        instance.cmd_water()
        self.assertNotIn("Schedule", instance.tg.last)

    def test_today_summary(self):
        instance = make_bot("en")
        instance.ha = FakeHA(states=entities(instance), history={
            instance.entity["temperature"]: [row(0, 0, "21.0"), row(12, 0, "23.0")],
            instance.entity["heating_state"]: [row(8, 0, "on"), row(8, 20, "off")],
            instance.entity["boost_heating"]: [row(11, 0, "on"), row(11, 3, "off")],
            instance.entity["boost_water"]: [],
            instance.entity["water_heater"]: [row(4, 30, "on")],
        })
        instance.cmd_today()
        message = instance.tg.last
        self.assertIn("min 21.0 °C", message)
        self.assertIn("max 23.0 °C", message)
        self.assertIn("Heating Boosts: 1", message)
        self.assertIn("Radiators: 20 min", message)

    def test_status_when_home_assistant_answers(self):
        instance = make_bot("en")
        instance.ha = FakeHA(states=entities(instance))
        instance.cmd_status()
        message = instance.tg.last
        self.assertIn("Home Assistant: OK", message)
        self.assertIn("22.6 °C", message, "a fresh start must still show a reading")
        self.assertIn("Language: en", message)

    def test_status_uptime_follows_the_bot_clock(self):
        instance = make_bot("en")
        instance.ha = FakeHA(states=entities(instance))
        instance.health.started = instance.clock - timedelta(hours=53, minutes=12)
        instance.cmd_status()
        self.assertIn("53 h 12 min", instance.tg.last)

    def test_status_when_home_assistant_is_down(self):
        instance = make_bot("en", error="timed out")
        instance.cmd_status()
        self.assertIn("NO CONNECTION", instance.tg.last)
        self.assertIn("attention", instance.tg.last)

    def test_threshold_show_and_set(self):
        instance = make_bot("en")
        instance.cmd_threshold()
        self.assertIn("23.0 °C", instance.tg.last)
        instance.cmd_threshold("21.5")
        self.assertIn("21.5 °C", instance.tg.last)
        self.assertEqual(instance.threshold, 21.5)

    def test_threshold_accepts_a_comma_in_polish(self):
        instance = make_bot("pl")
        instance.cmd_threshold("22,5")
        self.assertEqual(instance.threshold, 22.5)
        self.assertIn("22,5 °C", instance.tg.last)

    def test_threshold_rejects_nonsense(self):
        instance = make_bot("en")
        for argument in ("abc", "99", "1"):
            instance.cmd_threshold(argument)
            self.assertIn("range", instance.tg.last)
        self.assertEqual(instance.threshold, 23.0)

    def test_mute_and_unmute(self):
        instance = make_bot("en")
        instance.cmd_mute("120")
        self.assertIn("2 h 00 min", instance.tg.last)
        self.assertTrue(instance.muted)
        instance.cmd_unmute()
        self.assertFalse(instance.muted)

    def test_mute_without_an_argument_defaults_to_an_hour(self):
        instance = make_bot("en")
        instance.cmd_mute()
        self.assertTrue(instance.muted)
        self.assertIn("1 h 00 min", instance.tg.last)

    def test_mute_is_bounded(self):
        instance = make_bot("en")
        for argument in ("9999999", "0", "abc"):
            instance.cmd_mute(argument)
            self.assertIn("range", instance.tg.last)
            self.assertFalse(instance.muted, f"/mute {argument} must not silence the bot")

    def test_unknown_command(self):
        instance = make_bot("en")
        instance.handle_command(OWNER, "/nonsense")
        self.assertIn("do not know", instance.tg.last)

    def test_a_stranger_is_refused_and_the_owner_is_not(self):
        instance = make_bot("en")
        instance.ha = FakeHA(states=entities(instance))
        instance.handle_command(STRANGER, "/status")
        self.assertEqual(instance.tg.sent[-1][0], STRANGER)
        self.assertIn("only answers its owner", instance.tg.last)
        instance.handle_command(OWNER, "/status")
        self.assertEqual(instance.tg.sent[-1][0], OWNER)

    def test_first_start_claims_an_unbound_bot(self):
        instance = make_bot("en", chat_id=None)
        self.assertIsNone(instance.state.chat_id)
        instance.handle_command(STRANGER, "/help")
        self.assertEqual(instance.state.chat_id, STRANGER,
                         "documented behaviour: with no pinned chat id the first chat wins")

    def test_a_pinned_chat_id_cannot_be_taken_over(self):
        instance = make_bot("en", chat_id=OWNER)
        instance.handle_command(STRANGER, "/help")
        self.assertEqual(instance.state.chat_id, OWNER)

    def test_a_broken_command_does_not_kill_the_bot(self):
        instance = make_bot("en", error="connection refused")
        instance.handle_command(OWNER, "/today")
        self.assertIn("did not answer", instance.tg.last)

    def test_long_messages_are_truncated(self):
        instance = make_bot("en")
        instance.send("x" * 5000)
        self.assertLessEqual(len(instance.tg.last), bot.Telegram.CHARACTER_LIMIT + 40)
        self.assertIn("shortened", instance.tg.last)


class TestAlerts(unittest.TestCase):
    def setUp(self):
        self.instance = make_bot("en", repeat_min=180, hysteresis_c=0.3)
        self.instance.ha = FakeHA(states=entities(self.instance))

    def set_temperature(self, value, age_minutes=1):
        names = self.instance.entity
        reading = (self.instance.clock - timedelta(minutes=age_minutes)).isoformat()
        self.instance.ha.values[names["temperature"]] = {
            "state": str(value), "last_changed": reading, "attributes": {}}

    def test_crossing_the_threshold_alerts_once(self):
        self.set_temperature(23.4)
        self.instance.check_temperature()
        self.assertIn("threshold exceeded", self.instance.tg.last)
        before = len(self.instance.tg.sent)
        self.instance.check_temperature()
        self.assertEqual(len(self.instance.tg.sent), before, "no repeat before the repeat time")

    def test_a_reminder_after_the_repeat_time(self):
        self.set_temperature(23.4)
        self.instance.check_temperature()
        self.instance.clock += timedelta(minutes=181)
        self.set_temperature(23.5)
        self.instance.check_temperature()
        self.assertIn("Still too warm", self.instance.tg.last)

    def test_hysteresis_keeps_the_alarm_armed_just_below_the_threshold(self):
        self.set_temperature(23.4)
        self.instance.check_temperature()
        self.set_temperature(22.9)      # within the 0.3 band
        self.instance.check_temperature()
        self.assertTrue(self.instance.state.alarm_active)
        self.set_temperature(22.6)      # clearly below
        self.instance.check_temperature()
        self.assertFalse(self.instance.state.alarm_active)
        self.assertIn("back below", self.instance.tg.last)

    def test_muting_does_not_latch_the_alarm(self):
        self.instance.cmd_mute("60")
        self.set_temperature(23.4)
        self.instance.check_temperature()
        self.assertNotIn("threshold exceeded", self.instance.tg.last)
        self.assertFalse(self.instance.state.alarm_active,
                         "a muted alert must fire once the silence ends")
        self.instance.clock += timedelta(minutes=61)
        self.instance.check_temperature()
        self.assertIn("threshold exceeded", self.instance.tg.last)

    def test_the_valve_hint_only_when_water_heats_and_radiators_do_not(self):
        names = self.instance.entity
        self.instance.ha.values[names["heating_state"]] = "off"
        self.instance.ha.values[names["water_heater"]] = "on"
        self.set_temperature(23.4)
        self.instance.check_temperature()
        self.assertIn("leaking valve", self.instance.tg.last)

    def test_no_valve_hint_while_the_radiators_run(self):
        names = self.instance.entity
        self.instance.ha.values[names["heating_state"]] = "on"
        self.set_temperature(23.4)
        self.instance.check_temperature()
        self.assertNotIn("leaking valve", self.instance.tg.last)

    def test_home_assistant_down_then_restored(self):
        instance = make_bot("en", ha_down_min=10)
        instance.ha = FakeHA(error="timed out")
        instance.check_temperature()
        self.assertEqual(instance.tg.sent, [], "one failed poll must not raise an alarm")
        instance.clock += timedelta(minutes=11)
        instance.check_temperature()
        self.assertIn("No contact with Home Assistant", instance.tg.last)
        instance.ha = FakeHA(states=entities(instance))
        instance.check_temperature()
        self.assertIn("restored", instance.tg.last)

    def test_a_stale_sensor_is_reported_once_and_then_cleared(self):
        instance = make_bot("en", stale_sensor_min=45)
        instance.ha = FakeHA(states=entities(instance))
        names = instance.entity
        instance.ha.values[names["temperature"]] = {
            "state": "22.6",
            "last_changed": (instance.clock - timedelta(minutes=90)).isoformat(),
            "attributes": {}}
        instance.check_temperature()
        self.assertIn("stopped refreshing", instance.tg.last)
        count = len(instance.tg.sent)
        instance.check_temperature()
        self.assertEqual(len(instance.tg.sent), count, "do not repeat the same warning")
        instance.ha.values[names["temperature"]] = {
            "state": "22.4", "last_changed": instance.clock.isoformat(), "attributes": {}}
        instance.check_temperature()
        self.assertIn("refreshing again", instance.tg.last)

    def test_an_unavailable_sensor_is_reported(self):
        instance = make_bot("en")
        instance.ha = FakeHA(states=entities(instance, temperature="unavailable"))
        instance.check_temperature()
        self.assertIn("unavailable", instance.tg.last)

    def test_the_daily_alert_counter_resets_with_the_date(self):
        self.set_temperature(23.4)
        self.instance.check_temperature()
        self.assertEqual(self.instance.state.alerts_today, 1)
        self.instance.clock += timedelta(days=1)
        self.set_temperature(22.0)
        self.instance.check_temperature()
        self.assertEqual(self.instance.state.alerts_today, 0)


class TestBothLanguages(unittest.TestCase):
    """Every command must produce a sensible message in every language."""

    def test_all_commands_answer_in_every_language(self):
        for language in msg.known_languages():
            for command in ("/help", "/temperature", "/heating", "/boosts", "/water", "/today",
                            "/status", "/threshold", "/mute", "/unmute"):
                instance = make_bot(language)
                instance.ha = FakeHA(states=entities(instance), history={
                    instance.entity["heating_state"]: [row(8, 0, "on"), row(8, 20, "off")],
                    instance.entity["temperature"]: [row(8, 0, "22.6")],
                })
                instance.handle_command(OWNER, command)
                message = instance.tg.last
                self.assertTrue(message, f"{language} {command} said nothing")
                self.assertNotIn("{", message, f"{language} {command} leaked a placeholder")
                self.assertNotIn("None", message, f"{language} {command} printed None")


if __name__ == "__main__":
    unittest.main()
