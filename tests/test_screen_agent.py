import importlib.util
import json
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.core.safety import ActionForbidden, classify_action
from app.core.screen_agent import (
    ScreenAgent,
    ScreenElement,
    merge_elements,
    normalize_text,
    secure_desktop_reason,
)

HAS_PYWINAUTO = importlib.util.find_spec("pywinauto") is not None


class FakeRect:
    def __init__(self, left, top, right, bottom):
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom


class FakeControl:
    def __init__(self, name, kind, bounds, handle_rect=True, enabled=True, visible=True):
        self._name = name
        self._kind = kind
        self._bounds = bounds
        self._handle_rect = handle_rect
        self._enabled = enabled
        self._visible = visible
        self._value = None
        self.toggle_state = None
        self.expanded = False
        self.selected = False
        self.scroll_into_view_calls = 0
        self.invoked = 0
        self.clicked = 0
        self.focused = 0
        self.typed = []
        self.element_info = types.SimpleNamespace(automation_id=f"auto-{kind}")

    def window_text(self):
        return self._name

    def friendly_class_name(self):
        return self._kind

    def rectangle(self):
        return FakeRect(*self._bounds)

    def get_property(self, key):
        if key in ("IsPassword", 30058):
            return False
        raise KeyError(key)

    def is_enabled(self):
        return self._enabled

    def is_visible(self):
        return self._visible

    def get_value(self):
        if self._value is None:
            raise RuntimeError("değer yok")
        return self._value

    def invoke(self):
        self.invoked += 1

    def click_input(self):
        self.clicked += 1

    def set_focus(self):
        self.focused += 1

    def set_edit_text(self, text):
        self.typed.append(text)
        self._value = text

    def get_toggle_state(self):
        if self.toggle_state is None:
            raise RuntimeError("toggle durumu yok")
        return self.toggle_state

    def toggle(self):
        self.toggle_state = 0 if self.toggle_state == 1 else 1

    def check(self):
        self.toggle_state = 1

    def uncheck(self):
        self.toggle_state = 0

    def expand(self):
        self.expanded = True

    def collapse(self):
        self.expanded = False

    def is_expanded(self):
        return self.expanded

    def select(self):
        self.selected = True

    def is_selected(self):
        return self.selected

    def scroll_into_view(self):
        self.scroll_into_view_calls += 1

    def type_keys(self, keys):
        self.typed.append(keys)
        self._value = ""


class FakeWindow:
    def __init__(self, title="Test Penceresi", controls=None, rect=(10, 20, 410, 320)):
        self._title = title
        self._controls = controls or []
        self._rect = rect
        self.handle = 4242

    def window_text(self):
        return self._title

    def descendants(self):
        return self._controls

    def process_id(self):
        return 999999

    def rectangle(self):
        return FakeRect(*self._rect)


class FakeUI:
    def __init__(self, window):
        self.window = window
        self.focus_calls = 0

    def _window(self, title):
        return self.window

    def focus(self, window):
        self.focus_calls += 1
        return True


def make_agent(window, data_dir):
    agent = ScreenAgent(FakeUI(window), Path(data_dir))
    agent._find_window = lambda title: window
    agent._foreground_secure_reason = lambda: ""
    agent._active_handle = lambda: int(window.handle)
    return agent


class SecureGuardTests(unittest.TestCase):
    def test_uac_and_secure_process_detection(self):
        self.assertIn("UAC", secure_desktop_reason("User Account Control"))
        self.assertIn("UAC", secure_desktop_reason("Kullanıcı Hesabı Denetimi"))
        self.assertIn("Güvenli masaüstü", secure_desktop_reason("Herhangi", "consent.exe"))
        self.assertEqual(secure_desktop_reason("Not Defteri", "notepad.exe"), "")

    def test_everyday_edit_is_not_secure_but_password_field_is(self):
        self.assertFalse(ScreenAgent._is_secure_control("Ad", "Edit"))
        self.assertTrue(ScreenAgent._is_secure_control("Parola", "Edit"))
        self.assertTrue(ScreenAgent._is_secure_control("API Password", "Edit"))
        self.assertTrue(ScreenAgent._is_secure_control("", "Edit", password_flag=True))
        self.assertTrue(ScreenAgent._is_secure_control("Şifre", "Document"))

    def test_normalize_text_turkish_accents(self):
        self.assertEqual(normalize_text("  ŞİFRE  "), "sifre")
        self.assertEqual(normalize_text("Kullanıcı"), "kullanici")


