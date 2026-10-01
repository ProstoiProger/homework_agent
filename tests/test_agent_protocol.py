import unittest

from google.genai import types

from agent import TOOL_DECLARATIONS, TeacherAgent


class AgentProtocolTests(unittest.TestCase):
    def test_agent_has_no_approval_or_send_tools(self):
        names = {tool["name"] for tool in TOOL_DECLARATIONS}
        self.assertNotIn("approve_homework", names)
        self.assertNotIn("send_homework", names)
        self.assertIn("sync_week_calendar", names)
        self.assertIn("get_week_schedule", names)

    def test_function_results_use_gemini_compatible_user_role(self):
        part = types.Part.from_function_response(
            name="list_students",
            response={"ok": True, "result": []},
        )

        content = TeacherAgent._function_response_content([part])

        self.assertEqual(content.role, "user")
        self.assertEqual(content.parts[0].function_response.name, "list_students")


if __name__ == "__main__":
    unittest.main()
