from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from agent import TeacherAgent
from calendar_service import GoogleCalendarService
from config import Settings, load_settings
from db import init_database
from models import HomeworkStatus
from pdf_service import HomeworkPdfService
from rag_service import ResourceService, SUPPORTED_EXTENSIONS
from services import DomainError, HomeworkService


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


def _services(context: ContextTypes.DEFAULT_TYPE):
    return (
        context.application.bot_data["settings"],
        context.application.bot_data["homeworks"],
        context.application.bot_data["calendar"],
    )


def _is_teacher(update: Update, settings: Settings) -> bool:
    return bool(update.effective_user and update.effective_user.id == settings.teacher_telegram_id)


async def _teacher_only(update: Update, settings: Settings) -> bool:
    if _is_teacher(update, settings):
        return True
    if update.effective_message:
        await update.effective_message.reply_text(
            "Эта команда доступна только преподавателю. Ученики получают здесь домашние задания."
        )
    return False


def format_report(report: dict) -> str:
    if not report["lessons"]:
        return f"{report['date']}: синхронизированных уроков нет."
    lines = [f"Итог за {report['date']}:"]
    icons = {
        HomeworkStatus.GENERATED: "📄 PDF сгенерирован",
        "NOT_CREATED": "❌ ДЗ не создано",
    }
    for item in report["lessons"]:
        lines.append(f"{icons.get(item['delivery'], '📄 PDF сгенерирован')} — {item['student']}")
    return "\n".join(lines)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings, homeworks, _ = _services(context)
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return
    if user.id == settings.teacher_telegram_id:
        await message.reply_text(
            "Готов. Пиши обычным языком, например:\n"
            "• Добавь ученика Дамира, 10Б, псевдоним Дамир\n"
            "• Дай Дамиру задачи 10–20 по алгебре\n"
            "• Загрузи сюда PDF/DOCX/TXT, затем попроси сгенерировать ДЗ по книге\n"
            "• Покажи сгенерированные ДЗ\n"
            "• Покажи расписание на неделю"
        )
        return

    if context.args:
        try:
            student = homeworks.register_student(
                context.args[0], user.id, user.username
            )
            await message.reply_text(
                f"Готово, {student['name']}. Telegram подключён. Здесь будут приходить домашние задания."
            )
        except DomainError as exc:
            await message.reply_text(str(exc))
        return

    student = homeworks.student_for_telegram(user.id)
    if student:
        await message.reply_text(
            f"Ты уже подключён как {student['name']}. Здесь будут приходить домашние задания."
        )
    else:
        await message.reply_text(
            "Нужна персональная ссылка регистрации от преподавателя. Открой именно её и нажми Start."
        )


async def pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings, homeworks, _ = _services(context)
    if not await _teacher_only(update, settings):
        return
    rows = homeworks.list_pending_homeworks()
    text = "Сгенерированных ДЗ пока нет."
    if rows:
        text = "Сгенерированные ДЗ:\n" + "\n".join(
            f"#{row['id']} — {row['student']}: {row['text']}" for row in rows
        )
    await update.effective_message.reply_text(text)


async def sync_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings, _, calendar = _services(context)
    if not await _teacher_only(update, settings):
        return
    try:
        result = await asyncio.to_thread(calendar.sync_week)
        await schedule_lesson_end_reminders(context.application)
        await update.effective_message.reply_text(
            "Календарь на 7 дней синхронизирован: "
            f"событий {result['events_total']}, новых {result['events_created']}, "
            f"уроков распознано {result['lessons_created'] + result['lessons_updated']}."
        )
    except Exception as exc:
        logger.exception("Calendar sync failed")
        await update.effective_message.reply_text(f"Не удалось синхронизировать календарь: {exc}")


async def today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings, homeworks, _ = _services(context)
    if not await _teacher_only(update, settings):
        return
    report = homeworks.delivery_report(datetime.now(settings.timezone).date())
    await update.effective_message.reply_text(format_report(report))