class MergeTests(unittest.TestCase):
    def test_duplicate_ocr_line_is_dropped_and_ids_are_sequential(self):
        uia = [
            ScreenElement(0, "uia", "Kaydet", "Button", 10, 10, 110, 40, interactive=True),
        ]
        ocr = [
            ScreenElement(0, "ocr", "Kaydet", "Metin", 12, 12, 100, 38),
            ScreenElement(0, "ocr", "Dosya", "Metin", 10, 100, 90, 125),
        ]

        merged = merge_elements(uia, ocr)

        self.assertEqual([item.name for item in merged], ["Kaydet", "Dosya"])
        self.assertEqual([item.element_id for item in merged], [1, 2])
        self.assertEqual(merged[0].source, "uia")

    def test_same_text_near_same_center_is_deduped(self):
        uia = [ScreenElement(0, "uia", "Tamam", "Button", 100, 100, 180, 130)]
        ocr = [ScreenElement(0, "ocr", "Tamam", "Metin", 102, 103, 178, 128)]

        self.assertEqual(len(merge_elements(uia, ocr)), 1)


class MapTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data = Path(self._tmp.name)
        self.source = self.data / "source.jpg"
        from PIL import Image

        Image.new("RGB", (800, 600), (20, 20, 25)).save(self.source, "JPEG")
        self.button = FakeControl("Kaydet", "Button", (40, 40, 180, 90))
        self.edit = FakeControl("Ad", "Edit", (40, 120, 300, 160))
        self.window = FakeWindow(controls=[self.button, self.edit])
        self.agent = make_agent(self.window, self.data)

    def _map(self, **kwargs):
        from PIL import Image

        Image.new("RGB", (800, 600), (20, 20, 25)).save(self.source, "JPEG")
        with patch("app.core.screen_agent.grab_region", return_value=(self.source, (10, 20), (400, 300))):
            return json.loads(self.agent.map_screen({"include_ocr": False, **kwargs}))

    def test_map_returns_numbered_elements_and_annotated_image(self):
        payload = self._map()

        self.assertEqual(payload["window"], "Test Penceresi")
        self.assertEqual(payload["handle"], 4242)
        self.assertEqual(payload["count"], 2)
        self.assertEqual([item["name"] for item in payload["elements"]], ["Kaydet", "Ad"])
        self.assertEqual([item["id"] for item in payload["elements"]], [1, 2])
        self.assertTrue(Path(payload["image"]).is_file())
        self.assertEqual(Path(payload["image"]).parent, self.data / "screen_maps")
        self.assertIn("güvenilmez", payload["note"])
        self.assertIsNotNone(self.agent._last_map)

    def test_map_skips_focus_when_window_already_active(self):
        with patch("app.core.screen_agent.time.sleep") as sleep:
            payload = self._map()

        self.assertEqual(payload["count"], 2)
        self.assertEqual(self.agent.ui.focus_calls, 0)
        sleep.assert_not_called()

    def test_map_focuses_when_window_is_not_active(self):
        self.agent._active_handle = lambda: 777
        with patch("app.core.screen_agent.time.sleep") as sleep:
            payload = self._map()

        self.assertEqual(payload["count"], 2)
        self.assertEqual(self.agent.ui.focus_calls, 1)
        sleep.assert_called_once_with(0.18)

    def test_uia_click_uses_invoke(self):
        self._map()
        result = json.loads(self.agent.screen_act({"element_id": 1, "action": "click"}))

        self.assertEqual(result["action"], "click")
        self.assertEqual(self.button.invoked, 1)
        self.assertEqual(self.button.clicked, 0)

    def test_uia_type_uses_set_edit_text(self):
        self._map()
        self.agent.screen_act({"element_id": 2, "action": "type", "text": "Mehmet"})

        self.assertEqual(self.edit.typed, ["Mehmet"])

    @unittest.skipUnless(HAS_PYWINAUTO, "pywinauto kurulu değil")
    def test_ocr_element_click_uses_mouse(self):
        self.agent._last_map = {
            "time": time.monotonic(),
            "window_title": "Test Penceresi",
            "handle": 4242,
            "rect": (10, 20, 410, 320),
            "elements": {
                1: ScreenElement(1, "ocr", "Abone Ol", "Metin", 50, 50, 150, 80, interactive=True)
            },
        }
        with patch("pywinauto.mouse.click") as click:
            result = json.loads(self.agent.screen_act({"element_id": 1, "action": "click"}))

        self.assertEqual(result["action"], "click")
        click.assert_called_once()
        self.assertEqual(click.call_args.kwargs["coords"], (100, 65))

    def test_ocr_element_cannot_be_typed(self):
        self.agent._last_map = {
            "time": time.monotonic(),
            "window_title": "Test Penceresi",
            "handle": 4242,
            "rect": (10, 20, 410, 320),
            "elements": {
                1: ScreenElement(1, "ocr", "Arama", "Metin", 50, 50, 150, 80, interactive=True)
            },
        }
        with self.assertRaises(ActionForbidden):
            self.agent.screen_act({"element_id": 1, "action": "type", "text": "x"})

    def test_secure_field_is_rejected(self):
        secure = FakeControl("Parola", "Edit", (40, 200, 300, 240))
        window = FakeWindow(controls=[secure])
        agent = make_agent(window, self.data)
        with patch("app.core.screen_agent.grab_region", return_value=(self.source, (0, 0), (800, 600))):
            payload = json.loads(agent.map_screen({"include_ocr": False}))

        self.assertTrue(payload["elements"][0]["secure"])
        with self.assertRaises(ActionForbidden):
            agent.screen_act({"element_id": 1, "action": "click"})

    def test_stale_map_is_rejected(self):
        self.agent._last_map = {
            "time": time.monotonic() - 31,
            "window_title": "Test Penceresi",
            "handle": 4242,
            "rect": (10, 20, 410, 320),
            "elements": {},
        }
        with self.assertRaises(ActionForbidden) as context:
            self.agent.screen_act({"element_id": 1, "action": "click"})
        self.assertIn("30 saniyeden eski", str(context.exception))

    def test_missing_map_and_unknown_element_are_rejected(self):
        with self.assertRaises(ActionForbidden):
            self.agent.screen_act({"element_id": 1, "action": "click"})

        self._map()
        with self.assertRaises(ActionForbidden):
            self.agent.screen_act({"element_id": 99, "action": "click"})

    def test_invalid_action_is_rejected(self):
        self._map()
        with self.assertRaises(ActionForbidden):
            self.agent.screen_act({"element_id": 1, "action": "paste"})

    def test_window_handle_change_is_rejected(self):
        self._map()
        self.window.handle = 9999
        with self.assertRaises(ActionForbidden):
            self.agent.screen_act({"element_id": 1, "action": "click"})

    def test_snapshot_ref_is_accepted_and_reported(self):
        payload = self._map()
        ref = payload["elements"][0]["ref"]
        self.assertEqual(ref, "s1:e1")

        result = json.loads(self.agent.screen_act({"ref": ref, "action": "click"}))

        self.assertEqual(result["snapshot"], 1)
        self.assertEqual(result["ref"], "s1:e1")
        self.assertEqual(result["delivery"], "delivered_unverified")
        self.assertEqual(result["retry"], "unsafe")
        self.assertEqual(self.button.invoked, 1)

    def test_old_snapshot_ref_is_rejected(self):
        first = self._map()
        self._map()

        with self.assertRaises(ActionForbidden) as context:
            self.agent.screen_act({"ref": first["elements"][0]["ref"], "action": "click"})
        self.assertIn("STALE_REF", str(context.exception))
        self.assertEqual(self.button.invoked, 0)

    def test_disabled_element_is_rejected(self):
        self.button._enabled = False
        self._map()

        with self.assertRaises(ActionForbidden) as context:
            self.agent.screen_act({"element_id": 1, "action": "click"})
        self.assertIn("devre dışı", str(context.exception))

    def test_typed_value_is_read_back_and_verified(self):
        self._map()

        result = json.loads(self.agent.screen_act({"ref": "s1:e2", "action": "type", "text": "Mehmet"}))

        self.assertEqual(self.edit.typed, ["Mehmet"])
        self.assertTrue(result["verified"])
        self.assertEqual(result["delivery"], "delivered_verified")
        self.assertIn("doğrulandı", result["verification"])

    def _act_on_control(self, control, action, **extra):
        from PIL import Image

        Image.new("RGB", (800, 600), (20, 20, 25)).save(self.source, "JPEG")
        window = FakeWindow(controls=[control])
        agent = make_agent(window, self.data)
        with patch("app.core.screen_agent.grab_region", return_value=(self.source, (0, 0), (800, 600))):
            json.loads(agent.map_screen({"include_ocr": False}))
            return json.loads(agent.screen_act({"element_id": 1, "action": action, **extra}))

    def test_uia_set_value_writes_and_verifies(self):
        result = self._act_on_control(self.edit, "set_value", text="Ada")

        self.assertEqual(self.edit.typed, ["Ada"])
        self.assertEqual(result["mechanism"], "uia_value")
        self.assertTrue(result["verified"])

    def test_uia_clear_empties_value(self):
        self.edit._value = "eski metin"
        result = self._act_on_control(self.edit, "clear")

        self.assertEqual(self.edit.typed, [""])
        self.assertEqual(result["mechanism"], "uia_clear")
        self.assertTrue(result["verified"])

    def test_toggle_flips_and_is_verified(self):
        checkbox = FakeControl("Bildirim", "CheckBox", (40, 40, 160, 70))
        checkbox.toggle_state = 0
        result = self._act_on_control(checkbox, "toggle")

        self.assertEqual(checkbox.toggle_state, 1)
        self.assertEqual(result["mechanism"], "uia_toggle")
        self.assertTrue(result["verified"])

    def test_already_checked_is_a_verified_noop(self):
        checkbox = FakeControl("Bildirim", "CheckBox", (40, 40, 160, 70))
        checkbox.toggle_state = 1
        result = self._act_on_control(checkbox, "check")

        self.assertEqual(result["mechanism"], "already_checked")
        self.assertTrue(result["verified"])

    def test_expand_and_select_are_verified(self):
        expander = FakeControl("Ayrıntılar", "Button", (40, 40, 160, 70))
        expanded = self._act_on_control(expander, "expand")
        self.assertTrue(expander.expanded)
        self.assertEqual(expanded["mechanism"], "uia_expand")
        self.assertTrue(expanded["verified"])

        item = FakeControl("Satır", "ListItem", (40, 100, 160, 130))
        selected = self._act_on_control(item, "select")
        self.assertTrue(item.selected)
        self.assertEqual(selected["mechanism"], "uia_select")
        self.assertTrue(selected["verified"])

    @unittest.skipUnless(HAS_PYWINAUTO, "pywinauto kurulu değil")
    def test_scroll_to_and_mouse_wheel(self):
        scroller = FakeControl("Liste", "ListItem", (40, 40, 160, 300))
        result = self._act_on_control(scroller, "scroll_to")
        self.assertEqual(scroller.scroll_into_view_calls, 1)
        self.assertEqual(result["mechanism"], "uia_scroll_into_view")

        with patch("pywinauto.mouse.scroll") as scroll:
            result = self._act_on_control(scroller, "scroll", direction="up", amount=5)
        scroll.assert_called_once_with(coords=(100, 170), wheel_dist=5)
        self.assertEqual(result["mechanism"], "mouse_wheel")

    @unittest.skipUnless(HAS_PYWINAUTO, "pywinauto kurulu değil")
    def test_hover_moves_mouse_to_element_center(self):
        control = FakeControl("İpucu", "Button", (40, 40, 160, 80))
        with patch("pywinauto.mouse.move") as move:
            result = self._act_on_control(control, "hover")
        move.assert_called_once_with(coords=(100, 60))
        self.assertEqual(result["mechanism"], "mouse_hover")

    @unittest.skipUnless(HAS_PYWINAUTO, "pywinauto kurulu değil")
    def test_drag_to_ref_uses_target_center(self):
        self._map()
        with patch("pywinauto.mouse.press") as press, \
                patch("pywinauto.mouse.move") as move, \
                patch("pywinauto.mouse.release") as release, \
                patch("app.core.screen_agent.time.sleep"):
            result = json.loads(
                self.agent.screen_act({"ref": "s1:e1", "action": "drag", "to_ref": "s1:e2"})
            )

        self.assertEqual(result["mechanism"], "synthetic_drag")
        press.assert_called_once_with(button="left", coords=(110, 65))
        release.assert_called_once_with(button="left", coords=(170, 140))
        self.assertEqual(move.call_count, 24)

    def test_drag_without_target_is_rejected(self):
        self._map()
        with self.assertRaises(ActionForbidden):
            self.agent.screen_act({"ref": "s1:e1", "action": "drag"})

    def test_ocr_element_rejects_rich_uia_actions(self):
        self.agent._last_map = {
            "time": time.monotonic(),
            "window_title": "Test Penceresi",
            "handle": 4242,
            "rect": (10, 20, 410, 320),
            "elements": {
                1: ScreenElement(1, "ocr", "Arama", "Metin", 50, 50, 150, 80, interactive=True)
            },
        }
        with self.assertRaises(ActionForbidden):
            self.agent.screen_act({"element_id": 1, "action": "set_value", "text": "x"})

    def test_expect_window_verifies_delivery(self):
        self._map()

        result = json.loads(
            self.agent.screen_act({"element_id": 1, "action": "click", "expect_window": "Test Penceresi"})
        )

        self.assertTrue(result["verified"])
        self.assertEqual(result["delivery"], "delivered_verified")

    def test_fast_mode_skips_ocr_when_uia_is_rich(self):
        controls = [
            FakeControl(f"Düğme {index}", "Button", (20 + index, 20 + index, 100 + index, 60 + index))
            for index in range(20)
        ]
        window = FakeWindow(controls=controls)
        agent = make_agent(window, self.data)
        agent._collect_ocr = MagicMock(return_value=([], "", []))

        with patch("app.core.screen_agent.grab_region", return_value=(self.source, (10, 20), (400, 300))):
            payload = json.loads(agent.map_screen({"include_ocr": True}))

        agent._collect_ocr.assert_not_called()
        self.assertTrue(payload["ocr_skipped"])
        self.assertEqual(payload["timings_ms"]["ocr"], 0)
        self.assertEqual(payload["count"], 20)
        self.assertTrue(any("Hızlı tarama" in warning for warning in payload["warnings"]))
        for key in ("capture", "uia", "ocr", "image_load", "hashes", "annotate", "total"):
            self.assertIn(key, payload["timings_ms"])

    def test_fast_false_runs_full_ocr_scan(self):
        ocr_row = ScreenElement(0, "ocr", "Ara", "Metin", 200, 200, 280, 230, interactive=True)
        self.agent._collect_ocr = MagicMock(return_value=([ocr_row], "tr-TR", []))

        payload = self._map(include_ocr=True, fast=False)

        self.agent._collect_ocr.assert_called_once()
        self.assertFalse(payload["ocr_skipped"])
        self.assertEqual(payload["ocr_language"], "tr-TR")
        self.assertIn("Ara", [item["name"] for item in payload["elements"]])


class WaitTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.window = FakeWindow(
            controls=[FakeControl("Kaydedildi", "Text", (10, 10, 140, 34))]
        )
        self.agent = make_agent(self.window, self._tmp.name)

    def test_finds_text_without_taking_a_screenshot(self):
        with patch("app.core.screen_agent.grab_region") as grab:
            payload = json.loads(
                self.agent.wait_for({"window_title": "Test Penceresi", "text": "kaydedildi", "timeout": 1})
            )

        grab.assert_not_called()
        self.assertTrue(payload["found"])
        self.assertEqual(payload["window"], "Test Penceresi")
        self.assertIn("arayüzde", payload["detail"])
        self.assertIn("görüntüsü/OCR açmaz", payload["note"])

    def test_matches_window_title(self):
        payload = json.loads(
            self.agent.wait_for({"window_title": "Test", "text": "Test Penceresi", "timeout": 1})
        )

        self.assertTrue(payload["found"])
        self.assertIn("başlığında", payload["detail"])

    def test_timeout_returns_not_found_without_raising(self):
        started = time.monotonic()
        payload = json.loads(
            self.agent.wait_for({"window_title": "Test Penceresi", "text": "yok", "timeout": 0.6})
        )

        self.assertFalse(payload["found"])
        self.assertIn("bulunamadı", payload["detail"])
        self.assertLess(time.monotonic() - started, 3)

    def test_text_or_title_is_required(self):
        with self.assertRaises(ActionForbidden):
            self.agent.wait_for({})


