#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Heating demand from climate hvac_action, data coverage and Home Assistant outages.

Synthetic data only. Run from the telegram-bot directory:
    python3 -m unittest discover -s tests -t .
"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hive_bot as bot  # noqa: E402
import messages as msg  # noqa: E402
from tests.test_commands import FakeHA, entities, make_bot  # noqa: E402

LONDON = ZoneInfo("Europe/London")
UTC = timezone.utc


def local(day, hour=0, minute=0, month=9, year=2026):
    return datetime(year, month, day, hour, minute, tzinfo=LONDON)


def utc_text(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=UTC).isoformat()


def climate(moment, action, current=21.0, target=21.0, state="heat", changed=None):
    """A climate row stamped like Home Assistant does: attribute updates move last_updated."""
    stamp = moment if isinstance(moment, str) else moment.isoformat()
    attributes = {"current_temperature": current, "temperature": target}
    if action is not None:
        attributes["hvac_action"] = action
    return {"state": state, "attributes": attributes,
            "last_changed": changed or stamp, "last_updated": stamp}


def plain(moment, state):
    return {"state": state, "last_changed": moment.isoformat()}


def ha_event(moment, message):
    return {"name": "Home Assistant", "message": message, "domain": "homeassistant",
            "when": moment.isoformat()}


def demand_minutes(rows, since, until, gaps=()):
    return bot.on_minutes(bot.timeline(rows, since, until, bot.heat_demand_value, gaps))


class TestHeatDemandSignal(unittest.TestCase):
    def test_heating_at_the_target_temperature_counts(self):
        # pyhive's binary 'state' sensor is current < target, so it says off here. Hive's
        # own call for heat (hvac_action) is what counts.
        rows = [climate(local(19, 8), "heating", current=22.0, target=22.0),
                climate(local(19, 9), "idle", current=22.1, target=22.0)]
        self.assertEqual(demand_minutes(rows, local(19, 8), local(19, 10)), 60)

    def test_attribute_only_changes_use_last_updated(self):
        # The state stays 'heat' all day, so last_changed never moves; only last_updated
        # shows when hvac_action changed.
        changed = local(19, 0).isoformat()
        rows = [climate(local(19, 0), "idle", changed=changed),
                climate(local(19, 9), "heating", changed=changed),
                climate(local(19, 9, 30), "idle", changed=changed),
                climate(local(19, 14), "heating", changed=changed),
                climate(local(19, 14, 5), "idle", changed=changed)]
        self.assertEqual(demand_minutes(rows, local(19), local(19, 18)), 35)

    def test_unknown_states_and_missing_attribute_are_unknown(self):
        rows = [climate(local(19, 8), "heating"),
                climate(local(19, 9), None, state="unavailable"),
                climate(local(19, 10), None, state="heat"),       # no hvac_action at all
                climate(local(19, 11), "off", state="off"),
                climate(local(19, 12), "heating", state="unknown")]
        segments = bot.timeline(rows, local(19, 8), local(19, 13), bot.heat_demand_value)
        self.assertEqual(bot.on_minutes(segments), 60)
        self.assertEqual(bot.known_minutes(segments), 120, "08-09 heating and 11-12 off")

    def test_climate_history_is_requested_with_attributes(self):
        asked = []
        client = bot.HomeAssistant("http://ha.local:8123", "token")
        client._get = lambda path, params=None, timeout_sec=25: asked.append((path, params)) or []
        client.history(["climate.thermostat_1"], local(19), local(19, 12), attributes=True)
        client.history(["binary_sensor.thermostat_1_boost"], local(19), local(19, 12))
        with_attributes, plain_states = asked[0][1], asked[1][1]
        self.assertNotIn("no_attributes", with_attributes)
        self.assertNotIn("minimal_response", with_attributes)
        self.assertEqual(with_attributes.get("significant_changes_only"), "0")
        self.assertEqual(plain_states.get("no_attributes"), "true")
        self.assertIn("2026-09-18T23:00:00+00:00", asked[0][0], "UTC in the address")

    def test_the_bot_no_longer_reads_the_binary_heating_sensor(self):
        self.assertNotIn("heating_state", bot.DEFAULT_ENTITIES)


