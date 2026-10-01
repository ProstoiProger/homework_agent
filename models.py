from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class HomeworkStatus:
    GENERATED = "GENERATED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    APPROVED = "APPROVED"
    SENDING = "SENDING"
    SENT = "SENT"


class LessonStatus:
    SCHEDULED = "SCHEDULED"
    CANCELLED = "CANCELLED"


class CalendarEventStatus:
    CONFIRMED = "confirmed"
    TENTATIVE = "tentative"
    CANCELLED = "cancelled"


class Student(Base):
    __tablename__ = "students"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    telegram_username: Mapped[str | None] = mapped_column(String(100))
    registration_code: Mapped[str | None] = mapped_column(String(64), unique=True)
    class_name: Mapped[str | None] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )

    aliases: Mapped[list[StudentAlias]] = relationship(
        back_populates="student", cascade="all, delete-orphan"
    )
    homeworks: Mapped[list[Homework]] = relationship(back_populates="student")
    lessons: Mapped[list[Lesson]] = relationship(back_populates="student")


class StudentAlias(Base):
    __tablename__ = "student_aliases"

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), nullable=False)
    alias: Mapped[str] = mapped_column(String(200), nullable=False)

    student: Mapped[Student] = relationship(back_populates="aliases")


class Homework(Base):
    __tablename__ = "homeworks"

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), nullable=False)
    lesson_id: Mapped[int | None] = mapped_column(ForeignKey("lessons.id"))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    deadline: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=HomeworkStatus.GENERATED
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)

    student: Mapped[Student] = relationship(back_populates="homeworks")
    lesson: Mapped[Lesson | None] = relationship(back_populates="homeworks")


class Lesson(Base):
    __tablename__ = "lessons"

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), nullable=False)
    google_event_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    event_title: Mapped[str | None] = mapped_column(String(500))
    starts_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime)
    lesson_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=LessonStatus.SCHEDULED
    )
    reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    calendar_updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )

    student: Mapped[Student] = relationship(back_populates="lessons")
    homeworks: Mapped[list[Homework]] = relationship(back_populates="lesson")


class CalendarEvent(Base):
    __tablename__ = "calendar_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    google_event_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    calendar_id: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(500))
    starts_at: Mapped[datetime | None] = mapped_column(DateTime)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    all_day: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=CalendarEventStatus.CONFIRMED
    )
    html_link: Mapped[str | None] = mapped_column(String(1000))
    google_updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime)


class ResourceDocument(Base):
    __tablename__ = "resource_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(200))
    telegram_file_id: Mapped[str | None] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    storage_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    char_count: Mapped[int] = mapped_column(nullable=False)
    chunk_count: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )

    chunks: Mapped[list[ResourceChunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class ResourceChunk(Base):
    __tablename__ = "resource_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("resource_documents.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(nullable=False)
    location: Mapped[str | None] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text, nullable=False)

    document: Mapped[ResourceDocument] = relationship(back_populates="chunks")
