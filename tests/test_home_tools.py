import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")

from PySide6.QtWidgets import QApplication, QWidget

from app.core.agent import ActionExecutor
from app.core.home_tools import (
    FILTER_SECONDS_PER_CUP,
    TURK_BREW_SECONDS,
    SmartHome,
    home_voice_intent,
)
from app.ui.home_modal import HomeModal
from app.ui.main_window import MainWindow

APP = QApplication.instance() or QApplication([])


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class LightTests(unittest.TestCase):
    def setUp(self):
        self.home = SmartHome(FakeClock())

    def test_lights_start_off_and_turn_on_with_brightness(self):
        light = self.home.light("salon")
        self.assertFalse(light.on)
        self.home.set_light("salon", brightness=40)
        self.assertTrue(light.on)
        self.assertEqual(light.brightness, 40)

    def test_zero_brightness_turns_light_off_and_tone_is_clamped(self):
        self.home.set_light("calisma", on=True, brightness=80, tone="soguk")
        self.home.set_light("calisma", brightness=0)
        self.assertFalse(self.home.light("calisma").on)
        self.home.set_light("calisma", tone="mor")
        self.assertEqual(self.home.light("calisma").tone, "soguk")
        self.home.set_light("calisma", brightness=140)
        self.assertEqual(self.home.light("calisma").brightness, 100)

    def test_all_lights_share_brightness_and_switch_off(self):
        self.home.set_all_lights(on=True, brightness=25, tone="sicak")
        self.assertTrue(all(light.on for light in self.home.lights.values()))
        self.assertEqual({light.brightness for light in self.home.lights.values()}, {25})
        self.home.set_all_lights(on=False)
        self.assertFalse(any(light.on for light in self.home.lights.values()))


class CoffeeTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.home = SmartHome(self.clock)

    def test_turkish_coffee_brews_for_75_seconds(self):
        ok, coffee, seconds = self.home.brew("turk", foam="bol", sugar="az")
        self.assertTrue(ok)
        self.assertEqual(seconds, TURK_BREW_SECONDS)
        self.assertEqual(coffee.foam, "bol")
        self.assertEqual(coffee.sugar, "az")
        self.assertIn("demleniyor", coffee.describe(self.clock.now))
        self.clock.now += TURK_BREW_SECONDS - 5
        self.assertFalse(self.home.coffee("turk").ready)
        self.clock.now += 5
        ready = self.home.coffee("turk")
        self.assertTrue(ready.ready)
        self.assertIn("hazır", ready.describe(self.clock.now))
        self.assertIn("Bol köpüklü", ready.describe(self.clock.now))

    def test_filter_coffee_scales_with_cups_and_blocks_second_brew(self):
        ok, coffee, seconds = self.home.brew("filtre", cups=3)
        self.assertTrue(ok)
        self.assertEqual(coffee.cups, 3)
        self.assertEqual(seconds, FILTER_SECONDS_PER_CUP * 3)
        again, same, _ = self.home.brew("filtre", cups=4)
        self.assertFalse(again)
        self.assertIs(same, coffee)
        self.clock.now += seconds
        self.assertTrue(self.home.coffee("filtre").ready)
        again, _, _ = self.home.brew("filtre", cups=4)
        self.assertTrue(again)
        self.assertEqual(self.home.coffee("filtre").cups, 4)

    def test_cancel_resets_coffee(self):
        self.home.brew("turk")
        self.home.cancel_coffee("turk")
        self.assertFalse(self.home.coffee("turk").ready)
        self.assertIsNone(self.home.coffee("turk").started_at)
        self.assertIn("boş", self.home.coffee("turk").describe(self.clock.now))