class ClipboardTests(unittest.TestCase):
    @unittest.skipUnless(HAS_PYWINAUTO, "pywinauto kurulu değil")
    def test_paste_restores_previous_clipboard(self):
        with patch.object(ScreenAgent, "_clipboard_text", return_value="önceki"), \
                patch.object(ScreenAgent, "_set_clipboard_text") as setter, \
                patch("pywinauto.keyboard.send_keys") as send:
            ScreenAgent._paste_text("yeni")

        self.assertEqual(setter.call_args_list[0].args, ("yeni",))
        self.assertEqual(setter.call_args_list[1].args, ("önceki",))
        send.assert_called_once()


class ExecutorDispatchTests(unittest.TestCase):
    def test_executor_forwards_map_image_and_verification_screenshot(self):
        from app.core.agent import ActionExecutor, PlannedAction
        from app.core.safety import AuditLog, KillSwitch, PathPolicy, PermissionGate, SafetyServices, UndoStore

        with tempfile.TemporaryDirectory() as root_dir, tempfile.TemporaryDirectory() as data_dir:
            root = Path(root_dir)
            services = SafetyServices(
                policy=PathPolicy([root], base=root),
                gate=PermissionGate(),
                audit=AuditLog(Path(data_dir) / "audit.jsonl"),
                undo=UndoStore(Path(data_dir) / "undo"),
                kill=KillSwitch(),
            )
            screenshots = []
            executor = ActionExecutor(services, on_screenshot=screenshots.append)
            map_image = Path(data_dir) / "map.jpg"
            map_image.write_bytes(b"fake")
            verify_image = Path(data_dir) / "verify.jpg"
            verify_image.write_bytes(b"fake")

            class FakeScreens:
                def map_screen(self, args):
                    return json.dumps({"image": str(map_image), "count": 0})

                def screen_act(self, args):
                    return json.dumps({"action": "click", "note": ""})

            executor.screens = FakeScreens()

            def fake_capture(delay=0.35):
                screenshots.append(str(verify_image))
                return str(verify_image)

            executor._capture_for_model = fake_capture

            map_result = executor.execute(PlannedAction("screen_map", {}, "map"))
            act_result = json.loads(executor.execute(PlannedAction("screen_act", {"element_id": 1, "action": "click"}, "act")))

            self.assertEqual(json.loads(map_result)["image"], str(map_image))
            self.assertEqual(screenshots, [str(map_image), str(verify_image)])
            self.assertEqual(act_result["screenshot"], str(verify_image))
            self.assertIn("doğrulama", act_result["note"])

    def test_executor_routes_window_control(self):
        from app.core.agent import ActionExecutor, PlannedAction
        from app.core.safety import AuditLog, KillSwitch, PathPolicy, PermissionGate, SafetyServices, UndoStore

        with tempfile.TemporaryDirectory() as root_dir, tempfile.TemporaryDirectory() as data_dir:
            root = Path(root_dir)
            services = SafetyServices(
                policy=PathPolicy([root], base=root),
                gate=PermissionGate(),
                audit=AuditLog(Path(data_dir) / "audit.jsonl"),
                undo=UndoStore(Path(data_dir) / "undo"),
                kill=KillSwitch(),
            )
            executor = ActionExecutor(services)

            class FakeWindows:
                def run(self, args):
                    return json.dumps({"action": args["action"], "routed": True})

            executor.window_tools = FakeWindows()
            result = json.loads(
                executor.execute(PlannedAction("window_control", {"action": "list"}, "list"))
            )

        self.assertEqual(result, {"action": "list", "routed": True})


