import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from homework_agent.calendar_service import GoogleCalendarService, match_student, normalize
from homework_agent.db import Database, init_database
from homework_agent.models import Lesson, Student, StudentAlias
from homework_agent.services import DomainError, HomeworkService


class CalendarMatchingTests(unittest.TestCase):
    def test_matches_unique_alias(self):
        damir = Student(id=1, name="Дамир Ахметов", active=True)
        damir.aliases = [StudentAlias(alias="Дамир")]
        aruzhan = Student(id=2, name="Аружан", active=True)
        aruzhan.aliases = []
        self.assertEqual(match_student("Математика — Дамир", [damir, aruzhan]), damir)

    def test_ambiguous_event_is_not_matched(self):
        first = Student(id=1, name="Иван Петров", active=True)
        first.aliases = [StudentAlias(alias="Иван")]
        second = Student(id=2, name="Иван Сидоров", active=True)
        second.aliases = [StudentAlias(alias="Иван")]
        self.assertIsNone(match_student("Урок Иван", [first, second]))

    def test_normalize_handles_yo_and_punctuation(self):
        self.assertEqual(normalize(" Алёна—10Б "), "алена 10б")

    def test_oauth_requires_one_time_interactive_authorization(self):
        with TemporaryDirectory() as temp_dir:
            service = GoogleCalendarService(
                database=Database(f"sqlite:///{Path(temp_dir) / 'test.db'}"),
                timezone=ZoneInfo("UTC"),
                calendar_id="primary",
                service_account_file=None,
                oauth_client_id="client-id",
                oauth_client_secret="client-secret",
                oauth_token_file=Path(temp_dir) / "token.json",
                allow_interactive_oauth=False,
            )

            self.assertTrue(service.enabled)
            self.assertEqual(service.auth_mode, "oauth")
            with self.assertRaisesRegex(DomainError, "check_calendar.py"):
                service._oauth_credentials()

    def test_week_sync_keeps_all_events_and_sets_45_minute_lesson(self):
        with TemporaryDirectory() as temp_dir:
            database = init_database(
                f"sqlite:///{(Path(temp_dir) / 'calendar.db').as_posix()}"
            )
            timezone = ZoneInfo("Asia/Qyzylorda")
            homeworks = HomeworkService(database, timezone)
            homeworks.add_student("Дамир", aliases=["Дамир"])
            service = GoogleCalendarService(
                database, timezone, "primary", None, lesson_duration_minutes=45
            )
            service._list_events_range = lambda _day, _days: [
                {
                    "id": "lesson-1",
                    "summary": "Математика — Дамир",
                    "description": "Производные",
                    "location": "Zoom",
                    "htmlLink": "https://calendar.google.com/event?eid=1",
                    "status": "confirmed",
                    "start": {"dateTime": "2026-09-14T10:00:00+05:00"},
                    "end": {"dateTime": "2026-09-14T11:00:00+05:00"},
                },
                {
                    "id": "other-1",
                    "summary": "Позвонить в банк",
                    "start": {"dateTime": "2026-09-15T12:00:00+05:00"},
                    "end": {"dateTime": "2026-09-15T12:30:00+05:00"},
                },
                {
                    "id": "all-day-1",
                    "summary": "Выходной",
                    "start": {"date": "2026-09-16"},
                    "end": {"date": "2026-09-17"},
                },
            ]

            result = service.sync_range(date(2026, 9, 14), 7)
            schedule = service.week_schedule(date(2026, 9, 14))
            reminders = homeworks.lesson_end_reminders(
                datetime(2026, 9, 14, 10, 0, tzinfo=timezone), 2
            )

            self.assertEqual(result["events_total"], 3)
            self.assertEqual(len(schedule["events"]), 3)
            self.assertEqual(reminders[0]["run_at"].strftime("%H:%M"), "10:43")
            with database.session() as session:
                lesson = session.query(Lesson).one()
                self.assertEqual(
                    lesson.ends_at - lesson.starts_at,
                    timedelta(minutes=45),
                )
            database.engine.dispose()


if __name__ == "__main__":
    unittest.main()