async def week(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings, _, calendar = _services(context)
    if not await _teacher_only(update, settings):
        return
    try:
        await asyncio.to_thread(calendar.sync_week)
        await schedule_lesson_end_reminders(context.application)
        schedule = calendar.week_schedule()
        events = schedule["events"]
        if not events:
            await update.effective_message.reply_text("На ближайшие 7 дней событий нет.")
            return
        lines = [f"📅 Расписание {schedule['from']} — {schedule['to']}:"]
        current_date = None
        for event in events:
            if event["date"] != current_date:
                current_date = event["date"]
                lines.append(f"\n{current_date}")
            if event["all_day"]:
                time_label = "весь день"
            else:
                time_label = f"{event['starts_at'][11:16]}–{event['ends_at'][11:16]}"
            line = f"• {time_label} — {event['title']}"
            if event.get("location"):
                line += f" ({event['location']})"
            lines.append(line)
        text = "\n".join(lines)
        for start in range(0, len(text), 3900):
            await update.effective_message.reply_text(text[start:start + 3900])
    except Exception as exc:
        logger.exception("Week schedule failed")
        await update.effective_message.reply_text(f"Не удалось получить неделю: {exc}")


async def students(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings, homeworks, _ = _services(context)
    if not await _teacher_only(update, settings):
        return
    rows = homeworks.list_students()
    text = "Ученики не добавлены."
    if rows:
        text = "Ученики:\n" + "\n".join(
            f"#{row['id']} — {row['name']} ({row['class_name'] or 'без класса'}) — "
            f"{'Telegram подключён' if row['telegram_connected'] else 'нет Telegram'}"
            for row in rows
        )
    await update.effective_message.reply_text(text)


async def upload_resource(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings, _, _ = _services(context)
    message = update.effective_message
    if not message or not message.document:
        return
    if not await _teacher_only(update, settings):
        return

    document = message.document
    filename = document.file_name or "resource"
    extension = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in SUPPORTED_EXTENSIONS:
        await message.reply_text("Поддерживаются только PDF, DOCX, TXT и MD.")
        return
    if document.file_size and document.file_size > settings.resource_max_file_bytes:
        limit_mb = settings.resource_max_file_bytes // (1024 * 1024)
        await message.reply_text(f"Файл слишком большой. Лимит: {limit_mb} МБ.")
        return

    status_message = await message.reply_text("📚 Скачиваю и индексирую ресурс…")
    try:
        telegram_file = await document.get_file()
        data = bytes(await telegram_file.download_as_bytearray())
        if len(data) > settings.resource_max_file_bytes:
            raise DomainError("Файл превышает установленный лимит.")
        resources: ResourceService = context.application.bot_data["resources"]
        result = await asyncio.to_thread(
            resources.ingest_bytes,
            data,
            filename,
            document.mime_type,
            document.file_id,
        )
        if result["already_exists"]:
            text = f"ℹ️ Этот ресурс уже загружен: #{result['id']} — {result['filename']}"
        else:
            text = (
                f"✅ Ресурс #{result['id']} проиндексирован: {result['filename']}\n"
                f"Символов: {result['char_count']}, фрагментов: {result['chunk_count']}.\n\n"
                "Теперь напиши, например: «Сгенерируй Дамиру 8 задач по производным "
                "по ресурсу #1»."
            )
        await status_message.edit_text(text)
    except Exception as exc:
        logger.exception("Resource upload failed")
        await status_message.edit_text(f"Не удалось обработать ресурс: {exc}")


async def resources_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings, _, _ = _services(context)
    if not await _teacher_only(update, settings):
        return
    resources: ResourceService = context.application.bot_data["resources"]
    rows = resources.list_documents()
    if not rows:
        await update.effective_message.reply_text("Ресурсы ещё не загружены.")
        return
    await update.effective_message.reply_text(
        "Загруженные ресурсы:\n"
        + "\n".join(
            f"#{row['id']} — {row['filename']} ({row['chunk_count']} фрагм.)"
            for row in rows
        )
    )


async def resource_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings, _, _ = _services(context)
    if not await _teacher_only(update, settings):
        return
    if not context.args:
        await update.effective_message.reply_text("Использование: /resource_delete ID")
        return
    try:
        resource_id = int(context.args[0])
        resources: ResourceService = context.application.bot_data["resources"]
        deleted = await asyncio.to_thread(resources.delete_document, resource_id)
        await update.effective_message.reply_text(
            f"Ресурс удалён: #{deleted['id']} — {deleted['filename']}"
        )
    except Exception as exc:
        await update.effective_message.reply_text(f"Не удалось удалить ресурс: {exc}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings, _, _ = _services(context)
    message = update.effective_message
    if not message or not message.text:
        return
    if not await _teacher_only(update, settings):
        return
    await context.bot.send_chat_action(message.chat_id, ChatAction.TYPING)
    agent: TeacherAgent = context.application.bot_data["agent"]
    try:
        answer = await agent.respond(message.text, context.bot)
        await message.reply_text(answer)
    except Exception as exc:
        logger.exception("Natural-language command failed")
        await message.reply_text(f"Не удалось обработать команду: {exc}")


async def calendar_sync_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    settings, _, calendar = _services(context)
    if not calendar.enabled:
        return
    try:
        await asyncio.to_thread(calendar.sync_week)
        await schedule_lesson_end_reminders(context.application)
    except Exception:
        logger.exception("Scheduled calendar sync failed")


async def reminder_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    settings, homeworks, _ = _services(context)
    reminder = context.job.data
    await context.bot.send_message(
        chat_id=settings.teacher_telegram_id,
        text=(
            f"⏰ Через {settings.lesson_end_reminder_minutes} мин. закончится урок: "
            f"{reminder['title']} ({reminder['student']})."
        ),
    )
    homeworks.mark_reminder_sent(reminder["lesson_id"])


async def schedule_lesson_end_reminders(application: Application) -> None:
    settings: Settings = application.bot_data["settings"]
    homeworks: HomeworkService = application.bot_data["homeworks"]
    queue = application.job_queue
    if queue is None:
        return
    for job in queue.jobs(r"^lesson-end-"):
        job.schedule_removal()
    reminders = homeworks.lesson_end_reminders(
        datetime.now(settings.timezone), settings.lesson_end_reminder_minutes
    )
    for reminder in reminders:
        queue.run_once(
            reminder_job,
            when=reminder["run_at"],
            data=reminder,
            name=f"lesson-end-{reminder['lesson_id']}",
        )


async def evening_summary_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    settings, homeworks, _ = _services(context)
    report = homeworks.delivery_report(datetime.now(settings.timezone).date())
    await context.bot.send_message(
        chat_id=settings.teacher_telegram_id, text=format_report(report)
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error
    logger.error(
        "Unhandled Telegram update error",
        exc_info=(type(error), error, error.__traceback__) if error else None,
    )


async def post_init(application: Application) -> None:
    settings: Settings = application.bot_data["settings"]
    queue = application.job_queue
    if queue is None:
        raise RuntimeError('Install dependency "python-telegram-bot[job-queue]"')
    queue.run_once(calendar_sync_job, when=1, name="initial-calendar-sync")
    queue.run_repeating(
        calendar_sync_job,
        interval=settings.calendar_sync_interval_minutes * 60,
        first=settings.calendar_sync_interval_minutes * 60,
        name="calendar-sync",
    )
    queue.run_daily(
        evening_summary_job,
        time=settings.evening_summary_at,
        name="evening-summary",
    )
    await schedule_lesson_end_reminders(application)
    logger.info("Bot initialized as @%s", application.bot.username)


def build_application(settings: Settings) -> Application:
    database = init_database(settings.database_url)
    pdf_service = HomeworkPdfService(settings.homework_pdf_dir)
    homeworks = HomeworkService(
        database,
        settings.timezone,
        pdf_service=pdf_service,
        delivery_mode=settings.homework_delivery_mode,
    )
    resources = ResourceService(database, settings.resource_storage_dir)
    calendar = GoogleCalendarService(
        database,
        settings.timezone,
        settings.google_calendar_id,
        settings.google_service_account_file,
        settings.google_calendar_subject,
        settings.google_oauth_client_id,
        settings.google_oauth_client_secret,
        settings.google_oauth_token_file,
        lesson_duration_minutes=settings.lesson_duration_minutes,
    )
    agent = TeacherAgent(settings, homeworks, calendar, resources)

    application = (
        Application.builder().token(settings.telegram_bot_token).post_init(post_init).build()
    )
    application.bot_data.update(
        settings=settings,
        homeworks=homeworks,
        calendar=calendar,
        resources=resources,
        agent=agent,
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("pending", pending))
    application.add_handler(CommandHandler("homeworks", pending))
    application.add_handler(CommandHandler("sync", sync_calendar))
    application.add_handler(CommandHandler("week", week))
    application.add_handler(CommandHandler("today", today))
    application.add_handler(CommandHandler("students", students))
    application.add_handler(CommandHandler("resources", resources_list))
    application.add_handler(CommandHandler("resource_delete", resource_delete))
    application.add_handler(MessageHandler(filters.Document.ALL, upload_resource))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)
    return application


def main() -> None:
    settings = load_settings()
    application = build_application(settings)
    logger.info("Starting Telegram bot")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
