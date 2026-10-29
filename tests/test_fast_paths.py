import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")

from app.core.agent import ActionPlanner, ActionPlan, PlannedAction
from app.core.worker import AIWorker
from app.ui.main_window import (
    camera_context_needed,
    local_ui_intent,
    screen_context_needed,
    screen_voice_intent,
)


class LocalUiIntentTests(unittest.TestCase):
    def test_panel_commands_are_classified(self):
        cases = {
            "haritayı aç": "map",
            "Haritayı açar mısın": "map",
            "tarayıcıyı aç": "browser",
            "ayarları göster": "settings",
            "modelleri aç": "models",
            "komut paletini aç": "commands",
            "sohbet geçmişini aç": "history",
            "pano merkezini aç": "clipboard",
            "hatırlatıcıları aç": "reminders",
            "brifingi göster": "briefing",
            "saat panelini aç": "time",
            "sistem panelini aç": "system",
            "zamanlayıcıyı aç": "timer",
            "notlarımı göster": "notes",
            "takvimi aç": "calendar",
            "hava durumunu aç": "weather",
            "kitap taramayı aç": "book",
            "görev günlüğünü aç": "trace",
            "hafızayı aç": "memory",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(local_ui_intent(text), expected)

    def test_long_or_unrelated_text_is_left_to_ai(self):
        for text in (
            "merhaba",
            "haritayı aç ve oradan en yakın eczaneyi bul",
            "bana bugün hava nasıl olacak acaba",
            "",
        ):
            with self.subTest(text=text):
                self.assertIsNone(local_ui_intent(text))


class CameraContextTests(unittest.TestCase):
    def test_visual_questions_need_camera_frame(self):
        for text in (
            "ne görüyorsun?",
            "kamerada ne var",
            "ekranda ne yazıyor",
            "beni görüyor musun",
            "etrafı incele",
            "elimde ne var",
            "fotoğrafı oku",
            "resimde kaç kişi var",
        ):
            with self.subTest(text=text):
                self.assertTrue(camera_context_needed(text))

    def test_non_visual_text_skips_camera_frame(self):
        for text in (
            "merhaba",
            "saat kaç",
            "not al: yarın toplantı var",
            "haritayı aç",
            "github commitlerini özetle",
        ):
            with self.subTest(text=text):
                self.assertFalse(camera_context_needed(text))


class ScreenContextTests(unittest.TestCase):
    def test_screen_questions_request_screen_frame(self):
        for text in (
            "ekranımda ne var",
            "ekranda ne yazıyor",
            "bu pencerede ne açık",
            "aktif sekmeyi oku",
            "masaüstünde hangi uygulama var",
            "hangi uygulama açık",
        ):
            with self.subTest(text=text):
                self.assertTrue(screen_context_needed(text))

    def test_non_screen_text_skips_screen_frame(self):
        for text in (
            "merhaba",
            "ekran kartı sürücüsünü güncelle",
            "ekranı kilitle",
            "tam ekran kamera",
            "bana bir uygulama öner",
            "github commitlerini özetle",
        ):
            with self.subTest(text=text):
                self.assertFalse(screen_context_needed(text))

    def test_screen_voice_toggle_phrases(self):
        cases = {
            "ekran bağlamını aç": "on",
            "Ekran bağlamını başlat": "on",
            "ekran bağlamını açar mısın": "on",
            "ekran bağlamını kapat": "off",
            "ekran izlemeyi kapatır mısın": "off",
            "ekran bağlamını kapatma": None,
            "ekranımda ne var": None,
            "ekranımı görüyor musun": None,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(screen_voice_intent(text), expected)


class _PlannerAI:
    def __init__(self, plan=None, error=None):
        self.plan = plan
        self.error = error
        self.prompts = []

    def plan_actions(self, prompt):
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return self.plan


class _WorkerStub:
    _plan = None
    _attachments = ()
    _camera_image = None
    _screen_image = None
    FAST_MAX_WORDS = AIWorker.FAST_MAX_WORDS
    FAST_CONNECTORS = AIWorker.FAST_CONNECTORS
    FAST_ACTION_TYPES = AIWorker.FAST_ACTION_TYPES

    def __init__(self, prompt, ai):
        self._prompt = prompt
        self.ai = ai


def _application_plan():
    return ActionPlan([
        PlannedAction("open_application", {"application": "notepad"}, "Not Defteri'ni aç")
    ])


def _delete_plan():
    return ActionPlan([
        PlannedAction("delete_path", {"path": "C:/tmp/x.txt"}, "Dosyayı sil", "destructive")
    ])


class LocalPlanTests(unittest.TestCase):
    def make_stub(self, prompt, plan=None, error=None):
        return _WorkerStub(prompt, _PlannerAI(plan=plan, error=error))

    def test_short_safe_command_uses_local_plan(self):
        stub = self.make_stub("not defteri aç", _application_plan())
        plan = AIWorker._local_plan(stub)
        self.assertIsNotNone(plan)
        self.assertEqual(plan.actions[0].action_type, "open_application")
        self.assertEqual(stub.ai.prompts, ["not defteri aç"])

    def test_real_planner_matches_fast_path(self):
        class AIWithPlanner:
            def __init__(self):
                self.planner = ActionPlanner()

            def plan_actions(self, prompt):
                return self.planner.plan(prompt)

        stub = self.make_stub("not defteri aç")
        stub.ai = AIWithPlanner()
        plan = AIWorker._local_plan(stub)
        self.assertIsNotNone(plan)
        self.assertEqual(plan.actions[0].action_type, "open_application")

    def test_destructive_actions_are_not_fast_tracked(self):
        stub = self.make_stub("x dosyasını sil", _delete_plan())
        self.assertIsNone(AIWorker._local_plan(stub))

    def test_overwrite_actions_stay_in_approval_flow(self):
        plan = ActionPlan([
            PlannedAction(
                "write_text_file",
                {"path": "C:/Users/x/Desktop/Jarvis_Notu.txt", "content": "x", "overwrite": True},
                "Notu yaz",
            )
        ])
        stub = self.make_stub("not defterine x yaz", plan)
        self.assertIsNone(AIWorker._local_plan(stub))

    def test_camera_attachment_and_long_prompts_stay_on_ai_path(self):
        stub = self.make_stub("not defteri aç", _application_plan())
        stub._camera_image = "C:/tmp/frame.jpg"
        self.assertIsNone(AIWorker._local_plan(stub))
        stub._camera_image = None
        stub._screen_image = "C:/tmp/screen.jpg"
        self.assertIsNone(AIWorker._local_plan(stub))
        stub._screen_image = None
        stub._attachments = ["C:/tmp/doc.pdf"]
        self.assertIsNone(AIWorker._local_plan(stub))
        stub._attachments = ()
        stub._prompt = "not defteri aç ve içine yarın yapılacaklar listesini yaz"
        self.assertIsNone(AIWorker._local_plan(stub))

    def test_multi_step_commands_stay_on_ai_path(self):
        stub = self.make_stub("not defteri aç sonra hesap makinesi aç", _application_plan())
        self.assertIsNone(AIWorker._local_plan(stub))

    def test_empty_plan_and_planner_errors_are_ignored(self):
        stub = self.make_stub("not defteri aç", ActionPlan([]))
        self.assertIsNone(AIWorker._local_plan(stub))
        stub = self.make_stub("not defteri aç", error=RuntimeError("boom"))
        self.assertIsNone(AIWorker._local_plan(stub))
        stub = self.make_stub("not defteri aç", None)
        self.assertIsNone(AIWorker._local_plan(stub))

    def test_explicit_plan_takes_precedence(self):
        explicit = _application_plan()
        stub = self.make_stub("uzun bir metin", None)
        stub._plan = explicit
        self.assertIs(AIWorker._local_plan(stub), explicit)


if __name__ == "__main__":
    unittest.main()
