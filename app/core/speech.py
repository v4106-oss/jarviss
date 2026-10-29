"""Doğal Türkçe seslendirme; cümle cümle sentez, kesilebilir oynatma.

Birincil yol edge-tts nöral sesleridir. Metin cümlelere bölünür; ilk cümle
hazır olur olmaz çalmaya başlar, kalanı arka planda üretilir. Sentez Qt
iş parçacığı yerine daemon Python iş parçacıklarında yapılır: uygulama
kapanırken yarım kalan bir ağ isteği QThread çökmesine yol açmaz.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer

VOICE = "tr-TR-EmelNeural"
DEFAULT_STYLE = "jarvis"
VOICE_STYLES: Dict[str, Tuple[str, str, str]] = {
    "jarvis": ("en-US-AndrewMultilingualNeural", "-10Hz", "Jarvis · özel, derin, hızlı"),
    "natural": ("tr-TR-EmelNeural", "+0Hz", "Emel · doğal Türkçe"),
    "warm": ("en-US-AvaMultilingualNeural", "+0Hz", "Ava · sıcak kadın"),
    "male": ("en-AU-WilliamMultilingualNeural", "-10Hz", "William · derin erkek"),
}
# Kayıtlı voice_rate kullanıcı tabanıdır; karakterin kendi temposunu bunun üstüne ekler.
STYLE_RATE_OFFSETS: Dict[str, int] = {
    "jarvis": 24,
    "natural": 12,
    "warm": 24,
    "male": 22,
}
RATE = "-3%"
PITCH = "+0Hz"
SYNTH_ATTEMPTS = 3
SYNTH_TIMEOUT = 10.0
MAX_CHARS = 3000
MAX_CHUNK_CHARS = 220
FIRST_CHUNK_CHARS = 90
VOICE_REPLY_CHARS = 110
CACHE_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Jarvis" / "cache" / "tts"
LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
_TRACE_LOCK = threading.Lock()

_LONG_SPEECH_PATTERN = re.compile(
    r"\b(?:haber\w*|oku|okur\w*|okuy\w*|okuma\w*|detay\w*|hepsini|tumunu|butun\w*"
    r"|liste\w*|maddeler\w*|acikla\w*|anlat\w*)\b"
)


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


def _trace(message: str) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = LOG_DIR / "konusma.log"
        with _TRACE_LOCK:
            if path.is_file() and path.stat().st_size > 512 * 1024:
                path.unlink(missing_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                stamp = time.strftime("%Y-%m-%d %H:%M:%S")
                handle.write(f"{stamp} {message}\n")
    except OSError:
        pass


def style_parts(style: str) -> Tuple[str, str, str]:
    return VOICE_STYLES.get(str(style or "").strip().casefold(), VOICE_STYLES[DEFAULT_STYLE])


def style_label(style: str) -> str:
    return style_parts(style)[2]


def style_rate_offset(style: str) -> int:
    key = str(style or "").strip().casefold()
    return STYLE_RATE_OFFSETS.get(key, STYLE_RATE_OFFSETS[DEFAULT_STYLE])


def clean_for_speech(text: str) -> str:
    """Markdown, kod ve bağlantıları ayıklayıp seslendirilebilir düz metin üretir."""
    text = str(text or "")
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[*_#>~|]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = "".join(character for character in text if character.isprintable())
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
    return text


def _split_long(text: str, max_chars: int) -> List[str]:
    parts: List[str] = []
    current = ""
    for word in text.split():
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= max_chars:
            current = f"{current} {word}"
        else:
            parts.append(current)
            current = word
    if current:
        parts.append(current)
    return parts


def split_for_speech(text: str, max_chars: int = MAX_CHUNK_CHARS) -> List[str]:
    """Yanıtı kısa cümle parçalarına böler; ilk ses mümkün olduğunca erken çıkar."""
    clean = clean_for_speech(text)
    if not clean:
        return []
    sentences = [piece for piece in re.split(r"(?<=[.!?…;:])\s+", clean) if piece]
    chunks: List[str] = []
    current = ""
    for sentence in sentences:
        pieces = [sentence] if len(sentence) <= max_chars else _split_long(sentence, max_chars)
        for piece in pieces:
            if not current:
                current = piece
            elif len(current) + 1 + len(piece) <= max_chars:
                current = f"{current} {piece}"
            else:
                chunks.append(current)
                current = piece
    if current:
        chunks.append(current)
    return chunks


def plan_chunks(
    text: str,
    first_chars: int = FIRST_CHUNK_CHARS,
    max_chars: int = MAX_CHUNK_CHARS,
) -> List[str]:
    """İlk parçayı kısa tutar (ses hemen başlasın), kalanı daha büyük parçalara böler."""
    clean = clean_for_speech(text)
    if not clean:
        return []
    sentences = [piece for piece in re.split(r"(?<=[.!?…;:])\s+", clean) if piece]
    first = sentences[0] if sentences else clean
    rest = sentences[1:]
    if len(first) > first_chars:
        head, tail = _split_head(first, first_chars)
        first = head
        if tail:
            rest.insert(0, tail)
    chunks = [first]
    rest_text = " ".join(rest).strip()
    if rest_text:
        chunks.extend(split_for_speech(rest_text, max_chars=max_chars))
    return [chunk for chunk in chunks if chunk]


def _split_head(text: str, limit: int) -> Tuple[str, str]:
    if len(text) <= limit:
        return text, ""
    window = text[:limit]
    cut = max(window.rfind(", "), window.rfind("; "), window.rfind(": "))
    if cut >= 24 and cut + 1 < len(text):
        return text[: cut + 1].strip(), text[cut + 1 :].strip()
    cut = window.rfind(" ")
    if cut <= 0:
        return window, text[limit:].strip()
    return text[:cut].strip(), text[cut:].strip()


def concise_for_voice(text: str, max_chars: int = VOICE_REPLY_CHARS) -> str:
    """Sesli sohbette ilk cümleleri tutar; ayrıntı sohbet metninde kalır."""
    clean = clean_for_speech(text)
    if len(clean) <= max_chars:
        return clean
    sentences = [piece for piece in re.split(r"(?<=[.!?…;:])\s+", clean) if piece]
    result = ""
    for sentence in sentences:
        candidate = f"{result} {sentence}".strip() if result else sentence
        if result and len(candidate) > max_chars:
            break
        if not result and len(sentence) > max_chars:
            result = _split_head(sentence, max_chars)[0].rstrip(" ,;:")
            break
        result = candidate
        if len(result) >= max_chars * 0.6:
            break
    return result or clean[:max_chars]


def spoken_limit_for_prompt(
    prompt: str,
    concise: int = VOICE_REPLY_CHARS,
) -> Optional[int]:
    """Sesli istekte kısa yanıt sınırı; açık anlatım/okuma isteklerinde sınırsız."""
    folded = _fold(prompt)
    if _LONG_SPEECH_PATTERN.search(folded):
        return None
    return concise


async def _synthesize_async(text: str, path: Path, voice: str, rate: str, pitch: str) -> None:
    import edge_tts

    communicator = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    await asyncio.wait_for(communicator.save(str(path)), timeout=SYNTH_TIMEOUT)


def synthesize(
    text: str,
    voice: str = VOICE,
    rate: str = RATE,
    pitch: str = PITCH,
) -> Path:
    """Metni nöral MP3 olarak üretir ve önbellekten yeniden kullanır."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(f"{voice}|{rate}|{pitch}|{text}".encode("utf-8")).hexdigest()[:24]
    path = CACHE_DIR / f"{digest}.mp3"
    if path.is_file() and path.stat().st_size > 1024:
        return path
    last_error: Optional[Exception] = None
    for attempt in range(SYNTH_ATTEMPTS):
        try:
            asyncio.run(_synthesize_async(text, path, voice, rate, pitch))
            if path.is_file() and path.stat().st_size > 1024:
                _trim_cache()
                return path
            raise RuntimeError("Nöral ses dosyası üretilemedi.")
        except Exception as error:
            last_error = error
            path.unlink(missing_ok=True)
            if attempt + 1 < SYNTH_ATTEMPTS:
                time.sleep(0.6)
    raise last_error or RuntimeError("Nöral ses üretilemedi.")


