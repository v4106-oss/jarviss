"""Mikrofon seçimi ve kesintisiz Türkçe konuşma oturumu."""

from __future__ import annotations

import inspect
import math
import re
import threading
import time
from collections import deque
from typing import List, Optional, Tuple

from PySide6.QtCore import QObject, Signal, Slot

_HOST_ORDER = ("wasapi", "directsound", "mme", "wdm-ks")
_device_cache: Tuple[float, List[Tuple[int, str]], str] = (0.0, [], "")
_STOP_WORDS = {
    "dur",
    "sus",
    "kes",
    "iptal",
    "bekle",
    "kapat",
    "yeter",
    "hayir",
    "tamam",
    "sessiz",
}


def _fold(text: str) -> str:
    return (
        str(text or "")
        .casefold()
        .replace("ı", "i")
        .replace("ş", "s")
        .replace("ğ", "g")
        .replace("ü", "u")
        .replace("ö", "o")
        .replace("ç", "c")
    )


def _words(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", _fold(text))


def should_accept_barge(text: str) -> bool:
    """Jarvis konuşurken tek kelimelik gürültüyü/yankıyı söz kesme sayma."""
    words = _words(text)
    if not words:
        return False
    if len(words) >= 2:
        return True
    return words[0] in _STOP_WORDS


# Google Türkçe modeli "Jarvis" adını sık sık "carvis", "jarvıs" ya da
# "hejiyan" gibi yazıyor; bu yazımları tek bir ada çeviriyoruz.
_WAKE_ROOTS = ("jarv", "carv", "jarw", "carw", "jerv", "corv", "korv", "curv")
_WAKE_SPELLINGS = {"hejiyan", "heycan", "heyjan", "hejyan", "hecyan"}
_HEY_WORDS = {"hey", "hej", "hi"}


def _wake_like(token: str) -> bool:
    folded = re.sub(r"[^a-z0-9]", "", _fold(token))
    if not folded:
        return False
    if folded in _WAKE_SPELLINGS:
        return True
    return any(folded.startswith(root) for root in _WAKE_ROOTS)


def _normalize_wake(text: str) -> str:
    tokens = str(text or "").split()
    if not tokens:
        return str(text or "")
    for index, token in enumerate(tokens):
        if not _wake_like(token):
            continue
        start = index
        if index and _fold(tokens[index - 1]).strip(".,!?") in _HEY_WORDS:
            start = index - 1
        tokens = tokens[:start] + ["Jarvis"] + tokens[index + 1:]
        break
    if len(tokens) > 2 and tokens[0] == "Jarvis":
        # "hejiyan biz nasılsın": "biz" ses tanımanın Jarvis ekidir.
        if _fold(tokens[1]).strip(".,!?") == "biz" and _fold(tokens[2]).startswith("nasil"):
            tokens = [tokens[0]] + tokens[2:]
    return " ".join(tokens)


def _pick_transcript(payload) -> str:
    """show_all yanıtından uyanma kelimesini içeren en iyi alternatifi seçer."""
    if isinstance(payload, str):
        return payload.strip()
    if not isinstance(payload, dict):
        return ""
    alternatives = payload.get("alternative")
    if not isinstance(alternatives, list):
        return str(payload.get("transcript") or "").strip()
    texts = [
        str(item.get("transcript") or "").strip()
        for item in alternatives
        if isinstance(item, dict)
    ]
    texts = [text for text in texts if text]
    if not texts:
        return ""
    for text in texts:
        if "jarv" in _fold(text):
            return text
    for text in texts:
        if any(_wake_like(token) for token in text.split()):
            return text
    return texts[0]


def _audio_hosts(audio) -> List[str]:
    try:
        return [
            str(audio.get_host_api_info_by_index(index).get("name") or "").casefold()
            for index in range(audio.get_host_api_count())
        ]
    except Exception:
        return []


def _host_rank(name: str) -> int:
    folded = str(name or "").casefold()
    for index, key in enumerate(_HOST_ORDER):
        if key in folded:
            return index
    return len(_HOST_ORDER)


def microphone_devices(refresh: bool = False) -> Tuple[List[Tuple[int, str]], str]:
    """Giriş aygıtlarını (PortAudio indeksi, ad) ve varsayılan aygıt adını döndürür."""
    global _device_cache
    now = time.monotonic()
    if not refresh and now - _device_cache[0] < 4.0:
        return list(_device_cache[1]), _device_cache[2]
    try:
        import pyaudio
    except Exception:
        return [], ""
    audio = pyaudio.PyAudio()
    devices: List[Tuple[int, str]] = []
    default_name = ""
    try:
        hosts = _audio_hosts(audio)
        candidates = []
        for index in range(audio.get_device_count()):
            try:
                info = audio.get_device_info_by_index(index)
            except Exception:
                continue
            if int(info.get("maxInputChannels") or 0) <= 0:
                continue
            name = str(info.get("name") or f"Mikrofon {index}").strip()
            if not name:
                continue
            host = hosts[int(info.get("hostApi") or 0)] if hosts else ""
            candidates.append((_host_rank(host), index, name))
        candidates.sort(key=lambda item: (item[0], item[1]))
        seen = set()
        for _, index, name in candidates:
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            devices.append((index, name))
        try:
            default_name = str(audio.get_default_input_device_info().get("name") or "").strip()
        except Exception:
            default_name = ""
    finally:
        audio.terminate()
    _device_cache = (now, list(devices), default_name)
    return devices, default_name


def resolve_microphone_index(name: str) -> Optional[int]:
    """Kayıtlı aygıt adını PortAudio indeksine çevirir; boşsa sistem varsayılanı."""
    wanted = str(name or "").strip().casefold()
    if not wanted:
        return None
    devices, _ = microphone_devices()
    for index, label in devices:
        if label.casefold() == wanted:
            return index
    for index, label in devices:
        if label.casefold().startswith(wanted) or wanted.startswith(label.casefold()):
            return index
    return None


def listen_turkish(
    timeout: int = 8,
    phrase_time_limit: int = 20,
    device_index: Optional[int] = None,
) -> str:
    """Mikrofondan kısa bir Türkçe cümle dinler ve metne çevirir."""
    try:
        import speech_recognition as sr
    except ImportError:
        raise RuntimeError("Ses tanıma bileşeni kurulu değil.")

    recognizer = sr.Recognizer()
    kwargs = {"device_index": int(device_index)} if device_index is not None else {}
    try:
        with sr.Microphone(**kwargs) as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.5)
            audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_time_limit)
        payload = recognizer.recognize_google(audio, language="tr-TR", show_all=True)
        return _normalize_wake(_pick_transcript(payload))
    except sr.WaitTimeoutError as error:
        raise RuntimeError("Mikrofondan ses gelmedi. Tekrar deneyin.") from error
    except sr.UnknownValueError as error:
        raise RuntimeError("Söylenenler anlaşılamadı. Daha net konuşup tekrar deneyin.") from error
    except sr.RequestError as error:
        raise RuntimeError("Ses tanıma servisine ulaşılamadı. İnternet bağlantısını kontrol edin.") from error


