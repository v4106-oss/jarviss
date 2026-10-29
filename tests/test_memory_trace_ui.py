import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

from app.core.storage import Storage
from app.ui.extra_modals import MemoryModal, TaskTraceModal

APP = QApplication.instance() or QApplication([])


class TraceAndMemoryModalTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "jarvis.db"))
        self.addCleanup(self.storage.close)
        self.parent = QWidget()
        self.addCleanup(self.parent.deleteLater)

    def test_task_trace_lists_runs_and_events(self):
        run_id = self.storage.start_agent_run("Ekrandaki hatayı düzelt")
        self.storage.log_agent_event(run_id, "tool_result", {"tool": "capture_screen", "result": "✅ görüntü"})
        self.storage.finish_agent_run(run_id, "completed", "Hata düzeltildi.")

        modal = TaskTraceModal(self.parent, self.storage)
        self.addCleanup(modal.close_modal)
        self.assertEqual(modal.list_layout.count(), 1)
        self.assertIn("1 GÖREV", modal.subtitle_label.text())
        self.assertIn("1 ARAÇ", modal.subtitle_label.text())

        modal._copy(self.storage.get_agent_runs(1)[0], self.storage.get_agent_events(run_id, 10))
        copied = QApplication.clipboard().text()
        self.assertIn("capture_screen", copied)
        self.assertIn("Ekrandaki hatayı düzelt", copied)

    def test_memory_modal_lists_saves_and_deletes_notes(self):
        self.storage.save_memory("Yanıt tercihi", "Kısa yanıtlar.")
        modal = MemoryModal(self.parent, self.storage)
        self.addCleanup(modal.close_modal)
        self.assertEqual(modal.list_layout.count(), 1)

        modal.key_input.setText("Çalışma saati")
        modal.value_input.setText("Sabahları daha verimliyim.")
        modal._save()
        self.assertEqual(self.storage.get_memory("Çalışma saati"), "Sabahları daha verimliyim.")
        self.assertEqual(modal.list_layout.count(), 2)

        modal._delete("Yanıt tercihi")
        self.assertIsNone(self.storage.get_memory("Yanıt tercihi"))
        self.assertEqual(modal.list_layout.count(), 1)


if __name__ == "__main__":
    unittest.main()