def _trim_cache(keep: int = 40) -> None:
    try:
        files = sorted(CACHE_DIR.glob("*.mp3"), key=lambda item: item.stat().st_mtime, reverse=True)
        for item in files[keep:]:
            item.unlink(missing_ok=True)
    except OSError:
        pass


def _sapi_script(text: str, volume: int) -> str:
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "Add-Type -AssemblyName System.Speech\n"
        f"$text = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}'))\n"
        "$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
        f"try {{ $synth.Volume = {max(0, min(100, volume))} }} catch {{}}\n"
        "try {\n"
        "  $voice = $synth.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -like 'tr*' } | Select-Object -First 1\n"
        "  if ($voice) { $synth.SelectVoice($voice.VoiceInfo.Name) }\n"
        "} catch {}\n"
        "$synth.Speak($text)\n"
        "$synth.Dispose()\n"
    )


def _emit(signal, *args) -> None:
    try:
        signal.emit(*args)
    except RuntimeError:
        pass


class SpeechService(QObject):
    """Cümle cümle seslendiren, seçili çıkışa çalan ve anında susan servis."""

    started = Signal()
    finished = Signal()
    error = Signal(str)
    busy_changed = Signal(bool)

    _chunk_ready = Signal(int, int, str)
    _synth_failed = Signal(int, str)
    _synth_finished = Signal(int)
    _fallback_started_sig = Signal(int)
    _fallback_failed_sig = Signal(int, str)
    _fallback_finished_sig = Signal(int)

    def __init__(self, settings=None, parent: Optional[QObject] = None) -> None:
        if settings is not None and parent is None and isinstance(settings, QObject) and not hasattr(settings, "voice_style"):
            parent, settings = settings, None
        super().__init__(parent)
        self._settings = settings
        self._output: Optional[QAudioOutput] = None
        self._player: Optional[QMediaPlayer] = None
        self._token = 0
        self._active = False
        self._text = ""
        self._chunks: List[str] = []
        self._paths = {}
        self._next_play = 0
        self._synth_done = False
        self._started_emitted = False
        self._pending_play = False
        self._pumping = False
        self._fallback_tried = False
        self._fallback_active = False
        self._fallback_proc = None
        self._fallback_stop: Optional[threading.Event] = None
        self._synth_stop: Optional[threading.Event] = None
        self._started_at = 0.0
        self._chunk_ready.connect(self._on_chunk_ready)
        self._synth_failed.connect(self._on_synth_failed)
        self._synth_finished.connect(self._on_synth_finished)
        self._fallback_started_sig.connect(self._on_fallback_started)
        self._fallback_failed_sig.connect(self._on_fallback_failed)
        self._fallback_finished_sig.connect(self._on_fallback_finished)
        self.apply_settings()

    @property
    def busy(self) -> bool:
        return self._active

    @property
    def spoken_text(self) -> str:
        return self._text

    @property
    def voice_style(self) -> str:
        value = str(getattr(self._settings, "voice_style", DEFAULT_STYLE) or DEFAULT_STYLE)
        return value if value in VOICE_STYLES else DEFAULT_STYLE

    @property
    def voice_name(self) -> str:
        return style_parts(self.voice_style)[0]

    @property
    def voice_label(self) -> str:
        return style_label(self.voice_style)

    def _ensure_player(self) -> None:
        """QtMultimedia motorunu ilk konuşmada kur; açılışı yavaşlatmasın."""
        if self._player is not None:
            return
        self._output = QAudioOutput(self)
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._output)
        self._player.mediaStatusChanged.connect(self._on_media_status)
        self._player.errorOccurred.connect(self._on_player_error)
        self.apply_settings()

    def apply_settings(self) -> None:
        """Ses karakteri, çıkış aygıtı ve düzeyi kayıtlı ayarlardan yenilenir."""
        if self._output is None:
            return
        try:
            wanted = str(getattr(self._settings, "speaker_device", "") or "")
            chosen = QMediaDevices.defaultAudioOutput()
            if wanted:
                for device in QMediaDevices.audioOutputs():
                    if device.description() == wanted:
                        chosen = device
                        break
            self._output.setDevice(chosen)
        except Exception:
            pass
        try:
            volume = int(getattr(self._settings, "voice_volume", 90))
        except (TypeError, ValueError):
            volume = 90
        self._output.setVolume(max(0.0, min(1.0, volume / 100.0)))

    def _rate_text(self) -> str:
        try:
            value = int(getattr(self._settings, "voice_rate", -3))
        except (TypeError, ValueError):
            value = -3
        value += style_rate_offset(self.voice_style)
        return f"{max(-20, min(24, value)):+d}%"

    def _volume(self) -> int:
        try:
            return max(0, min(100, int(getattr(self._settings, "voice_volume", 90))))
        except (TypeError, ValueError):
            return 90

    def speak(self, text: str, max_chars: Optional[int] = None) -> None:
        body = concise_for_voice(text, max_chars) if max_chars else clean_for_speech(text)
        chunks = plan_chunks(body)
        if not chunks:
            return
        self._ensure_player()
        self._halt(notify=False)
        self._token += 1
        token = self._token
        voice, pitch, _ = style_parts(self.voice_style)
        self._text = body
        self._chunks = chunks
        self._paths = {}
        self._next_play = 0
        self._synth_done = False
        self._started_emitted = False
        self._pending_play = False
        self._fallback_tried = False
        self._fallback_active = False
        self._active = True
        self._started_at = time.perf_counter()
        self._synth_stop = threading.Event()
        self.busy_changed.emit(True)
        _trace(
            f"speak token={token} kaynak={len(str(text or ''))} ses={len(body)} "
            f"parca={len(chunks)} limit={max_chars or 0}"
        )
        threading.Thread(
            target=self._synth_first,
            args=(token, chunks, voice, self._rate_text(), pitch, self._synth_stop),
            name="jarvis-tts-first",
            daemon=True,
        ).start()

    def _synth_first(
        self,
        token: int,
        chunks: List[str],
        voice: str,
        rate: str,
        pitch: str,
        stop: threading.Event,
    ) -> None:
        started = time.perf_counter()
        try:
            path = synthesize(chunks[0], voice, rate, pitch)
        except Exception as error:
            if not stop.is_set():
                _emit(self._synth_failed, token, str(error))
            return
        if stop.is_set():
            return
        _trace(f"ilk_parca token={token} ms={int((time.perf_counter() - started) * 1000)}")
        _emit(self._chunk_ready, token, 0, str(path))
        if len(chunks) > 1:
            threading.Thread(
                target=self._synth_rest,
                args=(token, chunks, voice, rate, pitch, stop),
                name="jarvis-tts-rest",
                daemon=True,
            ).start()
        else:
            _emit(self._synth_finished, token)

    def _synth_rest(
        self,
        token: int,
        chunks: List[str],
        voice: str,
        rate: str,
        pitch: str,
        stop: threading.Event,
    ) -> None:
        for index in range(1, len(chunks)):
            if stop.is_set():
                return
            try:
                path = synthesize(chunks[index], voice, rate, pitch)
            except Exception as error:
                if not stop.is_set():
                    _emit(self._synth_failed, token, str(error))
                return
            if stop.is_set():
                return
            _emit(self._chunk_ready, token, index, str(path))
        _emit(self._synth_finished, token)

    def _on_chunk_ready(self, token: int, index: int, path: str) -> None:
        if token != self._token or not self._active:
            return
        self._paths[index] = Path(path)
        self._pump()

    def _on_synth_finished(self, token: int) -> None:
        if token != self._token or not self._active or self._fallback_active:
            return
        self._synth_done = True
        self._pump()

    def _on_synth_failed(self, token: int, message: str) -> None:
        if token != self._token or not self._active:
            return
        if not self._fallback_tried and not self._started_emitted:
            self._fallback_tried = True
            self.error.emit(f"Doğal ses üretilemedi; yerel ses kullanılıyor: {message}")
            self._start_fallback(token)
            return
        self.error.emit(f"Seslendirme hatası: {message}")
        self._synth_done = True
        self._pump()

    def _pump(self) -> None:
        if self._pumping or not self._active or self._fallback_active or self._player is None:
            return
        if self._pending_play:
            return
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            return
        self._pumping = True
        try:
            path = self._paths.pop(self._next_play, None)
            if path is None and self._synth_done and self._paths:
                self._next_play = min(self._paths)
                path = self._paths.pop(self._next_play, None)
            if path is not None:
                self._next_play += 1
                self._pending_play = True
                self._player.setSource(QUrl.fromLocalFile(str(path)))
                if not self._started_emitted:
                    self._started_emitted = True
                    elapsed = int((time.perf_counter() - self._started_at) * 1000)
                    _trace(f"play token={self._token} ilk_ses_ms={elapsed}")
                    self.started.emit()
                self._player.play()
                return
            if self._synth_done and not self._paths:
                self._finish()
        finally:
            self._pumping = False

    def _on_media_status(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._pending_play = False
            QTimer.singleShot(0, self._pump)
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._pending_play = False
            if self._active and not self._fallback_active:
                self._on_player_error(
                    QMediaPlayer.Error.ResourceError,
                    "Ses dosyası oynatılamadı.",
                )

    def _on_player_error(self, error, error_string: str) -> None:
        if not self._active or self._fallback_active or error == QMediaPlayer.Error.NoError:
            return
        message = error_string or str(error)
        if not self._started_emitted and not self._fallback_tried:
            self._fallback_tried = True
            self.error.emit(f"Ses oynatılamadı; yerel ses kullanılıyor: {message}")
            self._start_fallback(self._token)
            return
        self.error.emit(f"Ses oynatma hatası: {message}")
        self._synth_done = True
        self._paths = {}
        self._finish()

    def _start_fallback(self, token: int) -> None:
        if self._player is not None:
            self._player.stop()
        self._paths = {}
        self._next_play = len(self._chunks)
        self._synth_done = True
        self._pending_play = False
        self._fallback_active = True
        self._fallback_stop = threading.Event()
        threading.Thread(
            target=self._fallback_run,
            args=(token, self._text, self._volume(), self._fallback_stop),
            name="jarvis-tts-fallback",
            daemon=True,
        ).start()

    def _fallback_run(self, token: int, text: str, volume: int, stop: threading.Event) -> None:
        if os.name != "nt":
            _emit(self._fallback_failed_sig, token, "Yerel ses yalnızca Windows'ta kullanılabilir.")
            return
        script = _sapi_script(text, volume)
        command = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", command],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except Exception as error:
            _emit(self._fallback_failed_sig, token, f"Yerel ses başlatılamadı: {error}")
            return
        self._fallback_proc = proc
        _emit(self._fallback_started_sig, token)
        while proc.poll() is None:
            if stop.wait(0.12):
                try:
                    proc.terminate()
                    proc.wait(1.0)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                break
        if self._fallback_proc is proc:
            self._fallback_proc = None
        _emit(self._fallback_finished_sig, token)

    def _on_fallback_started(self, token: int) -> None:
        if token != self._token:
            return
        if self._active and not self._started_emitted:
            self._started_emitted = True
            self.started.emit()

    def _on_fallback_failed(self, token: int, message: str) -> None:
        if token != self._token or not self._active:
            return
        self._fallback_active = False
        self.error.emit(message)
        self._finish()

    def _on_fallback_finished(self, token: int) -> None:
        if token != self._token:
            return
        self._fallback_active = False
        self._fallback_proc = None
        if self._active:
            self._finish()

    def _finish(self) -> None:
        if not self._active:
            return
        self._active = False
        self._pending_play = False
        self._paths = {}
        stop = self._synth_stop
        if stop is not None:
            stop.set()
        if self._player is not None:
            try:
                self._player.stop()
            except RuntimeError:
                pass
        _trace(f"finish token={self._token}")
        self.busy_changed.emit(False)
        self.finished.emit()

    def stop(self) -> None:
        self._halt(notify=True)

    def _halt(self, notify: bool) -> None:
        was_active = self._active
        self._token += 1
        self._active = False
        stop = self._synth_stop
        if stop is not None:
            stop.set()
        fallback_stop = self._fallback_stop
        if fallback_stop is not None:
            fallback_stop.set()
        proc = self._fallback_proc
        self._fallback_proc = None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
        if self._player is not None:
            try:
                self._player.stop()
            except RuntimeError:
                pass
        self._paths = {}
        self._next_play = 0
        self._synth_done = False
        self._pending_play = False
        self._fallback_active = False
        self._fallback_stop = None
        self._synth_stop = None
        if notify and was_active:
            self.busy_changed.emit(False)
            self.finished.emit()

    def shutdown(self) -> None:
        """Kapanışta beklemez; sentez daemon iş parçacıklarında sürüyorsa süreçle biter."""
        self._halt(notify=False)
        if self._player is not None:
            try:
                self._player.stop()
            except RuntimeError:
                pass