class TestDayBoundariesAndClockChanges(unittest.TestCase):
    def test_a_run_across_midnight_is_split_between_the_days(self):
        instance = make_bot("en", now=local(19, 18))
        instance.ha = FakeHA(states=entities(instance), history={instance.entity["climate"]: [
            climate(local(17, 0), "idle"), climate(local(18, 23, 30), "heating"),
            climate(local(19, 0, 30), "idle")]})
        instance.cmd_heating("3")
        message = instance.tg.last
        self.assertIn("17.09: 0 min", message)
        self.assertIn("18.09: 30 min", message)
        self.assertIn("19.09: 30 min", message)
        self.assertIn("Total: 1 h 00 min", message)

    def test_the_initial_row_before_the_window_sets_the_start(self):
        # Home Assistant sends the row in force at the window start, stamped earlier.
        rows = [climate(local(18, 22), "heating"), climate(local(19, 1), "idle")]
        self.assertEqual(demand_minutes(rows, local(19), local(19, 12)), 60)

    def test_autumn_clock_change_gives_a_25_hour_day(self):
        instance = make_bot("en", now=local(26, 12, month=10))
        instance.ha = FakeHA(states=entities(instance), history={instance.entity["climate"]: [
            climate(local(24, 0, month=10), "heating")]})
        instance.cmd_heating("3")
        message = instance.tg.last
        self.assertIn("24.10: 24 h 00 min", message)
        self.assertIn("25.10: 25 h 00 min", message)
        self.assertIn("26.10: 12 h 00 min", message)

    def test_spring_clock_change_gives_a_23_hour_day(self):
        rows = [climate(local(28, 0, month=3), "heating")]
        start, end = local(29, month=3), local(30, month=3)
        self.assertEqual(demand_minutes(rows, start, end), 23 * 60)

    def test_a_run_through_the_repeated_hour_is_measured_in_real_time(self):
        # 00:30 BST (23:30 UTC) to 01:30 GMT (01:30 UTC): two real hours. Rows written in
        # UTC by Home Assistant must also sort correctly inside the repeated hour.
        rows = [climate(utc_text(2026, 10, 24, 23, 30), "heating"),
                climate(utc_text(2026, 10, 25, 0, 45), "heating"),   # 01:45 BST
                climate(utc_text(2026, 10, 25, 1, 30), "idle")]      # 01:30 GMT
        day_start, day_end = local(25, month=10), local(26, month=10)
        self.assertEqual(demand_minutes(rows, day_start, day_end), 120)


