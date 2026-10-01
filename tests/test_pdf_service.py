import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader

from homework_agent.pdf_service import HomeworkPdfService


class HomeworkPdfServiceTests(unittest.TestCase):
    def test_renders_cyrillic_and_latex_without_raw_commands(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "homework.pdf"
            service = HomeworkPdfService(Path(temp_dir) / "generated")

            service.render(
                7,
                "Дамир",
                r"1. Вычислите: \(\frac{3}{4}+\frac{5}{6}\).",
                "завтра",
                output,
            )

            reader = PdfReader(output)
            extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
            self.assertEqual(len(reader.pages), 1)
            self.assertIn("Домашнее задание", extracted)
            self.assertIn("Дамир", extracted)
            self.assertNotIn(r"\frac", extracted)
            self.assertGreater(output.stat().st_size, 10_000)


if __name__ == "__main__":
    unittest.main()
