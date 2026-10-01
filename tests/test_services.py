import asyncio
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from db import init_database
from models import Homework, HomeworkStatus
from pdf_service import HomeworkPdfService
from services import DomainError, HomeworkService


class FakeBot:
    def __init__(self):
        self.messages = []
        self.documents = []

    async def send_message(self, chat_id, text):
        self.messages.append((chat_id, text))

    async def send_document(self, chat_id, document, filename, caption):
        self.documents.append((chat_id, filename, caption, document.read()))


class HomeworkServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        path = Path(self.tempdir.name) / "test.db"
        self.database = init_database(f"sqlite:///{path.as_posix()}")
        self.service = HomeworkService(self.database, ZoneInfo("UTC"))
        student = self.service.add_student("Дамир", "10Б", ["Дамир"])
        self.student_id = student["id"]
        self.service.register_student(student["registration_code"], 123456, "damir")

    def tearDown(self):
        self.database.engine.dispose()
        self.tempdir.cleanup()

    def test_new_homework_waits_for_approval(self):
        homework = self.service.create_homework(self.student_id, "Задачи 1–5")
        self.assertEqual(homework["status"], HomeworkStatus.GENERATED)

    def test_send_is_impossible_without_approved_status(self):
        homework = self.service.create_homework(self.student_id, "Задачи 1–5")
        bot = FakeBot()
        with self.assertRaises(DomainError):
            asyncio.run(self.service.send_homework(homework["homework_id"], bot))
        self.assertEqual(bot.messages, [])

    def test_approve_and_send_are_disabled(self):
        homework = self.service.create_homework(self.student_id, "Задачи 1–5")
        bot = FakeBot()
        with self.assertRaises(DomainError):
            self.service.approve_homework(homework["homework_id"])
        with self.assertRaises(DomainError):
            asyncio.run(self.service.send_homework(homework["homework_id"], bot))
        self.assertEqual(bot.messages, [])
        self.assertEqual(bot.documents, [])

    def test_pdf_mode_generates_document_but_never_sends_it(self):
        pdf_service = HomeworkPdfService(Path(self.tempdir.name) / "pdf")
        service = HomeworkService(
            self.database, ZoneInfo("UTC"), pdf_service=pdf_service, delivery_mode="pdf"
        )
        homework = service.create_homework(
            self.student_id, r"Вычислите: \(\frac{3}{4}+\frac{5}{6}\)."
        )
        pdf_path = service.homework_pdf(homework["homework_id"])
        bot = FakeBot()

        with self.assertRaises(DomainError):
            asyncio.run(service.send_homework(homework["homework_id"], bot))

        self.assertTrue(pdf_path.exists())
        self.assertTrue(pdf_path.read_bytes().startswith(b"%PDF"))
        self.assertEqual(bot.messages, [])
        self.assertEqual(bot.documents, [])


if __name__ == "__main__":
    unittest.main()
