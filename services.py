from __future__ import annotations

import secrets
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from db import Database
from models import Homework, HomeworkStatus, Lesson, LessonStatus, Student, StudentAlias
from pdf_service import HomeworkPdfService


class DomainError(Exception):
    pass


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class HomeworkService:
    def __init__(
        self,
        database: Database,
        timezone: ZoneInfo,
        pdf_service: HomeworkPdfService | None = None,
        delivery_mode: str = "text",
    ):
        self.database = database
        self.timezone = timezone
        self.pdf_service = pdf_service
        self.delivery_mode = delivery_mode

    @staticmethod
    def _student_dict(student: Student) -> dict[str, Any]:
        return {
            "id": student.id,
            "name": student.name,
            "class_name": student.class_name,
            "telegram_connected": student.telegram_id is not None,
            "aliases": [alias.alias for alias in student.aliases],
            "registration_code": student.registration_code,
        }

    def add_student(
        self, name: str, class_name: str | None = None, aliases: list[str] | None = None
    ) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise DomainError("Имя ученика не может быть пустым.")
        code = secrets.token_urlsafe(12)
        cleaned_aliases = sorted({value.strip() for value in aliases or [] if value.strip()})
        with self.database.transaction() as session:
            student = Student(
                name=name,
                class_name=class_name.strip() if class_name else None,
                registration_code=code,
            )
            student.aliases = [StudentAlias(alias=value) for value in cleaned_aliases]
            session.add(student)
            session.flush()
            return self._student_dict(student)

    def list_students(self) -> list[dict[str, Any]]:
        with self.database.session() as session:
            students = session.scalars(
                select(Student)
                .where(Student.active.is_(True))
                .options(selectinload(Student.aliases))
                .order_by(Student.name)
            ).all()
            return [self._student_dict(student) for student in students]

    def find_students(self, name: str, class_name: str | None = None) -> list[dict[str, Any]]:
        needle = name.strip().casefold()
        if not needle:
            return []
        matches = []
        for student in self.list_students():
            names = [student["name"], *student["aliases"]]
            if any(needle in value.casefold() for value in names):
                if class_name and (student["class_name"] or "").casefold() != class_name.casefold():
                    continue
                matches.append(student)
        return matches

    def ensure_registration_code(self, student_id: int) -> dict[str, Any]:
        with self.database.transaction() as session:
            student = session.scalar(
                select(Student)
                .where(Student.id == student_id, Student.active.is_(True))
                .options(selectinload(Student.aliases))
            )
            if not student:
                raise DomainError("Ученик не найден.")
            if not student.registration_code:
                student.registration_code = secrets.token_urlsafe(12)
            session.flush()
            return self._student_dict(student)

    def register_student(
        self, registration_code: str, telegram_id: int, telegram_username: str | None
    ) -> dict[str, Any]:
        with self.database.transaction() as session:
            student = session.scalar(
                select(Student)
                .where(
                    Student.registration_code == registration_code,
                    Student.active.is_(True),
                )
                .options(selectinload(Student.aliases))
            )
            if not student:
                raise DomainError("Код регистрации не найден или больше не действует.")
            if student.telegram_id and student.telegram_id != telegram_id:
                raise DomainError("Этот код уже привязан к другому Telegram-аккаунту.")
            other = session.scalar(
                select(Student).where(
                    Student.telegram_id == telegram_id,
                    Student.id != student.id,
                )
            )
            if other:
                raise DomainError("Этот Telegram-аккаунт уже привязан к другому ученику.")
            student.telegram_id = telegram_id
            student.telegram_username = telegram_username
            session.flush()
            return self._student_dict(student)

    def student_for_telegram(self, telegram_id: int) -> dict[str, Any] | None:
        with self.database.session() as session:
            student = session.scalar(
                select(Student)
                .where(Student.telegram_id == telegram_id, Student.active.is_(True))
                .options(selectinload(Student.aliases))
            )
            return self._student_dict(student) if student else None

    def _best_lesson_for_homework(self, session, student_id: int) -> Lesson | None:
        local_now = datetime.now(self.timezone)
        lessons = session.scalars(
            select(Lesson).where(
                Lesson.student_id == student_id,
                Lesson.lesson_date == local_now.date(),
                Lesson.status == LessonStatus.SCHEDULED,
            )
        ).all()
        if not lessons:
            return None
        now_naive = local_now.astimezone(UTC).replace(tzinfo=None)
        return min(lessons, key=lambda lesson: abs((lesson.starts_at - now_naive).total_seconds()))

    def create_homework(
        self, student_id: int, text: str, deadline: str | None = None
    ) -> dict[str, Any]:
        homework_text = text.strip()
        if not homework_text:
            raise DomainError("Текст ДЗ не может быть пустым.")
        with self.database.transaction() as session:
            student = session.scalar(
                select(Student).where(Student.id == student_id, Student.active.is_(True))
            )
            if not student:
                raise DomainError("Ученик не найден.")
            lesson = self._best_lesson_for_homework(session, student_id)
            homework = Homework(
                student_id=student_id,
                lesson_id=lesson.id if lesson else None,
                text=homework_text,
                deadline=deadline.strip() if deadline else None,
                status=HomeworkStatus.GENERATED,
            )
            session.add(homework)
            session.flush()
            return {
                "ok": True,
                "homework_id": homework.id,
                "student": student.name,
                "text": homework.text,
                "deadline": homework.deadline,
                "lesson_id": homework.lesson_id,
                "status": homework.status,
            }

    def list_pending_homeworks(self) -> list[dict[str, Any]]:
        with self.database.session() as session:
            rows = session.execute(
                select(Homework, Student)
                .join(Student, Student.id == Homework.student_id)
                .where(Homework.status.in_([HomeworkStatus.GENERATED, HomeworkStatus.WAITING_APPROVAL]))
                .order_by(Homework.created_at.desc(), Homework.id.desc())
            ).all()
            return [
                {
                    "id": homework.id,
                    "student": student.name,
                    "text": homework.text,
                    "deadline": homework.deadline,
                    "status": homework.status,
                }
                for homework, student in rows
            ]

    def homework_pdf(self, homework_id: int):
        if self.delivery_mode != "pdf" or not self.pdf_service:
            return None
        with self.database.session() as session:
            row = session.execute(
                select(Homework, Student)
                .join(Student, Student.id == Homework.student_id)
                .where(Homework.id == homework_id)
            ).one_or_none()
            if not row:
                raise DomainError("ДЗ не найдено.")
            homework, student = row
            return self.pdf_service.render(
                homework.id,
                student.name,
                homework.text,
                homework.deadline,
            )

    def approve_homework(self, homework_id: int) -> dict[str, Any]:
        raise DomainError(
            "Approve отключён: бот только генерирует PDF для преподавателя и никому его не отправляет."
        )

    def _reserve_homework_for_send(self, homework_id: int) -> dict[str, Any]:
        with self.database.transaction() as session:
            homework = session.get(Homework, homework_id)
            if not homework:
                raise DomainError("ДЗ не найдено.")
            student = session.get(Student, homework.student_id)
            if not student or not student.active:
                raise DomainError("Ученик не найден или отключён.")
            if homework.status != HomeworkStatus.APPROVED:
                raise DomainError(
                    "Отправка запрещена: сервер принимает только ДЗ со статусом APPROVED "
                    f"(сейчас {homework.status})."
                )
            if not student.telegram_id:
                raise DomainError("У ученика не подключён Telegram.")

            result = session.execute(
                update(Homework)
                .where(
                    Homework.id == homework_id,
                    Homework.status == HomeworkStatus.APPROVED,
                )
                .values(status=HomeworkStatus.SENDING)
            )
            if result.rowcount != 1:
                raise DomainError("ДЗ уже отправляется или его статус изменился.")
            return {
                "homework_id": homework.id,
                "student": student.name,
                "telegram_id": student.telegram_id,
                "text": homework.text,
                "deadline": homework.deadline,
            }

    def _release_send_reservation(self, homework_id: int) -> None:
        with self.database.transaction() as session:
            session.execute(
                update(Homework)
                .where(
                    Homework.id == homework_id,
                    Homework.status == HomeworkStatus.SENDING,
                )
                .values(status=HomeworkStatus.APPROVED)
            )

    def _mark_sent(self, homework_id: int) -> None:
        with self.database.transaction() as session:
            result = session.execute(
                update(Homework)
                .where(
                    Homework.id == homework_id,
                    Homework.status == HomeworkStatus.SENDING,
                )
                .values(status=HomeworkStatus.SENT, sent_at=utc_now())
            )
            if result.rowcount != 1:
                raise DomainError("Сообщение ушло, но не удалось зафиксировать статус SENT.")

    async def send_homework(self, homework_id: int, bot) -> dict[str, Any]:
        raise DomainError(
            "Автоотправка отключена: преподаватель пересылает готовый PDF вручную."
        )

    def lesson_end_reminders(
        self, now: datetime, minutes_before_end: int, horizon_days: int = 7
    ) -> list[dict[str, Any]]:
        now_utc = now.astimezone(UTC).replace(tzinfo=None)
        horizon = now_utc + timedelta(days=horizon_days)
        with self.database.session() as session:
            lessons = session.scalars(
                select(Lesson).where(
                    Lesson.status == LessonStatus.SCHEDULED,
                    Lesson.reminder_sent_at.is_(None),
                    Lesson.ends_at.is_not(None),
                    Lesson.ends_at > now_utc,
                    Lesson.ends_at <= horizon,
                ).options(selectinload(Lesson.student)).order_by(Lesson.ends_at)
            ).all()
            result = []
            for lesson in lessons:
                run_at_utc = lesson.ends_at - timedelta(minutes=minutes_before_end)
                if run_at_utc < now_utc:
                    run_at_utc = now_utc
                result.append({
                    "lesson_id": lesson.id,
                    "student": lesson.student.name,
                    "title": lesson.event_title or f"Урок с {lesson.student.name}",
                    "starts_at": lesson.starts_at.replace(tzinfo=UTC).astimezone(self.timezone),
                    "ends_at": lesson.ends_at.replace(tzinfo=UTC).astimezone(self.timezone),
                    "run_at": run_at_utc.replace(tzinfo=UTC).astimezone(self.timezone),
                })
            return result

    def _day_utc_bounds(self, day: date) -> tuple[datetime, datetime]:
        local_start = datetime.combine(day, time.min, tzinfo=self.timezone)
        local_end = local_start + timedelta(days=1)
        return (
            local_start.astimezone(UTC).replace(tzinfo=None),
            local_end.astimezone(UTC).replace(tzinfo=None),
        )

    def delivery_report(self, day: date) -> dict[str, Any]:
        day_start, day_end = self._day_utc_bounds(day)
        with self.database.session() as session:
            lessons = session.scalars(
                select(Lesson)
                .where(
                    Lesson.lesson_date == day,
                    Lesson.status == LessonStatus.SCHEDULED,
                )
                .options(selectinload(Lesson.student), selectinload(Lesson.homeworks))
                .order_by(Lesson.starts_at)
            ).all()
            result = []
            for lesson in lessons:
                homeworks = list(lesson.homeworks)
                if not homeworks:
                    homeworks = session.scalars(
                        select(Homework).where(
                            Homework.student_id == lesson.student_id,
                            Homework.lesson_id.is_(None),
                            Homework.created_at >= day_start,
                            Homework.created_at < day_end,
                        )
                    ).all()
                statuses = {homework.status for homework in homeworks}
                if HomeworkStatus.SENT in statuses:
                    delivery = HomeworkStatus.SENT
                elif HomeworkStatus.APPROVED in statuses or HomeworkStatus.SENDING in statuses:
                    delivery = HomeworkStatus.APPROVED
                elif HomeworkStatus.GENERATED in statuses or HomeworkStatus.WAITING_APPROVAL in statuses:
                    delivery = HomeworkStatus.GENERATED
                else:
                    delivery = "NOT_CREATED"
                result.append(
                    {
                        "lesson_id": lesson.id,
                        "student_id": lesson.student_id,
                        "student": lesson.student.name,
                        "starts_at": lesson.starts_at.isoformat(),
                        "delivery": delivery,
                    }
                )
            return {
                "date": day.isoformat(),
                "lessons": result,
                "sent": [item["student"] for item in result if item["delivery"] == HomeworkStatus.SENT],
                "not_sent": [item["student"] for item in result if item["delivery"] != HomeworkStatus.SENT],
            }

    def due_reminders(self, now: datetime, delay_minutes: int) -> list[dict[str, Any]]:
        now_utc = now.astimezone(UTC).replace(tzinfo=None)
        cutoff = now_utc - timedelta(minutes=delay_minutes)
        today = now.astimezone(self.timezone).date()
        report_by_lesson = {
            item["lesson_id"]: item for item in self.delivery_report(today)["lessons"]
        }
        with self.database.session() as session:
            lessons = session.scalars(
                select(Lesson)
                .where(
                    Lesson.lesson_date == today,
                    Lesson.status == LessonStatus.SCHEDULED,
                    Lesson.reminder_sent_at.is_(None),
                    Lesson.ends_at.is_not(None),
                    Lesson.ends_at <= cutoff,
                )
                .options(selectinload(Lesson.student))
                .order_by(Lesson.ends_at)
            ).all()
            return [
                {
                    "lesson_id": lesson.id,
                    "student": lesson.student.name,
                    "ended_at": lesson.ends_at.isoformat() if lesson.ends_at else None,
                    "delivery": report_by_lesson.get(lesson.id, {}).get("delivery", "NOT_CREATED"),
                }
                for lesson in lessons
                if report_by_lesson.get(lesson.id, {}).get("delivery") != HomeworkStatus.SENT
            ]

    def mark_reminder_sent(self, lesson_id: int) -> None:
        with self.database.transaction() as session:
            lesson = session.get(Lesson, lesson_id)
            if lesson and lesson.reminder_sent_at is None:
                lesson.reminder_sent_at = utc_now()
