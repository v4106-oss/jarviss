import json
import os
import unittest
from unittest.mock import patch

from app.core.safety import ActionForbidden
from app.core.window_tools import WindowTools


class FakeRect:
    def __init__(self, left, top, right, bottom):
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom


class FakeWindow:
    def __init__(self, title, handle, pid=4321, rect=(100, 100, 500, 400)):
        self._title = title
        self.handle = handle
        self._pid = pid
        self._rect = list(rect)
        self.minimized = False
        self.maximized = False
        self.focused = 0
        self.closed = 0
        self.moves = []
        self._exists = True

    def window_text(self):
        return self._title

    def process_id(self):
        return self._pid

    def rectangle(self):
        return FakeRect(*self._rect)

    def is_minimized(self):
        return self.minimized

    def is_maximized(self):
        return self.maximized

    def minimize(self):
        self.minimized = True

    def maximize(self):
        self.maximized = True

    def restore(self):
        self.minimized = False
        self.maximized = False

    def move_window(self, *args, **kwargs):
        x = kwargs.get("x", args[0] if len(args) > 0 else self._rect[0])
        y = kwargs.get("y", args[1] if len(args) > 1 else self._rect[1])
        width = kwargs.get("width", args[2] if len(args) > 2 else self._rect[2] - self._rect[0])
        height = kwargs.get("height", args[3] if len(args) > 3 else self._rect[3] - self._rect[1])
        self._rect = [int(x), int(y), int(x) + int(width), int(y) + int(height)]
        self.moves.append(tuple(self._rect))

    def close(self):
        self.closed += 1
        self._exists = False

    def exists(self):
        return self._exists


class FakeDesktop:
    def __init__(self, windows):
        self._windows = windows

    def windows(self):
        return self._windows


class FakeUI:
    def __init__(self, windows, fg):
        self.windows = windows
        self.fg = fg

    def _window(self, title):
        for window in self.windows:
            if title.casefold() in window.window_text().casefold():
                return window
        raise LookupError("pencere yok")

    def focus(self, window):
        window.focused += 1
        self.fg["handle"] = window.handle
        return True


