from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from models import Base


class Database:
    def __init__(self, database_url: str):
        connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        self.engine = create_engine(database_url, connect_args=connect_args)
        if database_url.startswith("sqlite"):
            event.listen(self.engine, "connect", self._enable_sqlite_foreign_keys)
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False)

    @staticmethod
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    def initialize(self) -> None:
        Base.metadata.create_all(self.engine)
        if self.engine.dialect.name == "sqlite":
            self._migrate_legacy_sqlite()

    def _migrate_legacy_sqlite(self) -> None:
        migrations = {
            "students": {
                "telegram_username": "VARCHAR(100)",
                "registration_code": "VARCHAR(64)",
                "created_at": "DATETIME",
            },
            "homeworks": {"lesson_id": "INTEGER REFERENCES lessons(id)"},
            "lessons": {
                "event_title": "VARCHAR(500)",
                "reminder_sent_at": "DATETIME",
                "calendar_updated_at": "DATETIME",
            },
        }
        inspector = inspect(self.engine)
        with self.engine.begin() as connection:
            for table_name, columns in migrations.items():
                existing = {column["name"] for column in inspector.get_columns(table_name)}
                for column_name, sql_type in columns.items():
                    if column_name not in existing:
                        connection.execute(
                            text(f'ALTER TABLE "{table_name}" ADD COLUMN "{column_name}" {sql_type}')
                        )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_students_registration_code "
                    "ON students(registration_code)"
                )
            )
            connection.execute(
                text("CREATE INDEX IF NOT EXISTS ix_students_telegram_id ON students(telegram_id)")
            )
            connection.execute(
                text("CREATE INDEX IF NOT EXISTS ix_homeworks_lesson_id ON homeworks(lesson_id)")
            )
            connection.execute(
                text(
                    "UPDATE homeworks SET status = 'GENERATED' "
                    "WHERE status IN ('WAITING_APPROVAL', 'APPROVED', 'SENDING')"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_calendar_events_event_date "
                    "ON calendar_events(event_date)"
                )
            )

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self.session_factory() as session:
            yield session

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        with self.session_factory.begin() as session:
            yield session


def init_database(database_url: str) -> Database:
    database = Database(database_url)
    database.initialize()
    return database
