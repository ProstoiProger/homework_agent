from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


PROJECT_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    teacher_telegram_id: int
    gemini_api_key: str
    gemini_model: str
    openrouter_api_key: str | None
    openrouter_model: str
    database_url: str
    resource_storage_dir: Path
    resource_max_file_bytes: int
    homework_delivery_mode: str
    homework_pdf_dir: Path
    timezone: ZoneInfo
    google_calendar_id: str | None
    google_service_account_file: Path | None
    google_calendar_subject: str | None
    google_oauth_client_id: str | None
    google_oauth_client_secret: str | None
    google_oauth_token_file: Path
    calendar_sync_interval_minutes: int
    lesson_duration_minutes: int
    lesson_end_reminder_minutes: int
    evening_summary_at: time

    @property
    def llm_provider(self) -> str:
        return "openrouter" if self.openrouter_api_key else "gemini"

    @property
    def calendar_enabled(self) -> bool:
        has_service_account = bool(self.google_service_account_file)
        has_oauth = bool(self.google_oauth_client_id and self.google_oauth_client_secret)
        return bool(self.google_calendar_id and (has_service_account or has_oauth))


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is missing")
    return value


def _parse_time(value: str, timezone: ZoneInfo) -> time:
    try:
        hour, minute = (int(part) for part in value.split(":", 1))
        return time(hour=hour, minute=minute, tzinfo=timezone)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("EVENING_SUMMARY_AT must use HH:MM format") from exc


def load_settings(*, require_runtime: bool = True) -> Settings:
    load_dotenv(PROJECT_DIR / ".env")

    timezone = ZoneInfo(os.getenv("TIMEZONE", "Asia/Qyzylorda"))
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        database_url = f"sqlite:///{(PROJECT_DIR / 'homework.db').as_posix()}"

    resource_storage_value = os.getenv("RESOURCE_STORAGE_DIR", "resources").strip()
    resource_storage_dir = Path(resource_storage_value)
    if not resource_storage_dir.is_absolute():
        resource_storage_dir = PROJECT_DIR / resource_storage_dir
    try:
        resource_max_file_bytes = int(os.getenv("RESOURCE_MAX_FILE_MB", "20")) * 1024 * 1024
    except ValueError as exc:
        raise RuntimeError("RESOURCE_MAX_FILE_MB must be an integer") from exc
    if resource_max_file_bytes <= 0:
        raise RuntimeError("RESOURCE_MAX_FILE_MB must be positive")

    homework_delivery_mode = os.getenv("HOMEWORK_DELIVERY_MODE", "pdf").strip().lower()
    if homework_delivery_mode not in {"pdf", "text"}:
        raise RuntimeError("HOMEWORK_DELIVERY_MODE must be pdf or text")
    homework_pdf_value = os.getenv("HOMEWORK_PDF_DIR", "generated_homeworks").strip()
    homework_pdf_dir = Path(homework_pdf_value)
    if not homework_pdf_dir.is_absolute():
        homework_pdf_dir = PROJECT_DIR / homework_pdf_dir

    service_account_value = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    service_account_file = None
    if service_account_value:
        service_account_file = Path(service_account_value)
        if not service_account_file.is_absolute():
            service_account_file = PROJECT_DIR / service_account_file

    oauth_client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip() or None
    oauth_client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip() or None
    oauth_token_value = os.getenv(
        "GOOGLE_OAUTH_TOKEN_FILE", "credentials/google-calendar-token.json"
    ).strip()
    oauth_token_file = Path(oauth_token_value)
    if not oauth_token_file.is_absolute():
        oauth_token_file = PROJECT_DIR / oauth_token_file

    calendar_id = os.getenv("GOOGLE_CALENDAR_ID", "").strip() or None
    if not calendar_id and oauth_client_id and oauth_client_secret:
        calendar_id = "primary"

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    teacher_value = os.getenv("TEACHER_TELEGRAM_ID", "0").strip()
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip() or None
    if require_runtime:
        token = _required("TELEGRAM_BOT_TOKEN")
        teacher_value = _required("TEACHER_TELEGRAM_ID")
        if not openrouter_key:
            gemini_key = _required("GEMINI_API_KEY")

    try:
        teacher_id = int(teacher_value or "0")
    except ValueError as exc:
        raise RuntimeError("TEACHER_TELEGRAM_ID must be an integer") from exc
    if require_runtime and teacher_id <= 0:
        raise RuntimeError("TEACHER_TELEGRAM_ID must be a positive integer")

    return Settings(
        telegram_bot_token=token,
        teacher_telegram_id=teacher_id,
        gemini_api_key=gemini_key,
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip(),
        openrouter_api_key=openrouter_key,
        openrouter_model=os.getenv("OPENROUTER_MODEL", "openrouter/auto").strip(),
        database_url=database_url,
        resource_storage_dir=resource_storage_dir,
        resource_max_file_bytes=resource_max_file_bytes,
        homework_delivery_mode=homework_delivery_mode,
        homework_pdf_dir=homework_pdf_dir,
        timezone=timezone,
        google_calendar_id=calendar_id,
        google_service_account_file=service_account_file,
        google_calendar_subject=os.getenv("GOOGLE_CALENDAR_SUBJECT", "").strip() or None,
        google_oauth_client_id=oauth_client_id,
        google_oauth_client_secret=oauth_client_secret,
        google_oauth_token_file=oauth_token_file,
        calendar_sync_interval_minutes=int(os.getenv("CALENDAR_SYNC_INTERVAL_MINUTES", "30")),
        lesson_duration_minutes=int(os.getenv("LESSON_DURATION_MINUTES", "45")),
        lesson_end_reminder_minutes=int(os.getenv("LESSON_END_REMINDER_MINUTES", "2")),
        evening_summary_at=_parse_time(os.getenv("EVENING_SUMMARY_AT", "21:00"), timezone),
    )
