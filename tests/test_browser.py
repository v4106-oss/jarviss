import tempfile
import unittest
from pathlib import Path

from app.core.agent import ActionExecutor, PlannedAction
from app.core.safety import (
    ActionForbidden,
    AuditLog,
    KillSwitch,
    PathPolicy,
    PermissionGate,
    SafetyServices,
    UndoStore,
    classify_action,
)
from app.ui.browser_modal import BrowserModal


class NormalizeUrlTests(unittest.TestCase):
    def test_plain_text_becomes_search(self):
        target = BrowserModal.normalize_url("istanbul hava durumu")
        self.assertTrue(target.startswith("https://duckduckgo.com/?q="))
        self.assertIn("istanbul", target)

    def test_bare_domain_gets_https(self):
        self.assertEqual(BrowserModal.normalize_url("example.com"), "https://example.com")

    def test_http_url_is_kept(self):
        self.assertEqual(
            BrowserModal.normalize_url("http://example.com/a?b=1"),
            "http://example.com/a?b=1",
        )

    def test_empty_input_is_rejected(self):
        self.assertEqual(BrowserModal.normalize_url(""), "")


class BrowserOpenTests(unittest.TestCase):
    def test_browser_open_requires_http(self):
        with self.assertRaises(ActionForbidden):
            classify_action("browser_open", {"url": "file:///C:/Windows"}, None)
        self.assertEqual(
            classify_action("browser_open", {"url": "https://example.com"}, None).name,
            "CREATE",
        )

    def test_executor_routes_browser_open_to_listener(self):
        with tempfile.TemporaryDirectory() as root_dir, tempfile.TemporaryDirectory() as data_dir:
            root = Path(root_dir)
            services = SafetyServices(
                policy=PathPolicy([root], base=root),
                gate=PermissionGate(),
                audit=AuditLog(Path(data_dir) / "audit.jsonl"),
                undo=UndoStore(Path(data_dir) / "undo"),
                kill=KillSwitch(),
            )
            opened = []
            executor = ActionExecutor(services, browser_listener=opened.append)

            result = executor.execute(
                PlannedAction("browser_open", {"url": "https://example.com"}, "open")
            )
            rejected = executor.execute(
                PlannedAction("browser_open", {"url": "file:///C:/Windows"}, "bad")
            )

            self.assertIn("Jarvis tarayıcısında", result)
            self.assertEqual(opened, ["https://example.com"])
            self.assertTrue(rejected.startswith("❌"))


if __name__ == "__main__":
    unittest.main()
