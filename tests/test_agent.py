import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.core.agent import ActionExecutor, ActionPlan, ActionPlanner, ApplicationLauncher, PlannedAction, WindowsUI
from app.core.desktop import DesktopTools
from app.core.safety import ActionForbidden, AuditLog, KillSwitch, PathPolicy, PermissionGate, SafetyServices, UndoStore
from app.core.file_context import extract_file_context


class AgentPlannerTests(unittest.TestCase):
    def test_complex_folder_document_command(self):
        prompt = (
            'mehmet adında bir klasör oluştur ve içine "akif" adında bir metin belgesi oluştur. '
            'belgenin içeriğinde 1 kere "adana" yaz'
        )
        plan = ActionPlanner().plan(prompt)

        self.assertIsNotNone(plan)
        self.assertEqual([item.action_type for item in plan.actions], ["create_folder", "write_text_file"])
        self.assertEqual(Path(plan.actions[1].arguments["path"]).parts[-2:], ("mehmet", "akif.txt"))
        self.assertEqual(plan.actions[1].arguments["content"], "adana")

    def test_executor_creates_real_file_and_verifies_it(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as data:
            root = Path(directory)
            services = SafetyServices(
                policy=PathPolicy([root], base=root),
                gate=PermissionGate(),
                audit=AuditLog(Path(data) / "audit.jsonl"),
                undo=UndoStore(Path(data) / "undo"),
                kill=KillSwitch(),
            )
            executor = ActionExecutor(services)
            target = root / "proje" / "not.txt"
            plan = ActionPlan(
                [
                    PlannedAction("create_folder", {"path": str(target.parent)}, "Klasör oluştur"),
                    PlannedAction("write_text_file", {"path": str(target), "content": "Adana", "overwrite": False}, "Dosya yaz"),
                ]
            )

            results = executor.execute_plan(plan)

            self.assertEqual(len(results), 2)
            self.assertTrue(all(item.startswith("✅") for item in results))
            self.assertTrue(target.exists())
            self.assertEqual(target.read_text(encoding="utf-8"), "Adana")

    def test_text_file_context_is_available_to_model(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "bilgi.txt"
            source.write_text("Jarvis dosya analizi testi", encoding="utf-8")
            context = extract_file_context(str(source))

            self.assertIn("bilgi.txt", context)
            self.assertIn("Jarvis dosya analizi testi", context)


class ApplicationLauncherTests(unittest.TestCase):
    def test_discovers_application_without_a_predefined_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            menu = Path(directory)
            shortcut = menu / "Microsoft Edge.lnk"
            shortcut.write_bytes(b"")
            (menu / "Discord.lnk").write_bytes(b"")
            launcher = ApplicationLauncher([menu])
            with patch.object(launcher, "_focus_running", return_value=None), \
                 patch.object(launcher, "_wait_for_new_window", side_effect=["Microsoft Edge", "Discord"]), \
                 patch("app.core.agent.os.startfile") as startfile:
                self.assertEqual(launcher.launch("edge"), "Microsoft Edge")
                self.assertEqual(launcher.launch("discord"), "Discord")
            self.assertEqual(startfile.call_count, 2)

    def test_launch_focuses_an_already_running_window_instead_of_opening_a_copy(self):
        launcher = ApplicationLauncher([])
        with patch.object(launcher, "_focus_running", return_value="Adsız - Paint"), \
             patch("app.core.agent.os.startfile") as startfile:
            self.assertEqual(launcher.launch("Paint"), "Adsız - Paint")
        startfile.assert_not_called()

    def test_launcher_window_automatically_clicks_play_and_verifies_game(self):
        with tempfile.TemporaryDirectory() as directory:
            menu = Path(directory)
            (menu / "League of Legends.lnk").write_bytes(b"")
            launcher = ApplicationLauncher([menu])
            verified = (
                "League of Legends (Riot Client açıldı, 'Play' düğmesine tıklandı "
                "ve sonuç doğrulandı)"
            )
            with patch.object(launcher, "_focus_running", return_value=None), \
                 patch.object(launcher, "_wait_for_new_window", return_value="Riot Client"), \
                 patch.object(launcher, "_wait_for_target", return_value=""), \
                 patch.object(launcher, "_matching_processes", return_value=[]), \
                 patch.object(launcher, "_start_from_launcher", return_value=(verified, "")) as start, \
                 patch("app.core.agent.os.startfile"):
                result = launcher.launch("League of Legends")

        self.assertIn("League of Legends", result)
        self.assertIn("Play", result)
        start.assert_called_once()

    def test_launcher_without_play_button_is_reported_as_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            menu = Path(directory)
            (menu / "League of Legends.lnk").write_bytes(b"")
            launcher = ApplicationLauncher([menu])
            with patch.object(launcher, "_focus_running", return_value=None), \
                 patch.object(launcher, "_wait_for_new_window", return_value="Riot Client"), \
                 patch.object(launcher, "_wait_for_target", return_value=""), \
                 patch.object(launcher, "_matching_processes", return_value=[]), \
                 patch.object(
                     launcher,
                     "_start_from_launcher",
                     return_value=("", "Riot Client içinde Oyna/Play düğmesi bulunamadı"),
                 ), \
                 patch("app.core.agent.os.startfile"):
                with self.assertRaises(ActionForbidden) as raised:
                    launcher.launch("League of Legends")

        message = str(raised.exception)
        self.assertIn("Oyna/Play", message)
        self.assertIn("doğrulamayı", message)
        self.assertNotIn("hazır", message)

    def test_play_click_requires_a_verified_game_window(self):
        launcher = ApplicationLauncher([])
        window = MagicMock()
        with patch("app.core.agent.WindowsUI._window", return_value=window), \
             patch("app.core.agent.WindowsUI.focus", return_value=True), \
             patch.object(launcher, "_click_play", return_value="Play") as click, \
             patch.object(launcher, "_wait_for_target", return_value="League of Legends"):
            verified, reason = launcher._start_from_launcher("League of Legends", "Riot Client")

        self.assertEqual(reason, "")
        self.assertIn("League of Legends", verified)
        self.assertIn("'Play'", verified)
        click.assert_called_once_with(window)

    def test_window_matching_separates_launcher_from_target(self):
        self.assertFalse(ApplicationLauncher._window_matches("League of Legends", "Riot Client"))
        self.assertTrue(ApplicationLauncher._window_matches("League of Legends", "League of Legends"))
        self.assertTrue(
            ApplicationLauncher._window_matches("Microsoft Edge", "YouTube - Microsoft Edge")
        )
        self.assertTrue(ApplicationLauncher._window_matches("OBS Studio", "OBS 30.1.2"))

    def test_play_label_matching_avoids_unrelated_text(self):
        self.assertTrue(ApplicationLauncher._is_play_label("Oyna"))
        self.assertTrue(ApplicationLauncher._is_play_label("PLAY"))
        self.assertFalse(ApplicationLauncher._is_play_label("Display"))
        self.assertFalse(ApplicationLauncher._is_play_label("Playlist"))

    def test_process_verification_does_not_treat_launcher_as_game(self):
        launcher = ApplicationLauncher([])
        with patch(
            "app.core.agent.ApplicationLauncher._process_names",
            return_value=["RiotClientServices.exe", "LeagueClientUx.exe"],
        ):
            self.assertEqual(
                launcher._matching_processes("League of Legends"), ["LeagueClientUx.exe"]
            )
            self.assertEqual(
                launcher._matching_processes("Riot Client"), ["RiotClientServices.exe"]
            )

    def test_open_url_uses_selected_browser_not_default(self):
        with tempfile.TemporaryDirectory() as directory:
            menu = Path(directory)
            (menu / "Microsoft Edge.lnk").write_bytes(b"")
            launcher = ApplicationLauncher([menu])
            with patch.object(launcher, "_shortcut_target", return_value=str(Path(__file__))), \
                 patch("app.core.agent.subprocess.Popen") as popen, \
                 patch("app.core.agent.webbrowser.open_new_tab") as default_browser:
                result = launcher.open_url("Edge", "https://ogmmateryal.eba.gov.tr")
            self.assertIn("Microsoft Edge", result)
            self.assertEqual(popen.call_args.args[0][1], "https://ogmmateryal.eba.gov.tr")
            default_browser.assert_not_called()

    def test_executor_launches_discovered_application(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as data:
            root = Path(directory)
            (root / "Discord.lnk").write_bytes(b"")
            services = SafetyServices(
                policy=PathPolicy([root], base=root),
                gate=PermissionGate(),
                audit=AuditLog(Path(data) / "audit.jsonl"),
                undo=UndoStore(Path(data) / "undo"),
                kill=KillSwitch(),
            )
            executor = ActionExecutor(services)
            executor.applications = ApplicationLauncher([root])
            with patch.object(executor.applications, "_focus_running", return_value=None), \
                 patch.object(executor.applications, "_wait_for_new_window", return_value="Discord"), \
                 patch("app.core.agent.os.startfile") as startfile:
                result = executor.execute(PlannedAction("open_application", {"application": "Discord"}, "Discord aç"))
            self.assertTrue(result.startswith("✅"), result)
            startfile.assert_called_once_with(str(root / "Discord.lnk"))

    def test_steam_workshop_uri_opens_and_unsafe_schemes_are_refused(self):
        launcher = ApplicationLauncher([])
        workshop = "steam://openurl/https://steamcommunity.com/sharedfiles/filedetails/?id=123"
        with patch("app.core.agent.os.startfile") as startfile:
            self.assertEqual(launcher.open_system_uri(workshop), workshop)
            startfile.assert_called_once_with(workshop)
        for bad in (
            "https://example.com",
            "file:///C:/Windows/System32/cmd.exe",
            "steam://openurl/file:///C:/Windows/System32/cmd.exe",
            "steam://run/123\ncalc.exe",
            "steam://" + "a" * 2000,
        ):
            with self.subTest(bad=bad[:60]), self.assertRaises(ActionForbidden):
                launcher.open_system_uri(bad)


    def test_obs_query_does_not_match_roblox_studio(self):
        launcher = ApplicationLauncher([])
        self.assertEqual(launcher._score("obs studio", Path("Roblox Studio.lnk")), 0.0)

    def test_winget_package_is_discovered_and_launched(self):
        with tempfile.TemporaryDirectory() as root:
            package = Path(root) / "OBSProject.OBSStudio_Microsoft.Winget.Source_8wekyb3d8bbwe"
            executable = package / "bin" / "64bit" / "obs64.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"MZ")
            launcher = ApplicationLauncher([], winget_root=Path(root))

            self.assertEqual(
                launcher._find_winget_app("OBS Studio"),
                (str(executable), "obsstudio"),
            )
            with patch.object(launcher, "_focus_running", return_value=None), \
                 patch.object(launcher, "_visible_windows", return_value={}), \
                 patch.object(launcher, "_find_shortcut", return_value=None), \
                 patch.object(launcher, "_find_registered_app", return_value=None), \
                 patch.object(launcher, "_find_store_app", return_value=None), \
                 patch.object(launcher, "_wait_for_new_window", return_value="OBS Studio"), \
                 patch.object(launcher, "_settle_window", return_value="OBS Studio açıldı"), \
                 patch("app.core.agent.subprocess.Popen") as popen:
                result = launcher.launch("OBS Studio")

        self.assertEqual(result, "OBS Studio açıldı")
        self.assertEqual(popen.call_args.args[0], [str(executable)])

    def test_launch_failure_does_not_claim_app_is_not_installed(self):
        launcher = ApplicationLauncher([])
        with patch.object(launcher, "_focus_running", return_value=None), \
             patch.object(launcher, "_visible_windows", return_value={}), \
             patch.object(launcher, "_find_shortcut", return_value=None), \
             patch.object(launcher, "_find_registered_app", return_value=None), \
             patch.object(launcher, "_find_store_app", return_value=None), \
             patch.object(launcher, "_find_winget_app", return_value=None):
            with self.assertRaises(ActionForbidden) as caught:
                launcher.launch("Hiçbir Yerde Olmayan Uygulama")
        text = str(caught.exception)
        self.assertIn("kanıtlamaz", text)
        self.assertIn("alternatif", text)


class GeneralActionTests(unittest.TestCase):
    def test_generated_python_command_runs_with_real_result(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as data:
            root = Path(directory)
            services = SafetyServices(
                policy=PathPolicy([root], base=root),
                gate=PermissionGate(),
                audit=AuditLog(Path(data) / "audit.jsonl"),
                undo=UndoStore(Path(data) / "undo"),
                kill=KillSwitch(),
            )
            executor = ActionExecutor(services)
            plan = ActionPlan([PlannedAction("run_command", {
                "language": "python", "code": "print('genel eylem çalıştı')", "working_directory": str(root),
            }, "Python çalıştır")])
            self.assertEqual(executor.assess(plan).decision, "ask")
            result = executor.execute_plan(plan)[0]
            self.assertTrue(result.startswith("✅"), result)
            self.assertIn("genel eylem çalıştı", result)

    def test_run_command_rejects_desktop_automation_bypasses(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as data:
            root = Path(directory)
            services = SafetyServices(
                policy=PathPolicy([root], base=root),
                gate=PermissionGate(),
                audit=AuditLog(Path(data) / "audit.jsonl"),
                undo=UndoStore(Path(data) / "undo"),
                kill=KillSwitch(),
            )
            executor = ActionExecutor(services)
            for code in (
                "import pyautogui\npyautogui.click(10, 10)",
                "import win32gui\nwin32gui.SetForegroundWindow(1)",
                "import pywinauto\npywinauto.mouse.click(button='left', coords=(1, 1))",
            ):
                with self.subTest(code=code.splitlines()[0]):
                    result = executor.execute(PlannedAction("run_command", {
                        "language": "python", "code": code, "working_directory": str(root),
                    }, "Masaüstü otomasyonu"))
                    self.assertTrue(result.startswith("❌"), result)
                    self.assertIn("mouse_control", result)

    def test_drag_uses_supported_move_signature(self):
        with tempfile.TemporaryDirectory() as data, patch("pywinauto.mouse") as mouse:
            tools = DesktopTools(Path(data))
            result = tools.mouse({"action": "drag", "x": 10, "y": 20, "end_x": 110, "end_y": 220})
        self.assertIn("drag", result)
        mouse.press.assert_called_once_with(coords=(10, 20))
        mouse.release.assert_called_once_with(coords=(110, 220))
        self.assertEqual(mouse.move.call_count, 24)
        for call in mouse.move.call_args_list:
            self.assertNotIn("duration", call.kwargs)

    def test_save_file_dialog_fills_standard_controls_and_verifies_file(self):
        ui = WindowsUI()
        target = Path(tempfile.gettempdir()) / "jarvis_save_dialog_test.png"
        if target.exists():
            target.unlink()
        edit = MagicMock()
        edit.friendly_class_name.return_value = "Edit"
        edit.automation_id.return_value = "1001"
        button = MagicMock()
        button.friendly_class_name.return_value = "Button"
        button.automation_id.return_value = "1"
        button.invoke.side_effect = lambda: target.write_bytes(b"test")
        window = MagicMock()
        window.descendants.return_value = [edit, button]
        window.is_minimized.return_value = False
        with patch.object(ui, "_window", return_value=window), \
                patch.object(ui, "focus", return_value=True), \
                patch("pywinauto.keyboard.send_keys"):
            result = ui.save_file("Paint", target)
        self.assertIn("kaydedildi", result)
        edit.set_edit_text.assert_called_once_with(str(target))
        button.invoke.assert_called_once()
        self.assertTrue(target.exists())
        target.unlink()

    def test_ui_action_uses_generic_control_name(self):
        ui = WindowsUI()
        control = MagicMock()
        control.window_text.return_value = "B.i.a.ş, sesli kanal"
        window = MagicMock()
        window.descendants.return_value = [control]
        with patch.object(ui, "_window", return_value=window), patch.object(ui, "focus", return_value=True):
            result = ui.act("Discord", "B.i.a.ş", "click")
        control.invoke.assert_called_once()
        self.assertIn("click", result)

    def test_draw_shape_selects_tool_and_drags_cleanly(self):
        ui = WindowsUI()
        tool = MagicMock()
        tool.window_text.return_value = "Dikdörtgen"
        tool.friendly_class_name.return_value = "ListItem"
        window = MagicMock()
        window.descendants.return_value = [tool]
        window.is_minimized.return_value = False
        with patch.object(ui, "_window", return_value=window), \
                patch.object(ui, "focus", return_value=True), \
                patch("pywinauto.mouse") as mouse:
            result = ui.draw_shape("Paint", "rectangle", 600, 450, 900, 700)
        tool.click_input.assert_called_once()
        mouse.press.assert_called_once_with(coords=(600, 450))
        mouse.release.assert_called_once_with(coords=(900, 700))
        self.assertEqual(mouse.move.call_count, 24)
        self.assertIn("Dikdörtgen", result)
        self.assertIn("(600, 450)", result)

    def test_draw_shape_refuses_when_window_cannot_be_focused(self):
        ui = WindowsUI()
        tool = MagicMock()
        tool.window_text.return_value = "Dikdörtgen"
        tool.friendly_class_name.return_value = "ListItem"
        window = MagicMock()
        window.descendants.return_value = [tool]
        with patch.object(ui, "_window", return_value=window), \
                patch.object(ui, "focus", return_value=False), \
                patch("pywinauto.mouse") as mouse:
            with self.assertRaises(ActionForbidden):
                ui.draw_shape("Paint", "rectangle", 600, 450, 900, 700)
        mouse.press.assert_not_called()

    def test_draw_shape_reports_unknown_tool(self):
        ui = WindowsUI()
        window = MagicMock()
        window.descendants.return_value = []
        window.is_minimized.return_value = False
        with patch.object(ui, "_window", return_value=window):
            with self.assertRaises(ActionForbidden):
                ui.draw_shape("Paint", "circle", 10, 10, 20, 20)

    def test_inspect_reports_control_centers_and_focuses_window(self):
        ui = WindowsUI()
        control = MagicMock()
        control.window_text.return_value = "Dikdörtgen"
        control.friendly_class_name.return_value = "ListItem"
        control.automation_id.return_value = ""
        control.rectangle.return_value = types.SimpleNamespace(left=789, top=209, right=809, bottom=229)
        canvas = MagicMock()
        canvas.window_text.return_value = ""
        canvas.friendly_class_name.return_value = "Pane"
        canvas.rectangle.return_value = types.SimpleNamespace(left=300, top=300, right=1700, bottom=950)
        window = MagicMock()
        window.window_text.return_value = "Adsız - Paint"
        window.descendants.return_value = [control, canvas]
        window.rectangle.return_value = types.SimpleNamespace(left=279, top=147, right=1771, bottom=1011)
        with patch.object(ui, "_window", return_value=window):
            data = json.loads(ui.inspect("Paint"))
        self.assertEqual(data["window_rect"], [279, 147, 1771, 1011])
        self.assertEqual(data["controls"][0]["center_x"], 799)
        self.assertEqual(data["controls"][0]["center_y"], 219)
        self.assertEqual(data["content_rect"], [300, 300, 1700, 950])
        window.set_focus.assert_called_once()


if __name__ == "__main__":
    unittest.main()
