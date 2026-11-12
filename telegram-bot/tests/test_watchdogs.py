#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Watchdog tests: a stuck Home Assistant, a recorder that stopped, the external ping and
the report after the bot itself was gone. Synthetic data only."""

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hive_bot as bot  # noqa: E402
from tests.test_commands import FakeHA, make_bot  # noqa: E402

LONDON = ZoneInfo("Europe/London")
NOW = datetime(2026, 3, 6, 12, 0, tzinfo=LONDON)
BEAT = "sensor.ha_heartbeat"
SECRET_URL = "https://hc-ping.example/0b1c2d3e-secret"


def beat(minutes_ago, value=None, now=NOW):
    moment = now - timedelta(minutes=minutes_ago)
    return {"state": value or moment.isoformat(), "last_changed": moment.isoformat(),
            "last_updated": moment.isoformat(), "attributes": {}}


class PulseHA(FakeHA):
    """FakeHA with a heartbeat entity, its recorded history and an optional 404."""

    def __init__(self, heartbeat=None, recorded=None, missing=False, **kwargs):
        super().__init__(**kwargs)
        self.heartbeat = heartbeat
        self.recorded = recorded            # None: the history holds the live value
        self.missing = missing

    def state(self, entity):
        if entity == BEAT:
            self._raise_if_needed()
            if self.missing:
                raise bot.HAError("HTTP Error 404: Not Found", status=404)
            return self.heartbeat
        return super().state(entity)

    def history(self, entities, since, until, attributes=False):
        if entities == [BEAT]:
            self.history_calls.append((list(entities), since, until, attributes))
            rows = self.recorded if self.recorded is not None else [
                {"state": self.heartbeat["state"], "last_changed": self.heartbeat["last_changed"]}]
            return {BEAT: list(rows)}
        return super().history(entities, since, until, attributes)


def watched(language="en", heartbeat=None, recorded=None, missing=False, ping_url=SECRET_URL,
            ping_error=None, **settings):
    instance = make_bot(language, now=NOW, ping_url=ping_url, **settings)
    instance.ha = PulseHA(heartbeat=heartbeat or beat(2), recorded=recorded, missing=missing)
    instance.health.ha_ok = True
    instance.health.ping = "held" if ping_url else "off"
    instance.pings = []

    def fake_ping(url):
        instance.pings.append(url)
        return ping_error
    instance.ping = fake_ping
    return instance


class TestHeartbeat(unittest.TestCase):
    def test_a_ticking_home_assistant_is_quiet_and_vouched_for(self):
        instance = watched(heartbeat=beat(4))
        instance.check_pulse()
        self.assertEqual(instance.tg.sent, [])
        self.assertEqual(instance.health.pulse, "ok")
        self.assertTrue(instance.health.recorder_ok)
        self.assertEqual(instance.pings, [SECRET_URL])
        self.assertEqual(instance.health.ping, "ok")

    def test_a_stopped_heartbeat_alerts_once_and_holds_the_ping(self):
        instance = watched(heartbeat=beat(47 * 60))
        instance.check_pulse()
        instance.check_pulse()
        self.assertEqual(len(instance.tg.sent), 1, "one alert per outage, not one per check")
        text = instance.tg.last
        self.assertIn("stopped working", text)
        self.assertIn(BEAT, text)
        self.assertIn("47 h", text)
        self.assertIn("(04.03 13:00)", text, "a tick from another day carries its date")
        self.assertEqual(instance.pings, [], "silence is what makes the outside service alarm")
        self.assertEqual(instance.health.ping, "held")
        self.assertTrue(instance.state.reported_ha_stalled)

        instance.ha.heartbeat = beat(1)
        instance.check_pulse()
        self.assertIn("ticking again", instance.tg.last)
        self.assertFalse(instance.state.reported_ha_stalled)
        self.assertEqual(instance.pings, [SECRET_URL])

    def test_up_to_the_threshold_is_not_an_alert(self):
        instance = watched(heartbeat=beat(20), heartbeat_max_min=20)
        instance.check_pulse()
        self.assertEqual(instance.tg.sent, [])
        instance.ha.heartbeat = beat(21)
        instance.check_pulse()
        self.assertEqual(len(instance.tg.sent), 1)

    def test_polish_alert(self):
        instance = watched("pl", heartbeat=beat(90))
        instance.check_pulse()
        self.assertIn("przestał działać", instance.tg.last)
        self.assertIn("harmonogram ciepłej wody", instance.tg.last)

    def test_a_missing_heartbeat_entity_is_not_an_alarm(self):
        instance = watched(missing=True)
        with self.assertLogs(bot.log, "WARNING") as logs:
            instance.check_pulse()
            instance.check_pulse()
        self.assertEqual(sum("BOT_ENTITY_HEARTBEAT=off" in line for line in logs.output), 1)
        self.assertEqual(instance.tg.sent, [])
        self.assertEqual(instance.health.pulse, "missing")
        self.assertEqual(len(instance.pings), 2, "HA answers, so the ping still goes out")

    def test_no_value_yet_after_a_restart_is_not_an_alarm(self):
        instance = watched(heartbeat={"state": "unknown", "last_updated": NOW.isoformat()})
        instance.check_pulse()
        self.assertEqual(instance.tg.sent, [])
        self.assertEqual(instance.health.pulse, "unknown")
        self.assertEqual(instance.pings, [], "nothing is vouched for before the first tick")

    def test_switched_off(self):
        instance = watched()
        instance.entity["heartbeat"] = ""
        instance.check_pulse()
        self.assertEqual(instance.ha.history_calls, [])
        self.assertEqual(instance.pings, [SECRET_URL])

    def test_home_assistant_down_holds_the_ping(self):
        instance = watched()
        instance.health.ha_ok = False
        instance.check_pulse()
        self.assertEqual(instance.pings, [])
        self.assertEqual(instance.tg.sent, [], "'no contact' is reported by its own alert")


class TestRecorder(unittest.TestCase):
    def test_a_fresh_tick_is_given_time_to_reach_the_database(self):
        instance = watched(heartbeat=beat(1), recorded=[])
        instance.check_pulse()
        self.assertEqual(instance.ha.history_calls, [])
        self.assertIsNone(instance.health.recorder_ok)

    def test_ticks_missing_from_the_history_alert_after_the_threshold(self):
        old_value = (NOW - timedelta(minutes=60)).isoformat()
        instance = watched(heartbeat=beat(4), recorded=[{"state": old_value}])
        instance.check_pulse()
        self.assertEqual(instance.tg.sent, [], "one missing tick can be a slow database")
        self.assertFalse(instance.health.recorder_ok)
        self.assertEqual(instance.pings, [], "not vouched for while it is unclear")

        later = NOW + timedelta(minutes=20)
        instance.clock = later
        instance.ha.heartbeat = beat(4, now=later)
        instance.check_pulse()
        self.assertEqual(len(instance.tg.sent), 1)
        self.assertIn("not saving history", instance.tg.last)
        self.assertIn("11:56", instance.tg.last, "counted from the first tick that never arrived")
        instance.check_pulse()
        self.assertEqual(len(instance.tg.sent), 1)

        instance.ha.recorded = None         # the database has the live value again
        instance.check_pulse()
        self.assertIn("saving history again", instance.tg.last)
        self.assertFalse(instance.state.reported_recorder_stalled)
        self.assertEqual(instance.pings, [SECRET_URL])


class TestExternalPing(unittest.TestCase):
    def test_off_without_an_address(self):
        instance = watched(ping_url="")
        instance.check_pulse()
        self.assertEqual(instance.pings, [])
        self.assertEqual(instance.health.ping, "off")

    def test_a_failing_ping_is_logged_once_without_the_address(self):
        instance = watched(ping_error="no connection (timed out)")
        with self.assertLogs(bot.log, "WARNING") as logs:
            instance.check_pulse()
            instance.check_pulse()
        self.assertEqual(len(logs.output), 1)
        self.assertNotIn("secret", "".join(logs.output))
        self.assertEqual(instance.health.ping, "failing")
        self.assertEqual(instance.tg.sent, [])

    def test_send_ping_never_quotes_the_address(self):
        for address in ("not-a-url/0b1c2d3e-secret", "file:///etc/0b1c2d3e-secret",
                        "ftp://example/0b1c2d3e-secret"):
            error = bot.send_ping(address)
            self.assertEqual(error, "not a valid http(s) address")

    def test_health_and_status_never_show_the_address(self):
        instance = watched()
        instance.check_pulse()
        self.assertNotIn("secret", str(instance.health.snapshot()))
        instance.cmd_status()
        self.assertNotIn("secret", instance.tg.last)
        self.assertIn("External ping: last sent 12:00", instance.tg.last)
        self.assertIn("Home Assistant heartbeat: last tick 2 min ago", instance.tg.last)


class TestDowntimeReport(unittest.TestCase):
    def test_a_long_silence_is_reported_with_home_assistants_own_gaps(self):
        instance = watched("pl")
        instance.state.last_alive_iso = datetime(2026, 3, 4, 12, 15, tzinfo=LONDON).isoformat()
        instance.ha.entries = [{"domain": "homeassistant", "message": "started",
                                "when": datetime(2026, 3, 6, 11, 11, tzinfo=LONDON).isoformat()}]
        instance.report_downtime()
        text = instance.tg.last
        self.assertIn("Bot nie działał", text)
        self.assertIn("04.03 12:15 – 06.03 12:00", text)
        self.assertIn("47 h 45 min", text)
        self.assertIn("Home Assistant", text)

    def test_a_restart_is_not_reported(self):
        instance = watched()
        instance.state.last_alive_iso = (NOW - timedelta(minutes=6)).isoformat()
        instance.report_downtime()
        self.assertEqual(instance.tg.sent, [])

    def test_first_start_is_not_reported(self):
        instance = watched()
        instance.report_downtime()
        self.assertEqual(instance.tg.sent, [])

    def test_every_check_leaves_a_sign_of_life(self):
        instance = watched()
        instance.check_pulse()
        stored = bot.State.load(instance.state_file)
        self.assertEqual(stored.last_alive_iso, NOW.isoformat())


class TestSettings(unittest.TestCase):
    def load(self, **values):
        import os
        import tempfile
        path = Path(tempfile.mkdtemp()) / ".env"
        lines = ["TELEGRAM_TOKEN=1:AA", "HA_URL=http://ha", "HA_TOKEN=x"]
        lines += [f"{key}={value}" for key, value in values.items()]
        path.write_text("\n".join(lines))
        saved = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("BOT_")}
        try:
            return bot.Config.load(path)
        finally:
            os.environ.update(saved)

    def test_defaults(self):
        config = self.load()
        self.assertEqual(config.entities["heartbeat"], BEAT)
        self.assertEqual(config.heartbeat_max_min, 20)
        self.assertEqual(config.ping_url, "")

    def test_switch_off_and_floor(self):
        config = self.load(BOT_ENTITY_HEARTBEAT="off", BOT_HEARTBEAT_MAX_MIN="3",
                           BOT_PING_URL=SECRET_URL)
        self.assertEqual(config.entities["heartbeat"], "")
        self.assertEqual(config.heartbeat_max_min, 2 * bot.PULSE_EVERY_MIN,
                         "below two ticks every restart would alarm")
        self.assertEqual(config.ping_url, SECRET_URL)


if __name__ == "__main__":
    unittest.main()
