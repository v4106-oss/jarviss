import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

from app.core.ai import AI
from app.core.safety import AccessMode, PermissionGate, Risk
from app.core.skill_library import SkillLibrary
from app.core.storage import Storage
from app.core.task_session import (
    TaskSessionError,
    TaskSessionManager,
    extract_screenshot,
    summarize_arguments,
)
from app.core.worker import AIWorker
from app.ui.extra_modals import TaskSessionModal

APP = QApplication.instance() or QApplication([])


class TaskSessionStorageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.storage = Storage(str(self.root / "jarvis.db"))
        self.addCleanup(self.storage.close)

    def test_session_lifecycle_and_steps(self):
        session_id = self.storage.start_task_session("Ekranı incele")
        self.assertEqual(self.storage.get_active_task_session()["id"], session_id)
        self.storage.add_task_step(session_id, 1, "capture_screen", "window=Test", "✅ görüntü", "ok")
        self.storage.add_task_step(session_id, 2, "find_text_on_screen", "text=<gizli>", "❌ bulunamadı", "error")
        steps = self.storage.get_task_steps(session_id)
        self.assertEqual([row["seq"] for row in steps], [1, 2])
        self.assertEqual(steps[1]["status"], "error")

        self.storage.set_task_session_state(session_id, "paused", pause_reason="kullanıcı")
        self.assertEqual(self.storage.get_task_session(session_id)["status"], "paused")
        self.assertEqual(self.storage.get_active_task_session()["id"], session_id)

        self.storage.set_task_session_state(session_id, "completed", summary="bitti")
        self.assertIsNone(self.storage.get_active_task_session())
        latest = self.storage.get_latest_task_session()
        self.assertEqual(latest["id"], session_id)
        self.assertEqual(latest["summary"], "bitti")
        self.assertIsNotNone(latest["finished_at"])

    def test_list_and_limit(self):
        for index in range(3):
            self.storage.start_task_session(f"görev {index}")
        sessions = self.storage.list_task_sessions(2)
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions[0]["goal"], "görev 2")


class TaskSessionManagerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.storage = Storage(str(self.root / "jarvis.db"))
        self.addCleanup(self.storage.close)
        self.skills = SkillLibrary(root=self.root / "skills", seed=False)
        self.manager = TaskSessionManager(
            self.storage, skills=self.skills, data_dir=self.root / "data"
        )

    def test_pause_takeover_resume_and_sequence(self):
        session_id = self.manager.start("Dosyayı taşı")
        self.manager.record_step("capture_screen", {"window": "Dosya Gezgini"}, "✅ pencere bulundu")
        self.manager.pause("kullanıcı istedi")
        session = self.storage.get_task_session(session_id)
        self.assertEqual(session["status"], "paused")
        self.assertEqual(session["pause_reason"], "kullanıcı istedi")

        self.manager.resume()
        session = self.storage.get_task_session(session_id)
        self.assertEqual(session["status"], "running")
        self.manager.takeover("UAC onayı gerekiyor")
        session = self.storage.get_task_session(session_id)
        self.assertEqual(session["status"], "takeover")
        self.assertEqual(session["pause_reason"], "UAC onayı gerekiyor")

        steps = self.storage.get_task_steps(session_id)
        self.assertEqual([row["seq"] for row in steps], [1, 2, 3])
        self.assertEqual(steps[-1]["tool"], "session")
        self.manager.finish("completed", "Taşıma tamamlandı.")
        self.assertEqual(self.storage.get_task_session(session_id)["status"], "completed")

    def test_export_redacts_sensitive_arguments(self):
        session_id = self.manager.start("Gizli içeriği işle")
        self.manager.record_step(
            "write_text_file",
            {"path": "C:/tmp/not.txt", "password": "çok-gizli", "content": "özel metin"},
            '✅ yazıldı {"screenshot": "C:/tmp/kanit.png"}',
        )
        message = self.manager.export(session_id)
        self.assertIn("gorev-", message)
        markdown = (self.root / "data" / "trajectories" / f"gorev-{session_id}.md").read_text("utf-8")
        payload = (self.root / "data" / "trajectories" / f"gorev-{session_id}.json").read_text("utf-8")
        for text in (markdown, payload):
            self.assertNotIn("çok-gizli", text)
            self.assertNotIn("özel metin", text)
            self.assertIn("<gizli>", text)
            self.assertIn("kanit.png", text)

    def test_build_skill_from_verified_steps(self):
        session_id = self.manager.start("Ayarları aç ve tema değiştir")
        self.manager.record_step("open_application", {"name": "Ayarlar"}, "✅ açıldı")
        self.manager.record_step("mouse_control", {"x": 30, "y": 40}, "✅ tıklandı")
        message = self.manager.build_skill("Tema Değiştir", "Tema değiştirmek gerektiğinde kullan.", session_id)
        self.assertIn("tema-degistir", message)
        skill = self.skills.get("tema-degistir")
        self.assertIsNotNone(skill)
        self.assertIn("open_application", skill.body)
        self.assertIn("mouse_control", skill.body)

    def test_build_skill_requires_two_tool_steps(self):
        session_id = self.manager.start("Tek adım")
        self.manager.record_step("capture_screen", {}, "✅ görüntü")
        with self.assertRaises(TaskSessionError):
            self.manager.build_skill("tek-adim", "Tek adım", session_id)