class HomeVoiceIntentTests(unittest.TestCase):
    def test_light_commands_are_parsed(self):
        cases = {
            "salonun ışığını aç": ("salon", True, None, None),
            "lambayı kapat": ("salon", False, None, None),
            "tüm ışıkları kapat": ("all", False, None, None),
            "çalışma odasını yüzde otuz yap soğuk beyaz": ("calisma", None, 30, "soguk"),
            "salon ışığını %40 yap": ("salon", None, 40, None),
        }
        for text, (room, on, brightness, tone) in cases.items():
            with self.subTest(text=text):
                intent = home_voice_intent(text)
                self.assertEqual(intent["action"], "light")
                self.assertEqual(intent["room"], room)
                self.assertEqual(intent["on"], on)
                self.assertEqual(intent["brightness"], brightness)
                self.assertEqual(intent["tone"], tone)

    def test_light_status_and_panel(self):
        intent = home_voice_intent("ışıklar açık mı")
        self.assertEqual(intent, {"action": "light_status", "room": "all"})
        self.assertEqual(home_voice_intent("ev panelini aç"), {"action": "panel"})

    def test_coffee_commands_are_parsed(self):
        self.assertEqual(
            home_voice_intent("bana bol köpüklü bir türk kahvesi yap"),
            {"action": "coffee_brew", "kind": "turk", "foam": "bol"},
        )
        self.assertEqual(
            home_voice_intent("filtre kahveyi 3 fincan demle"),
            {"action": "coffee_brew", "kind": "filtre", "cups": 3},
        )
        self.assertEqual(home_voice_intent("kahvem hazır mı")["action"], "coffee_status")
        self.assertEqual(home_voice_intent("kahveyi iptal et")["action"], "coffee_cancel")

    def test_unrelated_or_ambiguous_text_is_left_to_ai(self):
        for text in (
            "ışık nedir", "bugün hava nasıl", "kahve içmek sağlıklı mı",
            "salonun ışığını aç ve kahve yap", "ışıkları aç sonra kahve yap",
        ):
            with self.subTest(text=text):
                self.assertIsNone(home_voice_intent(text))


class _Chat:
    def __init__(self):
        self.items = []

    def add_message(self, text, user):
        self.items.append((text, bool(user)))


class _Speech:
    def __init__(self):
        self.spoken = []

    def speak(self, text):
        self.spoken.append(text)


class _Progress:
    def __init__(self):
        self.visible = True

    def hide(self):
        self.visible = False


class _HomeHandler:
    def __init__(self):
        self.clock = FakeClock()
        self.smart_home = SmartHome(self.clock)
        self.chat = _Chat()
        self.speech = _Speech()
        self.progress_label = _Progress()
        self._modal = None
        self.states = []
        self.panel_opens = 0

    def _set_state(self, state):
        self.states.append(state)

    def open_home(self):
        self.panel_opens += 1

    _apply_home_light = MainWindow._apply_home_light
    _describe_home_lights = MainWindow._describe_home_lights
    _brew_home_coffee = MainWindow._brew_home_coffee


class HomeHandlerTests(unittest.TestCase):
    def setUp(self):
        self.handler = _HomeHandler()

    def _handle(self, text, speak=False):
        return MainWindow._handle_home_voice_command(self.handler, text, speak=speak)

    def test_light_command_updates_state_and_replies(self):
        self.assertTrue(self._handle("salonun ışığını aç"))
        self.assertTrue(self.handler.smart_home.light("salon").on)
        self.assertIn("açık", self.handler.chat.items[-1][0].lower())
        self.assertEqual(self.handler.states[-1], "idle")
        self.assertFalse(self.handler.progress_label.visible)

    def test_dim_and_tone_command(self):
        self.assertTrue(self._handle("çalışma odasını yüzde otuz yap soğuk beyaz"))
        light = self.handler.smart_home.light("calisma")
        self.assertEqual(light.brightness, 30)
        self.assertEqual(light.tone, "soguk")
        self.assertIn("%30", self.handler.chat.items[-1][0])

    def test_coffee_brew_status_and_cancel(self):
        self.assertTrue(self._handle("filtre kahveyi 2 fincan demle"))
        self.assertIn("demleniyor", self.handler.chat.items[-1][0].lower())
        self.assertTrue(self._handle("kahvem hazır mı"))
        self.assertTrue(self._handle("kahveyi iptal et"))
        self.assertIn("iptal", self.handler.chat.items[-1][0].lower())

    def test_panel_command_opens_home(self):
        self.assertTrue(self._handle("ev panelini aç"))
        self.assertEqual(self.handler.panel_opens, 1)
        self.assertIn("panel", self.handler.chat.items[-1][0].lower())

    def test_speak_sets_speaking_state(self):
        self.assertTrue(self._handle("tüm ışıkları kapat", speak=True))
        self.assertEqual(self.handler.states[-1], "speaking")
        self.assertTrue(self.handler.speech.spoken)

    def test_unrelated_text_is_ignored(self):
        self.assertFalse(self._handle("bugün hava nasıl"))
        self.assertEqual(self.handler.chat.items, [])


class HomeToolTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.executor = type("HomeExecutor", (), {})()
        self.executor.home = SmartHome(self.clock)
        self.executor._run_home = ActionExecutor._run_home.__get__(self.executor)

    def run_home(self, **args):
        return self.executor._run_home(args)

    def test_status_reports_lights_and_coffees(self):
        result = self.run_home(action="status")
        self.assertIn("Salon ışığı: kapalı", result)
        self.assertIn("Türk kahvesi: makine boş", result)
        self.assertIn("simülasyon", result)

    def test_light_and_all_lights(self):
        result = self.run_home(action="light", room="calisma", on=True, brightness=35, tone="soguk")
        light = self.executor.home.light("calisma")
        self.assertTrue(light.on)
        self.assertEqual(light.brightness, 35)
        self.assertEqual(light.tone, "soguk")
        self.assertIn("%35", result)
        self.run_home(action="all_lights", on=False)
        self.assertFalse(any(item.on for item in self.executor.home.lights.values()))
        self.assertIn("gerekli", self.run_home(action="light"))

    def test_coffee_brew_status_cancel_and_duplicate(self):
        result = self.run_home(action="coffee_brew", kind="filtre", cups=3)
        self.assertIn("120 sn", result)
        self.assertIn("3 fincan", result)
        duplicate = self.run_home(action="coffee_brew", kind="filtre", cups=1)
        self.assertIn("zaten demleniyor", duplicate)
        status = self.run_home(action="coffee_status", kind="filtre")
        self.assertIn("demleniyor", status)
        cancelled = self.run_home(action="coffee_cancel", kind="filtre")
        self.assertIn("iptal", cancelled)
        self.assertIn("boş", self.run_home(action="coffee_status", kind="filtre"))


class HomeModalTests(unittest.TestCase):
    def setUp(self):
        self.parent = QWidget()
        self.parent.resize(1200, 800)
        self.parent.show()
        self.clock = FakeClock()
        self.home = SmartHome(self.clock)
        self.modal = HomeModal(self.parent, self.home)
        self.modal.show()
        APP.processEvents()

    def tearDown(self):
        self.modal.close_modal()
        self.parent.close()
        APP.processEvents()

    def test_light_card_follows_state(self):
        self.home.set_light("salon", on=True, brightness=35, tone="soguk")
        self.modal.refresh()
        state = self.modal.light_widgets["salon"]["state"].text()
        self.assertIn("%35", state)
        self.assertIn("SOĞUK", state)
        self.assertTrue(self.modal.light_widgets["salon"]["tones"]["soguk"].isChecked())
        self.assertFalse(self.modal.grab().isNull())

    def test_coffee_card_shows_progress_and_disables_brew(self):
        self.modal.coffee_widgets["turk"]["foam"].setCurrentIndex(2)
        self.modal._brew("turk")
        state = self.modal.coffee_widgets["turk"]["state"].text()
        self.assertIn("DEMLEN", state)
        self.assertFalse(self.modal.coffee_widgets["turk"]["brew"].isEnabled())
        self.clock.now += TURK_BREW_SECONDS
        self.modal.refresh()
        self.assertIn("HAZIR", self.modal.coffee_widgets["turk"]["state"].text())
        self.assertTrue(self.modal.coffee_widgets["turk"]["brew"].isEnabled())

    def test_all_off_button_turns_every_light_off(self):
        self.home.set_all_lights(on=True)
        self.modal._all_lights_off()
        self.assertFalse(any(light.on for light in self.home.lights.values()))
        self.assertIn("KAPALI", self.modal.light_widgets["calisma"]["state"].text())


if __name__ == "__main__":
    unittest.main()