class TestHomeAssistantOutages(unittest.TestCase):
    def test_a_clean_stop_and_start(self):
        logbook = [ha_event(local(19, 10), "stopped"), ha_event(local(19, 12), "started")]
        gaps = bot.ha_downtime(logbook, local(19), local(19, 18))
        self.assertEqual(gaps, [(local(19, 10).astimezone(UTC), local(19, 12).astimezone(UTC))])

    def test_an_old_state_is_not_carried_through_an_outage(self):
        rows = [climate(local(19, 9), "heating"), climate(local(19, 13), "idle")]
        logbook = [ha_event(local(19, 10), "stopped"), ha_event(local(19, 12), "started")]
        gaps = bot.ha_downtime(logbook, local(19), local(19, 18))
        segments = bot.timeline(rows, local(19, 9), local(19, 18), bot.heat_demand_value, gaps)
        self.assertEqual(bot.on_minutes(segments), 120, "09-10 and 12-13, not 10-12")
        self.assertEqual(bot.known_minutes(segments), 7 * 60)

    def test_an_unchanged_state_resumes_after_a_short_restart(self):
        # Home Assistant writes no new row for a state that did not change across a restart,
        # so the last value resumes after 'started'; only the outage itself is unknown.
        rows = [plain(local(19, 9), "on"), plain(local(19, 13), "off")]
        logbook = [ha_event(local(19, 10), "stopped"), ha_event(local(19, 10, 1), "started")]
        gaps = bot.ha_downtime(logbook, local(19), local(19, 18))
        segments = bot.timeline(rows, local(19, 9), local(19, 18), gaps=gaps)
        self.assertEqual(bot.on_minutes(segments), 239)
        self.assertEqual(bot.known_minutes(segments), 9 * 60 - 1)

    def test_a_sliver_of_old_state_after_a_restart_is_not_data(self):
        # After a long outage the first new row ('unavailable') can land a fraction of a second
        # after 'started'. That sliver must not turn a day without data into "0 min".
        instance = make_bot("en", now=local(19, 12))
        started = local(19, 8)
        instance.ha = FakeHA(
            states=entities(instance),
            history={instance.entity["climate"]: [
                climate(local(18, 20), "idle"),
                climate((started + timedelta(milliseconds=1)).isoformat(), None,
                        state="unavailable")]},
            logbook=[ha_event(started, "started")])
        instance.cmd_heating()
        self.assertIn("No data for these 12 h 00 min", instance.tg.last)
        self.assertNotIn("Call for heat: 0 min", instance.tg.last)

    def test_a_crash_starts_the_outage_at_the_last_sign_of_life(self):
        # A power cut logs no 'stopped'. Rows written while booting, just before 'started',
        # are not signs that the old instance was alive.
        rows = [climate(local(10, 9), "heating"), climate(local(10, 17, 13), "idle"),
                climate(local(22, 7, 58, month=9), None, state="unavailable"),
                climate(local(22, 8, 30, month=9), "heating")]
        logbook = [{"when": local(10, 17).isoformat(), "entity_id": "automation.snapshot",
                    "message": "triggered"},
                   ha_event(local(22, 8), "started")]
        evidence = [bot.row_time(row) for row in rows]
        gaps = bot.ha_downtime(logbook, local(1), local(22, 12), evidence)
        self.assertEqual(gaps, [(local(10, 17, 13).astimezone(UTC), local(22, 8).astimezone(UTC))])
        segments = bot.timeline(rows, local(1), local(22, 12), bot.heat_demand_value, gaps)
        self.assertAlmostEqual(bot.on_minutes(segments), (17 * 60 + 13 - 9 * 60) + 210)

    def test_a_start_inside_the_window_without_any_earlier_sign(self):
        logbook = [ha_event(local(19, 6), "started")]
        gaps = bot.ha_downtime(logbook, local(19), local(19, 18))
        self.assertEqual(gaps, [(local(19).astimezone(UTC), local(19, 6).astimezone(UTC))])

    def test_the_commands_show_the_outage_and_partial_data(self):
        instance = make_bot("en", now=local(19, 12))
        instance.ha = FakeHA(
            states=entities(instance),
            history={instance.entity["climate"]: [climate(local(19), "heating"),
                                                  climate(local(19, 9), "idle")]},
            logbook=[ha_event(local(19, 3), "stopped"), ha_event(local(19, 6), "started")])
        instance.cmd_heating()
        message = instance.tg.last
        self.assertIn("Call for heat: 6 h 00 min in 2 periods", message,
                      "00-03 and 06-09; the three hours without Home Assistant are not heating")
        self.assertIn("Data for 9 h 00 min of 12 h 00 min (75%)", message)
        self.assertIn("Home Assistant was not running: 19.09 03:00–06:00", message)

    def test_an_unreadable_logbook_is_said_out_loud(self):
        instance = make_bot("pl", now=local(19, 12))
        instance.ha = FakeHA(states=entities(instance), logbook_error="404",
                             history={instance.entity["climate"]: [climate(local(19), "idle")]})
        instance.cmd_heating()
        self.assertIn("przerw w pracy samego HA nie sprawdzono", instance.tg.last)