class TaskSessionHelperTests(unittest.TestCase):
    def test_summarize_arguments_redacts_and_collapses(self):
        summary = summarize_arguments(
            {"path": "C:/tmp/a.txt", "token": "abc", "text": "gizli", "rows": [1, 2]}
        )
        self.assertIn("path=C:/tmp/a.txt", summary)
        self.assertIn("token=<gizli>", summary)
        self.assertIn("text=<gizli>", summary)
        self.assertIn("<list:2>", summary)

    def test_extract_screenshot_from_json_and_text(self):
        self.assertEqual(extract_screenshot('{"screenshot": "C:/tmp/a.png"}'), "C:/tmp/a.png")
        self.assertEqual(extract_screenshot("ok C:\\tmp\\b.jpg son"), "C:\\tmp\\b.jpg")
        self.assertIsNone(extract_screenshot("sadece metin"))


class StepAccessModeTests(unittest.TestCase):
    def test_step_mode_asks_for_every_non_read_action(self):
        gate = PermissionGate(lambda: AccessMode.STEP, lambda: True)
        self.assertEqual(gate.decide(Risk.READ).action, "allow")
        for risk in (Risk.CREATE, Risk.MODIFY, Risk.DANGEROUS):
            self.assertEqual(gate.decide(risk).action, "ask")
        self.assertEqual(gate.decide(Risk.FORBIDDEN).action, "deny")

    def test_step_mode_keeps_web_taint_rule(self):
        gate = PermissionGate(lambda: AccessMode.STEP, lambda: True)
        decision = gate.decide(Risk.CREATE, tainted=True)
        self.assertEqual(decision.action, "ask")
        self.assertIn("Adım adım", decision.reason)


class WorkerPauseTests(unittest.TestCase):
    def test_pause_resume_takeover_use_signals_and_gate_tools(self):
        from app.core.settings import Settings

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"LOCALAPPDATA": tmp}):
            storage = Storage(str(Path(tmp) / "jarvis.db"))
            try:
                worker = AIWorker(storage=storage, settings=Settings())
                worker._session = type(
                    "Session",
                    (),
                    {
                        "pause": staticmethod(lambda reason: None),
                        "resume": staticmethod(lambda: None),
                        "takeover": staticmethod(lambda reason: None),
                    },
                )()
                worker._running = True
                events = []
                worker.paused.connect(events.append)
                worker.resumed.connect(lambda: events.append("resumed"))
                worker.takeover_requested.connect(lambda message: events.append(("takeover", message)))

                worker.pause("test")
                self.assertTrue(worker.is_paused)
                worker.resume()
                self.assertFalse(worker.is_paused)
                worker.takeover("UAC onayı")
                self.assertTrue(worker.is_paused)
                self.assertEqual(events[0], "test")
                self.assertEqual(events[1], "resumed")
                self.assertIn(("takeover", "UAC onayı"), events)
                worker.cancel()
                self.assertFalse(worker._wait_if_paused())
            finally:
                storage.close()


class TaskSessionModalTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.storage = Storage(str(self.root / "jarvis.db"))
        self.addCleanup(self.storage.close)
        self.manager = TaskSessionManager(
            self.storage,
            skills=SkillLibrary(root=self.root / "skills", seed=False),
            data_dir=self.root / "data",
        )
        self.session_id = self.manager.start("Modal görevini doğrula")
        self.manager.record_step("capture_screen", {"window": "Test"}, "✅ görüntü")
        self.manager.record_step("find_text_on_screen", {"text": "<gizli>"}, "✅ bulundu")
        self.parent = QWidget()
        self.addCleanup(self.parent.deleteLater)

    def test_modal_shows_steps_and_control_callbacks(self):
        calls = []
        modal = TaskSessionModal(
            self.parent,
            self.manager,
            pause=lambda reason: calls.append(("pause", reason)),
            resume=lambda: calls.append(("resume",)),
            takeover=lambda reason: calls.append(("takeover", reason)),
        )
        self.addCleanup(modal.close_modal)
        self.assertIn("GÖREV #", modal.subtitle_label.text())
        self.assertIn("2 ADIM", modal.subtitle_label.text())
        self.assertIn("Modal görevini doğrula", modal.goal_label.text())

        modal._toggle_pause()
        self.assertEqual(calls[0][0], "pause")

        self.manager.pause("test beklemesi")
        modal._reload()
        self.assertEqual(modal.pause_button.text(), "DEVAM ET")
        modal._toggle_pause()
        self.assertEqual(calls[-1][0], "resume")

        modal._do_takeover()
        self.assertEqual(calls[-1][0], "takeover")
        modal._export()
        self.assertTrue((self.root / "data" / "trajectories" / f"gorev-{self.session_id}.md").exists())
        self.assertIn("dışa aktarıldı", modal.notice.text())


class ProjectInstructionsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "AGENTS.md").write_text("Proje kuralı: testleri kısa tut.", encoding="utf-8")
        rules = self.root / ".qoder" / "rules"
        rules.mkdir(parents=True)
        (rules / "ui.md").write_text("Arayüz metinleri Türkçe olacak.", encoding="utf-8")

    def test_reads_agents_and_rules(self):
        ai = AI.__new__(AI)
        ai._instruction_cache = {}
        files = [self.root / "AGENTS.md", self.root / ".qoder" / "rules" / "ui.md"]
        with patch.object(AI, "_project_instruction_sources", return_value=files):
            text = ai._project_instructions()
        self.assertIn("Proje kuralı", text)
        self.assertIn("Arayüz metinleri", text)
        self.assertIn("AGENTS.md", text)

    def test_limit_and_cache(self):
        ai = AI.__new__(AI)
        ai._instruction_cache = {}
        big = self.root / "BIG.md"
        big.write_text("x" * 5000, encoding="utf-8")
        with patch.object(AI, "_project_instruction_sources", return_value=[big]):
            text = ai._project_instructions(limit=1200)
        self.assertIn("x" * 1200, text)
        self.assertLess(len(text), 1400)
        self.assertEqual(len(ai._instruction_cache), 1)


if __name__ == "__main__":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    unittest.main()
