#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline tests: formatting, configuration, state migration, history analysis.

Run from the telegram-bot directory:
    python3 -m unittest discover -s tests -t .
"""

import json
import os
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


def entry(hour, minute, state, day=19):
    return {"state": state,
            "last_changed": datetime(2026, 9, day, hour, minute, tzinfo=LONDON).isoformat()}


class TestTranslations(unittest.TestCase):
    """A missing translation must fail here, not in production."""

    def test_every_language_knows_the_same_keys(self):
        english = set(msg.MESSAGES["en"])
        for language, catalogue in msg.MESSAGES.items():
            self.assertEqual(english, set(catalogue), f"key mismatch in {language}")

    def test_every_message_formats_in_every_language(self):
        # Placeholders must exist in all languages; a stray {name} would raise.
        arguments = dict(percent="50", known="2 h 00 min", span="4 h 00 min", ranges="08.10",
                         gap="2 min", note="", hours=3, first="22.0", last="23.0",
                         heating="10 min", water="1 h 00 min", coverage="", threshold="23", temperature="22.6", target="21.0", day="Monday 19.09",
                         days=7, duration="10 min", periods="2 periods", start="08:00",
                         end="08:10", date="19.09", count=3, label="Heating", at="",
                         schedule="04:30-23:00", state="on", action="heating", mode="schedule",
                         time="08:00", age="5 min", error="boom", version="2.0", seconds=60,
                         since="08:00", uptime="1 h 00 min", hysteresis="0.3", when="none",
                         port=8099, language="en", low=5, high=35, min="21.0", max="23.0",
                         min_time="04:00", max_time="15:00", avg="22.10",
                         entity="sensor.ha_heartbeat", every=5, range="22.10 13:17 – 24.10 13:10")
        for language in msg.MESSAGES:
            locale = msg.make_locale(language, "Europe/London")
            for key in msg.MESSAGES[language]:
                rendered = locale.t(key, **arguments)
                self.assertNotIn("{", rendered, f"{language}/{key} kept a placeholder")

    def test_unknown_language_falls_back_to_english(self):
        self.assertEqual(msg.make_locale("de", "Europe/London").code, "en")


class TestFormatting(unittest.TestCase):
    def setUp(self):
        self.en = msg.make_locale("en", "Europe/London")
        self.pl = msg.make_locale("pl", "Europe/London")

    def test_decimal_separator_follows_the_language(self):
        self.assertEqual(self.en.number(22.6), "22.6")
        self.assertEqual(self.pl.number(22.6), "22,6")
        self.assertEqual(self.pl.number(22.104, 2), "22,10")

    def test_temperature_without_data_has_no_orphaned_degree_sign(self):
        self.assertEqual(self.en.temperature(None), "no data")
        self.assertEqual(self.pl.temperature(None), "brak danych")
        self.assertNotIn("°C", self.pl.temperature(None))
        self.assertEqual(self.en.temperature(22.6), "22.6 °C")

    def test_duration(self):
        self.assertEqual(self.en.duration(0), "0 min")
        self.assertEqual(self.en.duration(59.6), "1 h 00 min")
        self.assertEqual(self.en.duration(95), "1 h 35 min")

    def test_percent_never_rounds_to_a_misleading_value(self):
        locale = msg.make_locale("en", "Europe/London")
        self.assertEqual(locale.percent(859.5223190333334, 859.5223190333336), "100")
        self.assertEqual(locale.percent(239.0, 240.0), "99")
        self.assertEqual(locale.percent(0.5, 240.0), "<1")
        self.assertEqual(locale.percent(0.0, 240.0), "0")

    def test_plural_forms(self):
        self.assertEqual(self.en.count(1, "period"), "1 period")
        self.assertEqual(self.en.count(2, "period"), "2 periods")
        self.assertEqual(self.pl.count(1, "boost"), "1 boost")
        self.assertEqual(self.pl.count(3, "boost"), "3 boosty")
        self.assertEqual(self.pl.count(5, "boost"), "5 boostów")
        self.assertEqual(self.pl.count(12, "boost"), "12 boostów")
        self.assertEqual(self.pl.count(22, "boost"), "22 boosty")

    def test_weekday_names_are_translated(self):
        day = datetime(2026, 9, 19, tzinfo=LONDON).date()
        self.assertTrue(self.en.short_date(day).startswith("Saturday"))
        self.assertTrue(self.pl.short_date(day).startswith("sobota"))

    def test_numbers_accept_both_separators(self):
        self.assertEqual(bot.to_number("22,6"), 22.6)
        self.assertEqual(bot.to_number("22.6"), 22.6)
        self.assertIsNone(bot.to_number("unavailable"))
        self.assertIsNone(bot.to_number(None))

    def test_ha_timestamps(self):
        moment = bot.parse_ha_time("2026-09-19T08:00:00+00:00", LONDON)
        self.assertEqual(moment.hour, 9)  # 08:00 UTC is 09:00 British Summer Time
        self.assertIsNone(bot.parse_ha_time("not a date", LONDON))
        self.assertIsNone(bot.parse_ha_time(None, LONDON))


class TestEnvParsing(unittest.TestCase):
    """The .env.example ships with inline comments, so they must be handled."""

    # Config.load() deliberately lets the environment win over the file, so these tests have
    # to run in a clean one - otherwise they fail for anyone who has BOT_LANG or a threshold
    # exported in their shell profile.
    PREFIXES = ("TELEGRAM_", "HA_", "BOT_")

    def setUp(self):
        self._saved = {key: value for key, value in os.environ.items()
                       if key.startswith(self.PREFIXES)}
        for key in self._saved:
            del os.environ[key]

    def tearDown(self):
        for key in [k for k in os.environ if k.startswith(self.PREFIXES)]:
            del os.environ[key]
        os.environ.update(self._saved)

    def test_inline_comments_are_stripped(self):
        self.assertEqual(bot.strip_inline_comment("21.0   # my threshold"), "21.0")
        self.assertEqual(bot.strip_inline_comment("23.0\t# tab"), "23.0")
        self.assertEqual(bot.strip_inline_comment("http://ha.local:8123 # address"),
                         "http://ha.local:8123")

    def test_hash_inside_a_value_is_kept(self):
        self.assertEqual(bot.strip_inline_comment("pass#word"), "pass#word")
        self.assertEqual(bot.strip_inline_comment('"value # inside quotes"'),
                         "value # inside quotes")

    def _write_env(self, text):
        path = Path(tempfile.mkdtemp()) / ".env"
        path.write_text(text, encoding="utf-8")
        return path

    def test_settings_with_comments_are_applied(self):
        path = self._write_env(
            "TELEGRAM_TOKEN=1:AA\nHA_URL=http://ha:8123   # address\nHA_TOKEN=tok\n"
            "BOT_THRESHOLD_C=21.0   # my threshold\nBOT_POLL_SEC=30 # every half minute\n")
        config = bot.Config.load(path)
        self.assertEqual(config.threshold_c, 21.0)
        self.assertEqual(config.poll_sec, 30)
        self.assertEqual(config.ha_url, "http://ha:8123")

    def test_old_polish_key_names_still_work(self):
        path = self._write_env("TELEGRAM_TOKEN=1:AA\nHA_URL=http://ha:8123\nHA_TOKEN=tok\n"
                               "BOT_PROG_C=21.5\nTELEGRAM_CZAT_ID=123\nBOT_POWIADOM_START=0\n")
        config = bot.Config.load(path)
        self.assertEqual(config.threshold_c, 21.5)
        self.assertEqual(config.chat_id, "123")
        self.assertFalse(config.notify_on_start, "BOT_POWIADOM_START=0 must stay off")

    def test_new_key_wins_over_the_old_one(self):
        path = self._write_env("TELEGRAM_TOKEN=1:AA\nHA_URL=http://ha:8123\nHA_TOKEN=tok\n"
                               "BOT_THRESHOLD_C=20\nBOT_PROG_C=25\n")
        self.assertEqual(bot.Config.load(path).threshold_c, 20.0)

    def test_bad_number_falls_back_to_the_default(self):
        path = self._write_env("TELEGRAM_TOKEN=1:AA\nHA_URL=http://ha:8123\nHA_TOKEN=tok\n"
                               "BOT_THRESHOLD_C=abc\n")
        self.assertEqual(bot.Config.load(path).threshold_c, 23.0)

    def test_environment_overrides_the_file_for_every_prefix(self):
        path = self._write_env("TELEGRAM_TOKEN=1:AA\nHA_URL=http://ha:8123\nHA_TOKEN=tok\n"
                               "BOT_THRESHOLD_C=23\n")
        os.environ["BOT_THRESHOLD_C"] = "19.5"
        os.environ["BOT_ENTITY_TEMPERATURE"] = "sensor.my_own"
        os.environ["HA_URL"] = "http://other:8123"
        config = bot.Config.load(path)
        self.assertEqual(config.threshold_c, 19.5)
        self.assertEqual(config.entities["temperature"], "sensor.my_own")
        self.assertEqual(config.ha_url, "http://other:8123")

    def test_missing_required_settings_stop_the_bot_clearly(self):
        path = self._write_env("HA_URL=http://ha:8123\n")
        with self.assertRaises(SystemExit) as caught:
            bot.Config.load(path)
        self.assertIn("TELEGRAM_TOKEN", str(caught.exception))

    def test_unknown_language_and_zone_fall_back(self):
        path = self._write_env("TELEGRAM_TOKEN=1:AA\nHA_URL=http://ha:8123\nHA_TOKEN=tok\n"
                               "BOT_LANG=klingon\nBOT_TZ=Mars/Olympus\n")
        config = bot.Config.load(path)
        self.assertEqual(config.language, "en")
        self.assertEqual(config.timezone, "Europe/London")


class TestStateMigration(unittest.TestCase):
    """Upgrading must not unbind the chat or replay a day of old commands."""

    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "state.json"

    def test_version_1_state_is_migrated(self):
        self.path.write_text(json.dumps({
            "czat_id": "987654321", "prog_c": 22.5, "alarm_aktywny": True,
            "offset_telegram": 100, "alarmow_dzis": 4, "dzien_licznika": "2026-09-19",
            "zgloszony_brak_ha": True, "ostatni_alarm_temp": 23.4,
        }), encoding="utf-8")
        state = bot.State.load(self.path)
        self.assertEqual(state.chat_id, "987654321")
        self.assertEqual(state.threshold_c, 22.5)
        self.assertEqual(state.telegram_offset, 100)
        self.assertEqual(state.alerts_today, 4)
        self.assertTrue(state.alarm_active)
        self.assertTrue(state.reported_ha_down)
        self.assertTrue(self.path.with_name("state.json.bak").exists(),
                        "a backup of the old file must be kept")

    def test_unknown_keys_are_ignored_not_fatal(self):
        self.path.write_text(json.dumps({"chat_id": "7", "something_new": 1}), encoding="utf-8")
        self.assertEqual(bot.State.load(self.path).chat_id, "7")

    def test_broken_file_does_not_crash(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(bot.State.load(self.path).chat_id)

    def test_round_trip(self):
        state = bot.State(chat_id="5", telegram_offset=11, alerts_today=2)
        state.save(self.path)
        self.assertEqual(bot.State.load(self.path).telegram_offset, 11)
        self.assertNotIn("czat_id", self.path.read_text(encoding="utf-8"))


class TestTimeline(unittest.TestCase):
    """Hive drops to 'unavailable' for a few minutes; that must not be counted as on or off."""

    def setUp(self):
        self.since = datetime(2026, 9, 19, 8, 0, tzinfo=LONDON)
        self.until = datetime(2026, 9, 19, 12, 0, tzinfo=LONDON)

    def segments(self, entries, value=bot.on_off_value, gaps=()):
        return bot.timeline(entries, self.since, self.until, value, gaps)

    def minutes(self, entries):
        return bot.on_minutes(self.segments(entries))

    def test_simple_on_off(self):
        self.assertEqual(self.minutes([entry(8, 0, "on"), entry(8, 10, "off")]), 10)

    def test_unavailable_closes_the_period(self):
        entries = [entry(8, 0, "on"), entry(8, 10, "unavailable"), entry(8, 20, "off")]
        self.assertEqual(self.minutes(entries), 10, "the outage must not count as heating")
        self.assertEqual(bot.known_minutes(self.segments(entries)), 230)

    def test_trailing_unavailable_does_not_run_to_the_end_of_the_window(self):
        entries = [entry(8, 0, "on"), entry(8, 10, "unavailable")]
        self.assertEqual(self.minutes(entries), 10, "data ending in an outage must not count 240")
        self.assertEqual(bot.known_minutes(self.segments(entries)), 10)

    def test_short_blip_is_joined_for_display_but_not_counted(self):
        entries = [entry(8, 0, "on"), entry(8, 10, "unavailable"), entry(8, 12, "on"),
                   entry(8, 30, "off")]
        segments = self.segments(entries)
        runs = bot.on_runs(segments, stitch_gap_min=5.0)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].minutes, 28, "two minutes without data are not heating")
        self.assertEqual(runs[0].unknown_min, 2)
        self.assertEqual(bot.on_minutes(segments), 28)
        self.assertEqual(len(bot.on_runs(segments, stitch_gap_min=0.0)), 2)

    def test_long_gap_is_not_joined(self):
        entries = [entry(8, 0, "on"), entry(8, 10, "unavailable"), entry(9, 0, "on"),
                   entry(9, 10, "off")]
        runs = bot.on_runs(self.segments(entries), stitch_gap_min=5.0)
        self.assertEqual([run.minutes for run in runs], [10, 10])
        self.assertTrue(runs[0].open_end and runs[1].open_start)

    def test_still_on_now_runs_to_the_end_of_the_window(self):
        entries = [entry(8, 0, "off"), entry(11, 0, "on")]
        self.assertEqual(self.minutes(entries), 60)

    def test_repeated_state_does_not_split_the_period(self):
        entries = [entry(8, 0, "on"), entry(8, 5, "on"), entry(8, 20, "off")]
        self.assertEqual(len(bot.on_runs(self.segments(entries))), 1)
        self.assertEqual(self.minutes(entries), 20)

    def test_entries_before_the_window_are_clipped(self):
        entries = [entry(20, 0, "on", day=18), entry(8, 30, "off")]
        self.assertEqual(self.minutes(entries), 30)
        self.assertTrue(bot.is_complete(self.segments(entries)))

    def test_no_entries_means_unknown_not_off(self):
        segments = self.segments([])
        self.assertEqual(bot.on_minutes(segments), 0)
        self.assertEqual(bot.known_minutes(segments), 0)
        self.assertFalse(bot.is_complete(segments))

    def test_only_unavailable_means_unknown(self):
        segments = self.segments([entry(8, 0, "unavailable")])
        self.assertEqual(bot.known_minutes(segments), 0)

    def test_history_starting_late_leaves_the_start_unknown(self):
        segments = self.segments([entry(10, 0, "off")])
        self.assertEqual(bot.known_minutes(segments), 120)
        self.assertEqual(bot.span_minutes(segments), 240)

    def test_segments_cover_the_window_exactly(self):
        segments = self.segments([entry(8, 0, "on"), entry(9, 0, "unknown"), entry(10, 0, "off")])
        self.assertEqual(bot.span_minutes(segments), 240)
        self.assertEqual(segments[0][0], self.since.astimezone(bot.UTC))
        self.assertEqual(segments[-1][1], self.until.astimezone(bot.UTC))


class TestHistoryValues(unittest.TestCase):
    def setUp(self):
        self.since = datetime(2026, 9, 19, 0, 0, tzinfo=LONDON)
        self.until = datetime(2026, 9, 19, 12, 0, tzinfo=LONDON)

    def numbers(self, entries):
        return bot.timeline(entries, self.since, self.until, bot.number_value)

    def test_value_at_takes_the_reading_in_force(self):
        segments = self.numbers([entry(0, 0, "21.5"), entry(9, 0, "22.6"), entry(11, 0, "23.1")])
        self.assertEqual(bot.value_at(segments, datetime(2026, 9, 19, 10, 0, tzinfo=LONDON)), 22.6)

    def test_value_at_does_not_carry_a_reading_through_unavailable(self):
        segments = self.numbers([entry(0, 0, "21.5"), entry(7, 0, "unavailable")])
        self.assertIsNone(bot.value_at(segments, datetime(2026, 9, 19, 8, 0, tzinfo=LONDON)))

    def test_temperature_stats(self):
        segments = self.numbers([entry(0, 0, "21.0"), entry(6, 0, "23.0"), entry(9, 0, "22.0")])
        stats = bot.temperature_stats(segments)
        self.assertEqual(stats["min"], 21.0)
        self.assertEqual(stats["max"], 23.0)
        self.assertEqual((stats["first"], stats["last"]), (21.0, 22.0))
        # 6 h at 21, 3 h at 23, 3 h at 22 -> (126 + 69 + 66) / 12
        self.assertAlmostEqual(stats["average"], 21.75, places=2)

    def test_temperature_stats_skip_unknown_time(self):
        segments = self.numbers([entry(0, 0, "21.0"), entry(6, 0, "unavailable"),
                                 entry(9, 0, "24.0")])
        # 6 h at 21 and 3 h at 24; the 3 h without data weigh nothing.
        self.assertAlmostEqual(bot.temperature_stats(segments)["average"], 22.0, places=2)

    def test_temperature_stats_without_data(self):
        self.assertEqual(bot.temperature_stats(self.numbers([])), {})


class TestDayBounds(unittest.TestCase):
    def _bot_stub(self):
        instance = bot.Bot.__new__(bot.Bot)
        instance.config = bot.Config(telegram_token="1:AA", ha_url="http://ha", ha_token="t",
                                     timezone="Europe/London")
        instance.timezone = LONDON
        instance.locale = msg.make_locale("en", "Europe/London")
        return instance

    def test_a_day_starts_at_local_midnight(self):
        start, end = bot.Bot.day_bounds(self._bot_stub(),
                                        datetime(2026, 9, 1, tzinfo=LONDON).date())
        self.assertEqual((start.hour, start.minute), (0, 0))
        self.assertEqual(end - start, timedelta(hours=24))

    def test_clocks_going_forward_gives_a_23_hour_day(self):
        # A day boundary is local midnight to local midnight, but the real time that passes
        # is 23 hours - and that is what a duration must report.
        start, end = bot.Bot.day_bounds(self._bot_stub(),
                                        datetime(2026, 3, 29, tzinfo=LONDON).date())
        self.assertEqual((start.hour, end.hour), (0, 0))
        self.assertEqual(bot.elapsed_minutes(start, end), 23 * 60)

    def test_clocks_going_back_gives_a_25_hour_day(self):
        start, end = bot.Bot.day_bounds(self._bot_stub(),
                                        datetime(2025, 10, 26, tzinfo=LONDON).date())
        self.assertEqual(bot.elapsed_minutes(start, end), 25 * 60)

    def test_a_period_across_the_clock_change_is_not_an_hour_out(self):
        # 00:30 GMT to 02:30 BST: the wall clock moved two hours, one hour really passed.
        before = datetime(2026, 3, 29, 0, 30, tzinfo=LONDON)
        after = datetime(2026, 3, 29, 2, 30, tzinfo=LONDON)
        self.assertEqual((after - before).total_seconds() / 60, 120, "naive subtraction")
        self.assertEqual(bot.elapsed_minutes(before, after), 60, "real elapsed time")
        self.assertEqual(bot.span_minutes([(before, after, "on")]), 60)

    def test_today_is_never_projected_into_the_future(self):
        stub = self._bot_stub()
        start, end = bot.Bot.day_bounds(stub, datetime.now(LONDON).date())
        self.assertLessEqual(end, datetime.now(LONDON) + timedelta(seconds=2))


if __name__ == "__main__":
    unittest.main()