class TestMissingDataIsNotZero(unittest.TestCase):
    def test_heating_without_any_history(self):
        instance = make_bot("en")
        instance.ha = FakeHA(states=entities(instance))
        instance.cmd_heating()
        message = instance.tg.last
        self.assertIn("No data for these 18 h 00 min", message)
        self.assertNotIn("Call for heat: 0 min", message)
        self.assertIn("No known call for heat in the last 30 days (data for 0% of that time)",
                      message)

    def test_heating_with_an_unavailable_morning(self):
        instance = make_bot("pl")
        instance.ha = FakeHA(states=entities(instance), history={instance.entity["climate"]: [
            climate(local(19), None, state="unavailable"), climate(local(19, 9), "heating"),
            climate(local(19, 9, 30), "idle")]})
        instance.cmd_heating()
        message = instance.tg.last
        self.assertIn("Żądanie ciepła: 30 min w 1 okresie", message)
        self.assertIn("Dane za 9 h 00 min z 18 h 00 min (50%)", message)
        self.assertIn("brak danych tuż przed", message)

    def test_a_range_marks_days_without_data(self):
        instance = make_bot("en")
        instance.ha = FakeHA(states=entities(instance), history={instance.entity["climate"]: [
            climate(local(17, 12), "idle"), climate(local(19, 8), "heating"),
            climate(local(19, 8, 10), "idle")]})
        instance.cmd_heating("4")
        message = instance.tg.last
        self.assertIn("16.09: no data", message)
        self.assertIn("17.09: 0 min (data for 50% of the time)", message)
        self.assertIn("18.09: 0 min\n", message)
        self.assertIn("19.09: 10 min", message)

    def test_today_without_history_says_no_data(self):
        instance = make_bot("en")
        instance.ha = FakeHA(states=entities(instance))
        instance.cmd_today()
        message = instance.tg.last
        self.assertIn("Heating, call for heat: no data", message)
        self.assertIn("Hot water, control on: no data", message)
        self.assertIn("Heating Boosts: no data", message)

    def test_water_unknown_now_is_not_off(self):
        instance = make_bot("pl")
        instance.ha = FakeHA(states=entities(instance, water_heater="unavailable"))
        instance.cmd_water()
        message = instance.tg.last
        self.assertIn("Teraz: nieznany („unavailable”)", message)
        self.assertNotIn("wyłączone", message.split("\n")[2])

    def test_a_short_blip_inside_a_run_is_shown_and_not_counted(self):
        instance = make_bot("en")
        instance.ha = FakeHA(states=entities(instance), history={instance.entity["climate"]: [
            climate(local(19), "idle"), climate(local(19, 8), "heating"),
            climate(local(19, 8, 10), None, state="unavailable"),
            climate(local(19, 8, 12), "heating"), climate(local(19, 8, 30), "idle")]})
        instance.cmd_heating()
        message = instance.tg.last
        self.assertIn("Call for heat: 28 min in 1 period", message)
        self.assertIn("08:00 – 08:30  (28 min; +2 min without data, not counted)", message)

    def test_last_heating_followed_by_missing_data_says_so(self):
        instance = make_bot("en")
        instance.ha = FakeHA(states=entities(instance), history={instance.entity["climate"]: [
            climate(local(1), "idle"), climate(local(10, 9), "heating"),
            climate(local(10, 10), "idle"), climate(local(15), None, state="unavailable")]})
        instance.cmd_heating()
        self.assertIn("Last call for heat: 10.09 09:00 – 10:00 (1 h 00 min)", instance.tg.last)
        self.assertIn("Since then, data for", instance.tg.last)


class TestWordingInBothLanguages(unittest.TestCase):
    def test_no_message_claims_radiators_or_a_valve_fault(self):
        for language, catalogue in msg.MESSAGES.items():
            for key, text in catalogue.items():
                lowered = text.lower()
                for word in ("valve", "leak", "zawór", "zaworu", "przeciek", "radiators:",
                             "grzejniki:", "grzeją"):
                    self.assertNotIn(word, lowered, f"{language}/{key}")

    def test_both_languages_explain_the_signals(self):
        english, polish = msg.MESSAGES["en"], msg.MESSAGES["pl"]
        self.assertIn("not boiler firing", english["heating_signal_note"])
        self.assertIn("nie czas palenia kotła", polish["heating_signal_note"])
        self.assertIn("not continuous boiler firing", english["water_note"])
        self.assertIn("nie ciągła praca palnika", polish["water_note"])
        self.assertIn("CWU", polish["now_water"])
        self.assertIn("CO", polish["now_heating"])

    def test_the_same_situation_reads_the_same_in_both_languages(self):
        outputs = {}
        for language in ("en", "pl"):
            instance = make_bot(language, now=local(19, 12))
            instance.ha = FakeHA(
                states=entities(instance),
                history={instance.entity["climate"]: [climate(local(19), "heating"),
                                                      climate(local(19, 9), "idle")]},
                logbook=[ha_event(local(19, 3), "stopped"), ha_event(local(19, 6), "started")])
            instance.cmd_heating()
            outputs[language] = instance.tg.last
        for fragment_en, fragment_pl in (("6 h 00 min", "6 h 00 min"), ("75%", "75%"),
                                         ("03:00–06:00", "03:00–06:00"),
                                         ("hvac_action", "hvac_action")):
            self.assertIn(fragment_en, outputs["en"])
            self.assertIn(fragment_pl, outputs["pl"])
        self.assertEqual(outputs["en"].count("\n"), outputs["pl"].count("\n"))


if __name__ == "__main__":
    unittest.main()