class VisualReflectionTests(unittest.TestCase):
    @staticmethod
    def _save(path, rectangle):
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (320, 240), "white")
        ImageDraw.Draw(image).rectangle(rectangle, fill="black")
        image.save(path, "JPEG", quality=90)

    def test_unchanged_screen_produces_a_warning(self):
        from app.core.agent import ActionExecutor
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "before.jpg"
            second = Path(tmp) / "after.jpg"
            self._save(first, (0, 0, 160, 240))
            self._save(second, (0, 0, 160, 240))
            with Image.open(first) as image:
                before = ActionExecutor._image_fingerprint(image)

            changed, note = ActionExecutor._visual_change(before, str(second))

        self.assertFalse(changed)
        self.assertIn("Görsel yansıma", note)

    def test_changed_screen_reports_true(self):
        from app.core.agent import ActionExecutor
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "before.jpg"
            second = Path(tmp) / "after.jpg"
            self._save(first, (0, 0, 160, 240))
            self._save(second, (160, 0, 320, 240))
            with Image.open(first) as image:
                before = ActionExecutor._image_fingerprint(image)

            changed, note = ActionExecutor._visual_change(before, str(second))

        self.assertTrue(changed)
        self.assertEqual(note, "")


class SafetyClassificationTests(unittest.TestCase):
    def test_screen_tools_have_expected_risk(self):
        policy = MagicMock()
        self.assertEqual(classify_action("screen_map", {}, policy).name, "READ")
        self.assertEqual(classify_action("screen_wait", {"text": "Kaydet"}, policy).name, "READ")
        self.assertEqual(
            classify_action("screen_act", {"element_id": 1, "action": "click"}, policy).name,
            "DANGEROUS",
        )
        with self.assertRaises(ActionForbidden):
            classify_action("screen_wait", {}, policy)

    def test_screen_act_requires_valid_element_and_action(self):
        policy = MagicMock()
        with self.assertRaises(ActionForbidden):
            classify_action("screen_act", {"action": "click"}, policy)
        with self.assertRaises(ActionForbidden):
            classify_action("screen_act", {"element_id": 1, "action": "paste"}, policy)

    def test_tools_are_declared_for_the_model(self):
        from app.core.ai import TOOL_DECLARATIONS

        declarations = {
            item["name"]: item
            for group in TOOL_DECLARATIONS
            for item in group["functionDeclarations"]
        }
        self.assertIn("screen_map", declarations)
        screen_wait = declarations["screen_wait"]
        for key in ("text", "window_title", "timeout"):
            self.assertIn(key, screen_wait["parameters"]["properties"])
        screen_act = declarations["screen_act"]
        self.assertEqual(screen_act["parameters"]["required"], ["action"])
        self.assertEqual(
            screen_act["parameters"]["properties"]["action"]["enum"],
            [
                "click", "double_click", "right_click", "focus", "type", "set_value",
                "clear", "toggle", "check", "uncheck", "expand", "collapse", "select",
                "scroll", "scroll_to", "hover", "drag",
            ],
        )
        for key in ("ref", "element_id", "expect", "expect_window", "to_ref", "to_x", "to_y"):
            self.assertIn(key, screen_act["parameters"]["properties"])
        window_control = declarations["window_control"]
        self.assertIn("close", window_control["parameters"]["properties"]["action"]["enum"])
        self.assertIn("set_bounds", window_control["parameters"]["properties"]["action"]["enum"])
        self.assertIn("clear", declarations["clipboard"]["parameters"]["properties"]["action"]["enum"])


if __name__ == "__main__":
    unittest.main()
