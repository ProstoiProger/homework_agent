from datetime import datetime

from calendar_service import GoogleCalendarService
from config import load_settings
from db import init_database


def main() -> None:
    settings = load_settings(require_runtime=False)
    database = init_database(settings.database_url)
    calendar = GoogleCalendarService(
        database,
        settings.timezone,
        settings.google_calendar_id,
        settings.google_service_account_file,
        settings.google_calendar_subject,
        settings.google_oauth_client_id,
        settings.google_oauth_client_secret,
        settings.google_oauth_token_file,
        allow_interactive_oauth=True,
        lesson_duration_minutes=settings.lesson_duration_minutes,
    )
    if not calendar.enabled:
        raise RuntimeError(
            "Заполни OAuth client_id/client_secret или настройки service account в .env"
        )
    result = calendar.sync_week(datetime.now(settings.timezone).date())
    print("Google Calendar connected")
    print(
        f"events={result['events_total']} new={result['events_created']} "
        f"lessons={result['lessons_created'] + result['lessons_updated']} "
        f"unmatched={len(result['unmatched_events'])}"
    )
    if result["unmatched_events"]:
        print("Unmatched events:")
        for title in result["unmatched_events"]:
            print(f"- {title}")


if __name__ == "__main__":
    main()