class ConversationWorker(QObject):
    """Mikrofonu sürekli dinler; Jarvis konuşurken söz kesmeyi de yakalar.

    Kontrol bayrakları arayüz iş parçacığından doğrudan set edilir; Qt kuyruğuna
    güvenmeyiz çünkü run() döngüsü kuyruktaki slotları çalıştırmaz.
    """

    heard = Signal(str)
    error = Signal(str)
    finished = Signal()
    level = Signal(float)

    BARGE_MIN_SECONDS = 0.7
    BARGE_MIN_ELAPSED = 0.75
    BARGE_PHRASE_LIMIT = 6.0
    POST_SPEECH_MUTE = 0.6
    LISTEN_TIMEOUT = 0.65
    PHRASE_LIMIT = 14.0
    LEVEL_FLOOR = 60.0
    LEVEL_CEIL = 5000.0

    def __init__(
        self,
        device_index: Optional[int] = None,
        barge_in: bool = True,
        device_name: str = "",
    ) -> None:
        super().__init__()
        self.device_index = device_index
        self.device_name = str(device_name or "").strip()
        self.barge_in = bool(barge_in)
        self._stop_event = threading.Event()
        self._speaking = threading.Event()
        self._speaking_since = 0.0
        self._paused = threading.Event()
        self._source = None
        self._mute_until = 0.0
        self._spoken_words = set()
        # Oda gürültüsü tabanı cümleler arasında korunur; her dinleme turunda
        # sıfırdan öğrenmek gürültülü odada cümle sonunu geciktiriyordu.
        self._noise_samples: deque = deque(maxlen=24)
        self._noise_floor = 0.0

    def set_speaking(self, speaking: bool, spoken_text: str = "") -> None:
        if speaking:
            self._speaking_since = time.monotonic()
            self._spoken_words = set(_words(spoken_text))
            self._speaking.set()
        else:
            self._speaking.clear()
            self._spoken_words = set()
            self._mute_until = time.monotonic() + self.POST_SPEECH_MUTE

    def set_paused(self, paused: bool) -> None:
        if paused:
            self._paused.set()
        else:
            self._paused.clear()

    def stop(self) -> None:
        self._stop_event.set()
        source = self._source
        stream = getattr(source, "stream", None) if source is not None else None
        try:
            if stream is not None and stream.is_active():
                stream.stop_stream()
        except Exception:
            pass

    def _duration(self, audio) -> float:
        try:
            frames = len(audio.get_raw_data()) / max(1, audio.sample_width)
            return frames / max(1.0, float(audio.sample_rate) * max(1, audio.channels))
        except Exception:
            return 0.0

    @classmethod
    def _level_from_rms(cls, rms: float) -> float:
        if rms <= cls.LEVEL_FLOOR:
            return 0.0
        span = math.log10(cls.LEVEL_CEIL / cls.LEVEL_FLOOR)
        return max(0.0, min(1.0, math.log10(rms / cls.LEVEL_FLOOR) / span))

    def _listen(self, source, threshold: float, timeout: float,
                phrase_limit: float, pause_threshold: float):
        """Mikrofonu kendimiz okuyup canlı seviye yayınlar; sessizlikte cümleyi bitirir.

        Sabit eşik gürültülü odada sürekli "konuşma var" sayıyor ve cümle ancak
        phrase_limit dolunca kapanıyordu. Bu yüzden başlangıç eşiği ölçülen oda
        gürültüsünün üstüne taşınır; cümle sonu ise biraz daha alçak bir eşikle
        (histerezis) ve kısa bir sessizlikle belirlenir.
        """
        import audioop

        chunk = int(getattr(source, "CHUNK", 1024) or 1024)
        rate = int(getattr(source, "SAMPLE_RATE", 16000) or 16000)
        width = int(getattr(source, "SAMPLE_WIDTH", 2) or 2)
        seconds = chunk / float(rate)
        reader = source.stream.read
        try:
            params = inspect.signature(reader).parameters
        except (TypeError, ValueError):
            params = {}
        # 3.17+ sürümünde MicrophoneStream.read(size) bayrağı kendi içinde
        # kapatıyor; eski sürümlerde taşma istisnası buradan susturulur.
        overflow_flag = "exception_on_overflow" in params
        wait_deadline = time.monotonic() + max(0.05, float(timeout))
        pre_roll = deque(maxlen=max(1, int(0.3 / seconds)))
        frames: List[bytes] = []
        started = False
        voiced = 0.0
        silence = 0.0
        phrase_deadline = 0.0
        phrase_elapsed = 0.0
        phrase_levels: List[float] = []
        base_gate = max(120.0, float(threshold))
        start_gate = base_gate
        silence_gate = base_gate
        while not self._stop_event.is_set():
            data = (
                reader(chunk, exception_on_overflow=False)
                if overflow_flag
                else reader(chunk)
            )
            if not data:
                continue
            rms = float(audioop.rms(data, width))
            self.level.emit(self._level_from_rms(rms))
            now = time.monotonic()
            if not started:
                # Jarvis konuşurken (kulaklıkla söz kesme) kendi sesini
                # gürültü tabanına katmamak için ölçüm duraklatılır.
                if not self._speaking.is_set():
                    self._noise_samples.append(rms)
                    if len(self._noise_samples) >= 6:
                        ordered = sorted(self._noise_samples)
                        self._noise_floor = ordered[max(0, int(len(ordered) * 0.3))]
                start_gate = max(base_gate, self._noise_floor * 1.55 + 70.0)
                pre_roll.append(data)
                if rms >= start_gate:
                    voiced += seconds
                    if voiced >= 0.18:
                        started = True
                        silence_gate = max(base_gate * 0.55, start_gate * 0.8, 90.0)
                        frames.extend(pre_roll)
                        pre_roll.clear()
                        silence = 0.0
                        phrase_elapsed = 0.0
                        phrase_levels = []
                        phrase_deadline = now + phrase_limit
                else:
                    voiced = 0.0
                    if now >= wait_deadline:
                        raise self._timeout_error()
                continue
            frames.append(data)
            phrase_elapsed += seconds
            phrase_levels.append(rms)
            if rms >= silence_gate:
                silence = 0.0
            else:
                silence += seconds
                if silence >= pause_threshold:
                    break
            if phrase_elapsed >= 1.8 and (
                len(self._noise_samples) < 6 or self._noise_floor >= base_gate * 0.9
            ):
                ordered = sorted(phrase_levels)
                median = ordered[len(ordered) // 2]
                if max(phrase_levels) < median * 1.7:
                    # Tek düze enerji konuşma değil, sürekli gürültüdür; cümle
                    # limitini beklemeden vazgeç ve tabanı yükselt.
                    self._noise_floor = max(self._noise_floor, median)
                    raise self._timeout_error()
            if now >= phrase_deadline:
                break
        if not started or self._stop_event.is_set():
            raise self._timeout_error()
        return self._audio_data(b"".join(frames), rate, width)

    @staticmethod
    def _timeout_error():
        import speech_recognition as sr

        return sr.WaitTimeoutError()

    @staticmethod
    def _audio_data(raw: bytes, rate: int, width: int):
        import speech_recognition as sr

        return sr.AudioData(raw, rate, width)

    @Slot()
    def run(self) -> None:
        try:
            import speech_recognition as sr
        except ImportError:
            self.error.emit("Ses tanıma bileşeni kurulu değil.")
            self.finished.emit()
            return

        recognizer = sr.Recognizer()
        recognizer.dynamic_energy_threshold = False
        recognizer.pause_threshold = 0.52
        recognizer.non_speaking_duration = 0.42
        recognizer.phrase_threshold = 0.22
        if self.device_index is None and self.device_name:
            self.device_index = resolve_microphone_index(self.device_name)
        kwargs = {"device_index": int(self.device_index)} if self.device_index is not None else {}
        failures = 0
        try:
            with sr.Microphone(**kwargs) as source:
                self._source = source
                recognizer.adjust_for_ambient_noise(source, duration=0.45)
                base_energy = max(140.0, min(float(recognizer.energy_threshold), 1200.0))
                barge_energy = min(8000.0, base_energy * 2.4 + 380.0)
                while not self._stop_event.is_set():
                    if self._paused.is_set():
                        time.sleep(0.08)
                        continue
                    speaking = self._speaking.is_set()
                    if speaking and not self.barge_in:
                        time.sleep(0.08)
                        continue
                    try:
                        audio = self._listen(
                            source,
                            threshold=barge_energy if speaking else base_energy,
                            timeout=self.LISTEN_TIMEOUT,
                            phrase_limit=self.BARGE_PHRASE_LIMIT if speaking else self.PHRASE_LIMIT,
                            pause_threshold=0.4 if speaking else 0.45,
                        )
                    except sr.WaitTimeoutError:
                        continue
                    self.level.emit(0.0)
                    if self._stop_event.is_set():
                        break
                    # Dinleme sürerken Jarvis konuşmaya başlamış olabilir; bayat
                    # duruma göre kendi sesini kullanıcı sanıp yanıt döngüsü açma.
                    speaking_now = self._speaking.is_set()
                    if speaking_now:
                        if not self.barge_in:
                            continue
                        if time.monotonic() - self._speaking_since < self.BARGE_MIN_ELAPSED:
                            continue
                        if self._duration(audio) < self.BARGE_MIN_SECONDS:
                            continue
                    elif time.monotonic() < self._mute_until:
                        continue
                    try:
                        payload = recognizer.recognize_google(
                            audio, language="tr-TR", show_all=True
                        )
                        text = _normalize_wake(_pick_transcript(payload)).strip()
                    except sr.UnknownValueError:
                        continue
                    except sr.RequestError:
                        failures += 1
                        if failures >= 2:
                            self.error.emit(
                                "Ses tanıma servisine ulaşılamadı. İnternet bağlantısını kontrol edin."
                            )
                            break
                        time.sleep(1.0)
                        continue
                    failures = 0
                    if not text:
                        continue
                    if speaking_now:
                        if not should_accept_barge(text):
                            continue
                        words = set(_words(text))
                        if self._spoken_words and len(words) >= 3:
                            overlap = len(words & self._spoken_words) / len(words)
                            if overlap >= 0.7:
                                continue
                    self.heard.emit(text)
        except Exception as error:
            if not self._stop_event.is_set():
                self.error.emit(f"Mikrofon hatası: {error}")
        finally:
            self._source = None
            self.finished.emit()
