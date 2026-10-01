from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from db import Database
from models import CalendarEvent, CalendarEventStatus, Lesson, LessonStatus, Student
from services import DomainError, utc_now


SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


def normalize(value: str) -> str:
    value = value.casefold().replace("ё", "е")
    return " ".join(re.sub(r"[^\w]+", " ", value, flags=re.UNICODE).split())


def match_student(text: str, students: list[Student]) -> Student | None:
    haystack = f" {normalize(text)} "
    matches: dict[int, tuple[int, Student]] = {}
    for student in students:
        candidates = [student.name, *(alias.alias for alias in student.aliases)]
        for candidate in candidates:
            needle = normalize(candidate)
            if len(needle) >= 3 and f" {needle} " in haystack:
                previous = matches.get(student.id)
                if not previous or len(needle) > previous[0]:
                    matches[student.id] = (len(needle), student)
    if len(matches) != 1:
        return None
    return next(iter(matches.values()))[1]


class GoogleCalendarService:
    def __init__(
        self,
        database: Database,
        timezone: ZoneInfo,
        calendar_id: str | None,
        service_account_file,
        delegated_subject: str | None = None,
        oauth_client_id: str | None = None,
        oauth_client_secret: str | None = None,
        oauth_token_file=None,
        allow_interactive_oauth: bool = False,
        lesson_duration_minutes: int = 45,
    ):
        self.database = database
        self.timezone = timezone
        self.calendar_id = calendar_id
        self.service_account_file = service_account_file
        self.delegated_subject = delegated_subject
        self.oauth_client_id = oauth_client_id
        self.oauth_client_secret = oauth_client_secret
        self.oauth_token_file = oauth_token_file
        self.allow_interactive_oauth = allow_interactive_oauth
        self.lesson_duration_minutes = lesson_duration_minutes

    @property
    def enabled(self) -> bool:
        has_service_account = bool(self.service_account_file)
        has_oauth = bool(self.oauth_client_id and self.oauth_client_secret)
        return bool(self.calendar_id and (has_service_account or has_oauth))

    @property
    def auth_mode(self) -> str:
        if self.service_account_file and self.service_account_file.exists():
            return "service_account"
        if self.oauth_client_id and self.oauth_client_secret:
            return "oauth"
        return "disabled"

    def _oauth_credentials(self) -> Credentials:
        if not self.oauth_token_file:
            raise DomainError("Не задан GOOGLE_OAUTH_TOKEN_FILE.")
        credentials = None
        if self.oauth_token_file.exists():
            credentials = Credentials.from_authorized_user_file(str(self.oauth_token_file), SCOPES)
        if credentials and credentials.valid:
            return credentials
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            if not self.allow_interactive_oauth:
                raise DomainError(
                    "Google Calendar ещё не авторизован. Останови бота и один раз "
                    "запусти: python check_calendar.py"
                )
            client_config = {
                "installed": {
                    "client_id": self.oauth_client_id,
                    "client_secret": self.oauth_client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": ["http://localhost"],
                }
            }
            flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
            credentials = flow.run_local_server(
                port=0,
                access_type="offline",
                prompt="consent",
                success_message="Google Calendar подключён. Можно закрыть эту вкладку.",
            )
        self.oauth_token_file.parent.mkdir(parents=True, exist_ok=True)
        self.oauth_token_file.write_text(credentials.to_json(), encoding="utf-8")
        return credentials

    def _client(self):
        if not self.enabled:
            raise DomainError("Google Calendar не настроен. Заполни переменные GOOGLE_* в .env.")
        if self.auth_mode == "service_account":
            credentials = service_account.Credentials.from_service_account_file(
                str(self.service_account_file), scopes=SCOPES
            )
            if self.delegated_subject:
                credentials = credentials.with_subject(self.delegated_subject)
        elif self.auth_mode == "oauth":
            credentials = self._oauth_credentials()
        elif self.service_account_file:
            raise DomainError(f"Файл service account не найден: {self.service_account_file}")
        else:
            raise DomainError("Не настроены OAuth или service account для Calendar.")
        return build("calendar", "v3", credentials=credentials, cache_discovery=False)

    def _range_bounds(self, start_day: date, days: int) -> tuple[datetime, datetime]:
        start = datetime.combine(start_day, time.min, tzinfo=self.timezone)
        return start, start + timedelta(days=days)

    def _list_events_range(self, start_day: date, days: int) -> list[dict[str, Any]]:
        start, end = self._range_bounds(start_day, days)
        service = self._client()
        events: list[dict[str, Any]] = []
        page_token = None
        while True:
            response = service.events().list(
                calendarId=self.calendar_id,
                timeMin=start.isoformat(),
                timeMax=end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                showDeleted=False,
                pageToken=page_token,
            ).execute()
            events.extend(response.get("items", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                return events

    @staticmethod
    def _parse_datetime(payload: dict[str, str]) -> datetime | None:
        value = payload.get("dateTime")
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None

    @staticmethod
    def _parse_google_datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC).replace(tzinfo=None)

    def sync_range(self, start_day: date, days: int = 7) -> dict[str, Any]:
        days = max(1, min(days, 31))
        end_day = start_day + timedelta(days=days)
        events = self._list_events_range(start_day, days)
        with self.database.transaction() as session:
            students = session.scalars(
                select(Student).where(Student.active.is_(True)).options(selectinload(Student.aliases))
            ).all()
            known_events = {
                event.google_event_id: event
                for event in session.scalars(
                    select(CalendarEvent).where(
                        CalendarEvent.event_date >= start_day,
                        CalendarEvent.event_date < end_day,
                    )
                ).all()
            }
            known_lessons = {
                lesson.google_event_id: lesson
                for lesson in session.scalars(
                    select(Lesson).where(
                        Lesson.lesson_date >= start_day,
                        Lesson.lesson_date < end_day,
                    )
                ).all()
                if lesson.google_event_id
            }
            seen_events: set[str] = set()
            seen_lessons: set[str] = set()
            events_created = events_updated = lessons_created = lessons_updated = 0
            unmatched: list[str] = []

            for payload in events:
                event_id = payload.get("id")
                if not event_id:
                    continue
                title = payload.get("summary") or "Без названия"
                start_payload = payload.get("start", {})
                end_payload = payload.get("end", {})
                start = self._parse_datetime(start_payload)
                end = self._parse_datetime(end_payload)
                all_day = start is None
                if all_day:
                    date_value = start_payload.get("date")
                    if not date_value:
                        continue
                    event_date = date.fromisoformat(date_value)
                else:
                    event_date = start.astimezone(self.timezone).date()
                status = payload.get("status") or CalendarEventStatus.CONFIRMED
                starts_utc = start.astimezone(UTC).replace(tzinfo=None) if start else None
                ends_utc = end.astimezone(UTC).replace(tzinfo=None) if end else None

                stored = known_events.get(event_id)
                if stored:
                    events_updated += 1
                else:
                    stored = CalendarEvent(
                        google_event_id=event_id,
                        calendar_id=self.calendar_id or "primary",
                        title=title,
                        event_date=event_date,
                        all_day=all_day,
                    )
                    session.add(stored)
                    events_created += 1
                stored.title = title
                stored.description = payload.get("description")
                stored.location = payload.get("location")
                stored.starts_at = starts_utc
                stored.ends_at = ends_utc
                stored.event_date = event_date
                stored.all_day = all_day
                stored.status = status
                stored.html_link = payload.get("htmlLink")
                stored.google_updated_at = self._parse_google_datetime(payload.get("updated"))
                stored.synced_at = utc_now()
                seen_events.add(event_id)

                if all_day or status == CalendarEventStatus.CANCELLED:
                    continue
                student = match_student(
                    f"{title}\n{payload.get('description') or ''}", students
                )
                if not student:
                    unmatched.append(title)
                    continue
                lesson_end = starts_utc + timedelta(minutes=self.lesson_duration_minutes)
                lesson = known_lessons.get(event_id)
                if lesson:
                    lessons_updated += 1
                    if lesson.starts_at != starts_utc:
                        lesson.reminder_sent_at = None
                else:
                    lesson = Lesson(student_id=student.id, google_event_id=event_id)
                    session.add(lesson)
                    lessons_created += 1
                lesson.student_id = student.id
                lesson.event_title = title
                lesson.starts_at = starts_utc
                lesson.ends_at = lesson_end
                lesson.lesson_date = event_date
                lesson.status = LessonStatus.SCHEDULED
                lesson.calendar_updated_at = utc_now()
                seen_lessons.add(event_id)

            cancelled_events = 0
            for event_id, stored in known_events.items():
                if event_id not in seen_events and stored.status != CalendarEventStatus.CANCELLED:
                    stored.status = CalendarEventStatus.CANCELLED
                    stored.synced_at = utc_now()
                    cancelled_events += 1
            cancelled_lessons = 0
            for event_id, lesson in known_lessons.items():
                if event_id not in seen_lessons and lesson.status != LessonStatus.CANCELLED:
                    lesson.status = LessonStatus.CANCELLED
                    lesson.calendar_updated_at = utc_now()
                    cancelled_lessons += 1

            return {
                "ok": True,
                "from": start_day.isoformat(),
                "to": (end_day - timedelta(days=1)).isoformat(),
                "events_total": len(events),
                "events_created": events_created,
                "events_updated": events_updated,
                "events_cancelled": cancelled_events,
                "lessons_created": lessons_created,
                "lessons_updated": lessons_updated,
                "lessons_cancelled": cancelled_lessons,
                "unmatched_events": unmatched,
            }

    def sync_day(self, day: date) -> dict[str, Any]:
        result = self.sync_range(day, 1)
        return {
            **result,
            "date": day.isoformat(),
            "created": result["lessons_created"],
            "updated": result["lessons_updated"],
            "cancelled": result["lessons_cancelled"],
        }

    def sync_week(self, start_day: date | None = None) -> dict[str, Any]:
        return self.sync_range(start_day or datetime.now(self.timezone).date(), 7)

    def week_schedule(self, start_day: date | None = None) -> dict[str, Any]:
        start_day = start_day or datetime.now(self.timezone).date()
        end_day = start_day + timedelta(days=7)
        with self.database.session() as session:
            events = session.scalars(
                select(CalendarEvent).where(
                    CalendarEvent.event_date >= start_day,
                    CalendarEvent.event_date < end_day,
                    CalendarEvent.status != CalendarEventStatus.CANCELLED,
                ).order_by(CalendarEvent.event_date, CalendarEvent.starts_at, CalendarEvent.id)
            ).all()
            rows = []
            for event in events:
                rows.append({
                    "id": event.id,
                    "google_event_id": event.google_event_id,
                    "title": event.title,
                    "date": event.event_date.isoformat(),
                    "all_day": event.all_day,
                    "starts_at": (
                        event.starts_at.replace(tzinfo=UTC).astimezone(self.timezone).isoformat()
                        if event.starts_at else None
                    ),
                    "ends_at": (
                        event.ends_at.replace(tzinfo=UTC).astimezone(self.timezone).isoformat()
                        if event.ends_at else None
                    ),
                    "location": event.location,
                    "description": (event.description or "")[:1500] or None,
                    "html_link": event.html_link,
                    "status": event.status,
                })
            return {
                "from": start_day.isoformat(),
                "to": (end_day - timedelta(days=1)).isoformat(),
                "events": rows,
            }