class WindowToolsTests(unittest.TestCase):
    def setUp(self):
        self.notepad = FakeWindow("Not Defteri", 101, pid=1111, rect=(100, 100, 700, 500))
        self.browser = FakeWindow("Tarayıcı", 202, pid=2222, rect=(50, 60, 1050, 760))
        self.fg = {"handle": 202}
        self.ui = FakeUI([self.notepad, self.browser], self.fg)
        self.tools = WindowTools(
            self.ui,
            desktop_factory=lambda: FakeDesktop([self.notepad, self.browser, FakeWindow("", 303)]),
        )
        sleep_patch = patch("app.core.window_tools.time.sleep", lambda *_: None)
        sleep_patch.start()
        self.addCleanup(sleep_patch.stop)
        fg_patch = patch.object(
            WindowTools, "_foreground_handle", staticmethod(lambda: self.fg["handle"])
        )
        fg_patch.start()
        self.addCleanup(fg_patch.stop)
        process_patch = patch.object(
            WindowTools, "_process_name", staticmethod(lambda window: "notepad.exe")
        )
        process_patch.start()
        self.addCleanup(process_patch.stop)

    def run_action(self, **args):
        return json.loads(self.tools.run(args))

    def test_list_reports_foreground_first_and_skips_blank_titles(self):
        payload = self.run_action(action="list")

        self.assertEqual(payload["count"], 2)
        self.assertEqual([row["title"] for row in payload["windows"]], ["Tarayıcı", "Not Defteri"])
        self.assertTrue(payload["windows"][0]["foreground"])
        self.assertFalse(payload["windows"][1]["foreground"])

    def test_list_filter_and_limit(self):
        payload = self.run_action(action="list", filter="defter", limit=5)
        self.assertEqual([row["title"] for row in payload["windows"]], ["Not Defteri"])

        payload = self.run_action(action="list", limit=1)
        self.assertEqual(payload["count"], 1)

    def test_list_alias_is_accepted(self):
        payload = self.run_action(action="list_windows")
        self.assertEqual(payload["count"], 2)

    def test_focus_verifies_foreground(self):
        payload = self.run_action(action="focus", title="Not Defteri")

        self.assertEqual(payload["mechanism"], "foreground_verified")
        self.assertTrue(payload["verified"])
        self.assertEqual(self.notepad.focused, 1)
        self.assertTrue(payload["result"]["foreground"])

    def test_handle_can_target_a_window(self):
        payload = self.run_action(action="focus", handle=101)
        self.assertEqual(payload["window"]["handle"], 101)
        self.assertTrue(payload["verified"])

    def test_minimize_maximize_and_restore_are_verified(self):
        payload = self.run_action(action="minimize", title="Not Defteri")
        self.assertTrue(payload["result"]["minimized"])
        self.assertTrue(payload["verified"])

        payload = self.run_action(action="maximize", title="Not Defteri")
        self.assertTrue(payload["result"]["maximized"])
        self.assertTrue(payload["verified"])

        payload = self.run_action(action="restore", title="Not Defteri")
        self.assertFalse(payload["result"]["minimized"])
        self.assertFalse(payload["result"]["maximized"])
        self.assertTrue(payload["verified"])

    def test_move_uses_given_coordinates(self):
        payload = self.run_action(action="move", title="Not Defteri", x=200, y=150)

        self.assertEqual(self.notepad.moves[-1][:2], (200, 150))
        self.assertTrue(payload["verified"])

    def test_resize_changes_dimensions(self):
        payload = self.run_action(action="resize", title="Not Defteri", width=800, height=600)

        rect = self.notepad.moves[-1]
        self.assertEqual((rect[2] - rect[0], rect[3] - rect[1]), (800, 600))
        self.assertTrue(payload["verified"])

    def test_set_bounds_requires_all_coordinates(self):
        with self.assertRaises(ActionForbidden):
            self.tools.run({"action": "set_bounds", "title": "Not Defteri", "x": 10, "y": 10})

    def test_resize_rejects_tiny_window(self):
        with self.assertRaises(ActionForbidden):
            self.tools.run({"action": "resize", "title": "Not Defteri", "width": 50, "height": 600})

    def test_close_sends_wm_close_and_verifies_disappearance(self):
        payload = self.run_action(action="close", title="Not Defteri")

        self.assertEqual(self.notepad.closed, 1)
        self.assertTrue(payload["closed"])

    def test_close_rejects_critical_process(self):
        with patch.object(WindowTools, "_process_name", staticmethod(lambda window: "explorer.exe")):
            with self.assertRaises(ActionForbidden):
                self.tools.run({"action": "close", "title": "Not Defteri"})
        self.assertEqual(self.notepad.closed, 0)

    def test_close_rejects_jarvis_own_window(self):
        self.notepad._pid = os.getpid()
        with self.assertRaises(ActionForbidden):
            self.tools.run({"action": "close", "title": "Not Defteri"})
        self.assertEqual(self.notepad.closed, 0)

    def test_secure_desktop_window_is_rejected(self):
        uac = FakeWindow("User Account Control", 404)
        self.ui.windows.append(uac)
        with self.assertRaises(ActionForbidden) as context:
            self.tools.run({"action": "focus", "title": "User Account Control"})
        self.assertIn("UAC", str(context.exception))

    def test_unknown_window_and_action_are_rejected(self):
        with self.assertRaises(ActionForbidden):
            self.tools.run({"action": "focus", "title": "Yok Böyle Pencere"})
        with self.assertRaises(ActionForbidden):
            self.tools.run({"action": "patlat", "title": "Not Defteri"})

    def test_focus_requires_title_or_handle(self):
        with self.assertRaises(ActionForbidden):
            self.tools.run({"action": "focus"})


if __name__ == "__main__":
    unittest.main()
