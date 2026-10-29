import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWidgets import QApplication

from app.core.settings import Settings
from app.core.settings import VOICE_STYLES as SETTINGS_VOICE_STYLES
from app.core.speech import (
    SpeechService,
    VOICE_REPLY_CHARS,
    VOICE_STYLES as SPEECH_VOICE_STYLES,
    clean_for_speech,
    concise_for_voice,
    plan_chunks,
    split_for_speech,
    spoken_limit_for_prompt,
    style_label,
    style_parts,
    style_rate_offset,
)
from app.core.voice import (
    ConversationWorker,
    _normalize_wake,
    _pick_transcript,
    should_accept_barge,
)

APP = QApplication.instance() or QApplication([])


class AudioSettingsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        env = patch.dict(os.environ, {"LOCALAPPDATA": self._tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.settings = Settings()

    def test_audio_roundtrip(self):
        self.settings.set_audio(
            mic_device="  USB Mikrofon  ",
            speaker_device="Kulaklık",
            camera_index=2,
            voice_style="male",
            voice_rate=-7,
            voice_volume=55,
            headphones=False,
        )
        reloaded = Settings()
        self.assertEqual(reloaded.mic_device, "USB Mikrofon")
        self.assertEqual(reloaded.speaker_device, "Kulaklık")
        self.assertEqual(reloaded.camera_index, 2)
        self.assertEqual(reloaded.voice_style, "male")
        self.assertEqual(reloaded.voice_rate, -7)
        self.assertEqual(reloaded.voice_volume, 55)
        self.assertFalse(reloaded.voice_headphones)

    def test_audio_values_are_clamped(self):
        self.settings.set_audio(
            camera_index=99,
            voice_style="bilinmeyen",
            voice_rate=-99,
            voice_volume=250,
        )
        self.assertEqual(self.settings.camera_index, 16)
        self.assertEqual(self.settings.voice_style, "jarvis")
        self.assertEqual(self.settings.voice_rate, -20)
        self.assertEqual(self.settings.voice_volume, 100)


class SpeechChunkTests(unittest.TestCase):
    def test_long_answer_is_split_into_speakable_chunks(self):
        text = " ".join(["Jarvis bu cümleyi akıcı biçimde okuyacak."] * 14)
        chunks = split_for_speech(text, max_chars=120)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(0 < len(chunk) <= 120 for chunk in chunks))
        self.assertEqual(" ".join(chunks), clean_for_speech(text))

    def test_markdown_and_links_are_removed(self):
        cleaned = clean_for_speech("**Merhaba** [bağlantı](https://example.com) `kod`")
        self.assertEqual(cleaned, "Merhaba bağlantı kod")

    def test_styles_fall_back_safely(self):
        self.assertEqual(style_parts("yok")[0], style_parts("jarvis")[0])
        self.assertTrue(style_label("jarvis"))
        self.assertTrue(style_label("warm"))
        self.assertTrue(style_label("male"))

    def test_style_keys_match_settings(self):
        self.assertEqual(set(SPEECH_VOICE_STYLES), set(SETTINGS_VOICE_STYLES))

    def test_concise_voice_reply_keeps_first_sentences(self):
        text = (
            "Bugün hava açık ve 24 derece. Öğleden sonra hafif rüzgar var. "
            "Akşam ise serinleyecek ve 16 dereceye düşecek. Bu ayrıntı sohbette kalmalı."
        )
        short = concise_for_voice(text, max_chars=90)
        self.assertLessEqual(len(short), 90)
        self.assertTrue(short.startswith("Bugün hava açık"))
        self.assertNotIn("sohbette", short)
        self.assertEqual(clean_for_speech(text)[:20], clean_for_speech(text[:20]))

    def test_plan_chunks_keeps_first_chunk_short(self):
        text = (
            "Yarın sabah erken saatlerde başlayacak yoğun kar yağışı nedeniyle "
            "okullar tatil edildi ve ulaşım aksadı. " + "Detaylar akşam bülteninde."
        )
        chunks = plan_chunks(text, first_chars=60, max_chars=120)
        self.assertGreater(len(chunks), 1)
        self.assertLessEqual(len(chunks[0]), 60)
        self.assertTrue(all(len(chunk) <= 120 for chunk in chunks))

    def test_spoken_limit_respects_narration_requests(self):
        self.assertEqual(spoken_limit_for_prompt("Bugün hava nasıl?"), VOICE_REPLY_CHARS)
        self.assertEqual(spoken_limit_for_prompt("Okullar tatil mi?"), VOICE_REPLY_CHARS)
        self.assertIsNone(spoken_limit_for_prompt("Haberleri anlat"))
        self.assertIsNone(spoken_limit_for_prompt("Hepsini oku"))
        self.assertIsNone(spoken_limit_for_prompt("Detaylı açıkla"))
        self.assertIsNone(spoken_limit_for_prompt("Bunu okur musun"))

    def test_barge_in_rejects_noise_but_accepts_stop(self):
        self.assertFalse(should_accept_barge("Heij"))
        self.assertFalse(should_accept_barge("hmm"))
        self.assertTrue(should_accept_barge("dur"))
        self.assertTrue(should_accept_barge("kapat şunu"))
        self.assertTrue(should_accept_barge("yarın hava nasıl"))


class WakeWordTests(unittest.TestCase):
    def test_wake_word_variants_become_one_name(self):
        self.assertEqual(_normalize_wake("hejiyan biz nasılsın"), "Jarvis nasılsın")
        self.assertEqual(_normalize_wake("hey carvis naber"), "Jarvis naber")
        self.assertEqual(_normalize_wake("hey korves nesin"), "Jarvis nesin")
        self.assertEqual(_normalize_wake("jarvıs nasılsın"), "Jarvis nasılsın")
        self.assertEqual(_normalize_wake("merhaba jarvis"), "merhaba Jarvis")

    def test_plain_sentence_is_untouched(self):
        self.assertEqual(_normalize_wake("yarın hava nasıl"), "yarın hava nasıl")

    def test_pick_transcript_prefers_alternative_with_wake_word(self):
        payload = {"alternative": [
            {"transcript": "biz nasılsın"},
            {"transcript": "hejiyan biz nasılsın"},
        ]}
        self.assertEqual(_pick_transcript(payload), "hejiyan biz nasılsın")
        payload["alternative"].append({"transcript": "hey jarvis nasılsın"})
        self.assertEqual(_pick_transcript(payload), "hey jarvis nasılsın")
        self.assertEqual(_pick_transcript({"alternative": [{"transcript": "nasılsın"}]}), "nasılsın")
        self.assertEqual(_pick_transcript("düz metin"), "düz metin")
        self.assertEqual(_pick_transcript({}), "")


class SpeechServiceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        env = patch.dict(os.environ, {"LOCALAPPDATA": self._tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.settings = Settings()

    def test_player_stays_lazy_until_speech(self):
        service = SpeechService(self.settings)
        self.assertIsNone(service._player)
        service.apply_settings()
        self.assertIsNone(service._player)
        self.assertFalse(service.busy)
        service.shutdown()

    def test_speak_synthesizes_in_background_and_caps_text(self):
        service = SpeechService(self.settings)
        self.addCleanup(service.shutdown)
        text = "İlk cümle burada. " + " ".join(["Uzun ayrıntı cümlesi."] * 20)
        with patch.object(SpeechService, "_ensure_player", lambda self: None), patch(
            "app.core.speech.synthesize", lambda *a, **k: __import__("pathlib").Path(__file__)
        ):
            service.speak(text, max_chars=60)
            self.assertTrue(service.busy)
            self.assertLessEqual(len(service.spoken_text), 60)
            deadline = time.monotonic() + 2.0
            while not service._synth_done and time.monotonic() < deadline:
                APP.processEvents()
                time.sleep(0.01)
        self.assertTrue(service._synth_done)
        self.assertGreaterEqual(len(service._chunks), 1)

    def test_pending_chunk_blocks_premature_finish(self):
        from pathlib import Path

        class IdlePlayer:
            def playbackState(self):
                return 0

            def stop(self):
                pass

        service = SpeechService(self.settings)
        self.addCleanup(service.shutdown)
        service._player = IdlePlayer()
        service._active = True
        service._pending_play = True
        service._synth_done = True
        service._paths = {1: Path("bekleyen.mp3")}
        service._pump()
        self.assertTrue(service.busy)
        self.assertEqual(service._paths, {1: Path("bekleyen.mp3")})

    def test_jarvis_style_runs_faster_than_saved_rate(self):
        service = SpeechService(self.settings)
        self.addCleanup(service.shutdown)
        self.assertEqual(service.voice_style, "jarvis")
        self.assertEqual(service._rate_text(), "+21%")
        self.assertGreater(style_rate_offset("jarvis"), style_rate_offset("natural"))

    def test_shutdown_does_not_wait_for_slow_synthesis(self):
        service = SpeechService(self.settings)
        release = threading.Event()
        self.addCleanup(release.set)

        def slow(*args, **kwargs):
            release.wait(5.0)
            raise RuntimeError("iptal")

        with patch.object(SpeechService, "_ensure_player", lambda self: None), patch(
            "app.core.speech.synthesize", slow
        ):
            service.speak("Kısa bir cümle.")
            started = time.monotonic()
            service.shutdown()
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.5)
        self.assertFalse(service.busy)


class ConversationWorkerTests(unittest.TestCase):
    def test_flags_and_stop_are_non_blocking(self):
        worker = ConversationWorker(device_name="Mikrofon", barge_in=True)
        worker.set_speaking(True)
        self.assertTrue(worker._speaking.is_set())
        worker.set_speaking(False)
        self.assertFalse(worker._speaking.is_set())
        worker.set_paused(True)
        self.assertTrue(worker._paused.is_set())
        worker.stop()
        self.assertTrue(worker._stop_event.is_set())


    def test_listen_emits_live_levels_and_returns_audio(self):
        import math
        import struct

        rate = 16000
        chunk = 1024
        loud = b"".join(
            struct.pack("<h", int(9000 * math.sin(2 * math.pi * 220 * index / rate)))
            for index in range(int(rate * 0.9))
        )
        payload = b"\x00\x00" * int(rate * 0.45) + loud + b"\x00\x00" * int(rate * 0.9)

        class Stream:
            def __init__(self, data: bytes) -> None:
                self.data = data
                self.pos = 0

            def read(self, size: int) -> bytes:
                block = self.data[self.pos:self.pos + size]
                self.pos += len(block)
                return block

        class Source:
            CHUNK = chunk
            SAMPLE_RATE = rate
            SAMPLE_WIDTH = 2

            def __init__(self, data: bytes) -> None:
                self.stream = Stream(data)

        worker = ConversationWorker(device_name="test", barge_in=False)
        levels = []
        worker.level.connect(levels.append)
        audio = worker._listen(
            Source(payload), threshold=500, timeout=1.0, phrase_limit=5.0, pause_threshold=0.45
        )
        self.assertGreater(len(audio.frame_data), 0)
        self.assertEqual(audio.sample_rate, rate)
        self.assertGreater(len(levels), 10)
        self.assertGreater(max(levels), 0.5)

    def test_noisy_room_finishes_phrase_without_waiting_for_limit(self):
        import math
        import struct

        from speech_recognition import WaitTimeoutError

        rate = 16000

        def tone(seconds: float, amplitude: int) -> bytes:
            return b"".join(
                struct.pack("<h", int(amplitude * math.sin(2 * math.pi * 220 * i / rate)))
                for i in range(int(rate * seconds))
            )

        class Stream:
            def __init__(self, data: bytes) -> None:
                self.data = data
                self.pos = 0

            def read(self, size: int) -> bytes:
                block = self.data[self.pos:self.pos + size]
                self.pos += len(block)
                return block

        class Source:
            CHUNK = 1024
            SAMPLE_RATE = rate
            SAMPLE_WIDTH = 2

            def __init__(self, data: bytes) -> None:
                self.stream = Stream(data)

        worker = ConversationWorker(device_name="test", barge_in=False)
        # Oda gürültüsü ayarlanan eşiğin üstünde ve tek düze: cümle sanılıp
        # 5 saniyelik limite kadar açık kalmamalı.
        with self.assertRaises(WaitTimeoutError):
            worker._listen(
                Source(tone(2.5, 1270)),
                threshold=500,
                timeout=2.5,
                phrase_limit=5.0,
                pause_threshold=0.4,
            )
        self.assertGreater(worker._noise_floor, 600)

        # Taban yükseldikten sonra kısa cümle gürültüye rağmen hemen kapanır.
        phrase = tone(0.6, 1270) + tone(0.5, 4200) + tone(1.0, 1270)
        audio = worker._listen(
            Source(phrase),
            threshold=500,
            timeout=1.0,
            phrase_limit=5.0,
            pause_threshold=0.4,
        )
        duration = len(audio.frame_data) / (rate * 2)
        self.assertGreater(duration, 0.5)
        self.assertLess(duration, 1.6)


class FakeConversationWorker(QObject):
    heard = Signal(str)
    error = Signal(str)
    finished = Signal()
    level = Signal(float)

    def __init__(self, device_name: str = "", barge_in: bool = True) -> None:
        super().__init__()
        self.device_name = device_name
        self.barge_in = barge_in
        self._stop = threading.Event()

    @Slot()
    def run(self) -> None:
        self._stop.wait(0.4)
        self.finished.emit()

    def set_speaking(self, speaking: bool, spoken_text: str = "") -> None:
        pass

    def stop(self) -> None:
        self._stop.set()


class MicWiringTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        env = patch.dict(os.environ, {"LOCALAPPDATA": self._tmp.name})
        env.start()
        self.addCleanup(env.stop)

    def test_mic_starts_worker_without_opening_camera(self):
        from app.ui.main_window import MainWindow

        worker_patch = patch("app.ui.main_window.ConversationWorker", FakeConversationWorker)
        worker_patch.start()
        self.addCleanup(worker_patch.stop)
        window = MainWindow()
        self.addCleanup(window.close)
        window._camera_perception = True
        window.toggle_mic()
        APP.processEvents()
        self.assertTrue(window.mic_enabled)
        self.assertIsNotNone(window.voice_thread)
        self.assertIsNone(window.camera_window)
        window.toggle_mic()
        self.assertFalse(window.mic_enabled)
        deadline = time.monotonic() + 2.0
        while window.voice_thread is not None and time.monotonic() < deadline:
            APP.processEvents()
            time.sleep(0.02)
        self.assertIsNone(window.voice_thread)


    def test_voice_result_is_delivered_on_gui_thread(self):
        from PySide6.QtCore import QThread

        from app.ui.main_window import MainWindow

        captured = {}

        class EmittingWorker(FakeConversationWorker):
            level = Signal(float)

            def run(self) -> None:
                self.level.emit(0.7)
                self.heard.emit("merhaba")
                self.finished.emit()

        worker_patch = patch("app.ui.main_window.ConversationWorker", EmittingWorker)
        worker_patch.start()
        self.addCleanup(worker_patch.stop)
        window = MainWindow()
        self.addCleanup(window.close)
        window.send_message = lambda text, from_voice=False: captured.update(
            text=text, gui=QThread.currentThread() is APP.thread()
        )
        window.mic_enabled = True
        window._start_listening()
        deadline = time.monotonic() + 2.0
        while "text" not in captured and time.monotonic() < deadline:
            APP.processEvents()
            time.sleep(0.02)
        APP.processEvents()
        self.assertEqual(captured.get("text"), "merhaba")
        self.assertTrue(captured.get("gui"))

    def test_typed_reply_is_spoken_until_the_header_toggle_mutes_it(self):
        from app.ui.main_window import MainWindow

        window = MainWindow()

        def _close():
            window.request_active = False
            window.close()

        self.addCleanup(_close)
        spoken = []
        stopped = []
        window.speech.speak = lambda text, max_chars=None: spoken.append(text)
        window.speech.stop = lambda: stopped.append(True)
        self.assertTrue(window.speak_replies)
        self.assertIn("Ses açık", window.speak_status.text())
        window.request_active = True
        window._on_response("Merhaba, iyiyim.")
        self.assertEqual(spoken, ["Merhaba, iyiyim."])
        window.toggle_voice_replies()
        self.assertFalse(window.speak_replies)
        self.assertIn("Ses kapalı", window.speak_status.text())
        self.assertEqual(stopped, [True])
        window._on_response("İkinci yanıt.")
        self.assertEqual(spoken, ["Merhaba, iyiyim."])
        self.assertFalse(Settings().voice_replies)


if __name__ == "__main__":
    unittest.main()
