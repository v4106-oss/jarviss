from __future__ import annotations

import asyncio
import tempfile
import threading
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.core.safety import ActionForbidden

LANGUAGE_TAGS = {
    "tr": "tr-TR",
    "turkce": "tr-TR",
    "türkçe": "tr-TR",
    "en": "en-US",
    "english": "en-US",
    "ingilizce": "en-US",
}
_ENGINE_LOCK = threading.Lock()
_ENGINE_CACHE: Dict[str, Tuple[Any, str]] = {}


def _normalize(text: str) -> str:
    text = text.replace("İ", "i").replace("I", "i").replace("ı", "i")
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.split())


def _virtual_origin() -> Tuple[int, int]:
    try:
        import ctypes

        user32 = ctypes.windll.user32
        return int(user32.GetSystemMetrics(76)), int(user32.GetSystemMetrics(77))
    except Exception:
        return 0, 0


def grab_region(rectangle: Optional[Tuple[int, int, int, int]] = None) -> Tuple[Path, Tuple[int, int], Tuple[int, int]]:
    """Ekranın tamamını veya bir pencere dikdörtgenini geçici PNG olarak yakalar."""
    try:
        from PIL import ImageGrab
    except ImportError:
        raise ActionForbidden("OCR için Pillow gerekli; install_dependency ile Pillow kurulabilir.")
    virtual_left, virtual_top = _virtual_origin()
    try:
        image = ImageGrab.grab(all_screens=True)
    except Exception as error:
        raise ActionForbidden(f"OCR ekran görüntüsü alınamadı: {error}")
    origin = (virtual_left, virtual_top)
    if rectangle is not None:
        left, top, right, bottom = rectangle
        box = (
            max(0, left - virtual_left),
            max(0, top - virtual_top),
            min(image.width, right - virtual_left),
            min(image.height, bottom - virtual_top),
        )
        if box[2] <= box[0] or box[3] <= box[1]:
            image.close()
            raise ActionForbidden("Pencere ekranın görünür alanında değil; OCR yapılamadı.")
        image = image.crop(box)
        origin = (virtual_left + box[0], virtual_top + box[1])
    temp = tempfile.NamedTemporaryFile(prefix="jarvis_ocr_", suffix=".png", delete=False)
    temp.close()
    path = Path(temp.name)
    try:
        image.convert("RGB").save(path, "PNG")
        size = image.size
    finally:
        image.close()
    return path, origin, size


class ScreenOCR:
    """Windows.Media.Ocr ile ekrandaki kelimeleri ve gerçek ekran koordinatlarını bulur."""

    @staticmethod
    def available() -> bool:
        try:
            import winrt.windows.media.ocr  # noqa: F401
            import winrt.windows.storage  # noqa: F401
        except ImportError:
            return False
        return True

    @classmethod
    def _engine(cls, language: str = "", refresh: bool = False) -> Tuple[Any, str]:
        raw = language.strip().casefold()
        key = LANGUAGE_TAGS.get(raw, raw) or "auto"
        if not refresh:
            with _ENGINE_LOCK:
                cached = _ENGINE_CACHE.get(key)
            if cached is not None:
                return cached
        result = cls._create_engine(language)
        with _ENGINE_LOCK:
            _ENGINE_CACHE[key] = result
        return result

    @staticmethod
    def _create_engine(language: str = ""):
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine

        requested = LANGUAGE_TAGS.get(language.strip().casefold(), language.strip())
        candidates = [requested] if requested else []
        candidates.extend(["tr-TR", "en-US"])
        seen = set()
        for tag in candidates:
            if not tag or tag in seen:
                continue
            seen.add(tag)
            try:
                engine = OcrEngine.try_create_from_language(Language(tag))
            except Exception:
                engine = None
            if engine is not None:
                return engine, tag
        engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            raise ActionForbidden(
                "Windows OCR motoru bulunamadı; Windows dil paketlerinden Türkçe veya İngilizce OCR ekleyin."
            )
        try:
            tag = engine.recognizer_language.language_tag
        except Exception:
            tag = ""
        return engine, tag

    async def _recognize(self, image_path: Path, language: str) -> Tuple[List[dict], str]:
        from winrt.windows.graphics.imaging import BitmapDecoder
        from winrt.windows.storage import FileAccessMode, StorageFile

        engine, language_tag = self._engine(language)
        stream = None
        try:
            file = await StorageFile.get_file_from_path_async(str(image_path))
            stream = await file.open_async(FileAccessMode.READ)
            decoder = await BitmapDecoder.create_async(stream)
            bitmap = await decoder.get_software_bitmap_async()
            try:
                result = await engine.recognize_async(bitmap)
            except Exception:
                # Motor başka iş parçacığında oluşturulduğunda WinRT bazen kullanılamaz hale gelir.
                engine, language_tag = self._engine(language, refresh=True)
                result = await engine.recognize_async(bitmap)
            lines: List[dict] = []
            for line in result.lines:
                words = []
                for word in line.words:
                    text = (word.text or "").strip()
                    if not text:
                        continue
                    rectangle = word.bounding_rect
                    words.append({
                        "text": text,
                        "x": float(rectangle.x),
                        "y": float(rectangle.y),
                        "width": float(rectangle.width),
                        "height": float(rectangle.height),
                    })
                if words:
                    lines.append({"text": (line.text or "").strip(), "words": words})
            return lines, language_tag
        finally:
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass

    @staticmethod
    def _match(lines: List[dict], query: str) -> List[dict]:
        normalized_query = _normalize(query)
        if not normalized_query:
            return []
        tokens = normalized_query.split()
        matches = []
        seen = set()

        def add(text: str, line: str, words: List[dict], score: float) -> None:
            left = min(word["x"] for word in words)
            top = min(word["y"] for word in words)
            right = max(word["x"] + word["width"] for word in words)
            bottom = max(word["y"] + word["height"] for word in words)
            key = (round(left), round(top), round(right - left), round(bottom - top))
            if key in seen:
                return
            seen.add(key)
            matches.append({
                "text": text,
                "line": line,
                "box": [round(left), round(top), round(right - left), round(bottom - top)],
                "score": round(score, 2),
            })

        for line in lines:
            words = line["words"]
            for start in range(max(1, len(words) - len(tokens) + 1)):
                window = words[start:start + len(tokens)]
                if len(window) < len(tokens):
                    continue
                combined = " ".join(_normalize(word["text"]) for word in window)
                if combined == normalized_query:
                    score = 1.0
                elif combined.startswith(normalized_query):
                    score = 0.9
                elif normalized_query in combined:
                    score = 0.8
                else:
                    continue
                add(" ".join(word["text"] for word in window), line["text"], window, score)
            if not matches and normalized_query in _normalize(line["text"]):
                add(line["text"], line["text"], words, 0.5)
        matches.sort(key=lambda item: (-item["score"], item["box"][1], item["box"][0]))
        return matches

    def read_lines(
        self,
        image_path: Path,
        origin: Tuple[int, int] = (0, 0),
        language: str = "",
        limit: int = 60,
        scale: float = 1.0,
    ) -> dict:
        """Görüntüdeki tüm okunabilir satırları gerçek ekran koordinatlarıyla döndürür."""
        try:
            lines, language_tag = asyncio.run(self._recognize(image_path, language))
        except ActionForbidden:
            raise
        except Exception as error:
            raise ActionForbidden(f"Windows OCR çalıştırılamadı: {error}")
        offset_x, offset_y = origin
        output = []
        for line in lines:
            words = line.get("words") or []
            text = str(line.get("text", "")).strip()
            if not text or not words:
                continue
            left = min(word["x"] for word in words)
            top = min(word["y"] for word in words)
            right = max(word["x"] + word["width"] for word in words)
            bottom = max(word["y"] + word["height"] for word in words)
            output.append({
                "text": text[:200],
                "box": [
                    round(left * scale), round(top * scale),
                    round((right - left) * scale), round((bottom - top) * scale),
                ],
                "center_x": offset_x + round((left + (right - left) / 2) * scale),
                "center_y": offset_y + round((top + (bottom - top) / 2) * scale),
            })
        output.sort(key=lambda item: (item["center_y"], item["center_x"]))
        return {
            "language": language_tag,
            "count": len(output),
            "lines": output[:max(1, limit)],
            "truncated": len(output) > limit,
            "note": "Metin satırları gerçek ekran koordinatlarıyla listelendi; tıklama için uygun center_x/center_y kullan.",
        }

    def find(
        self,
        image_path: Path,
        query: str,
        origin: Tuple[int, int] = (0, 0),
        language: str = "",
    ) -> dict:
        query = query.strip()
        if not query:
            raise ActionForbidden("Aranacak ekran metni boş olamaz.")
        try:
            lines, language_tag = asyncio.run(self._recognize(image_path, language))
        except ActionForbidden:
            raise
        except Exception as error:
            raise ActionForbidden(f"Windows OCR çalıştırılamadı: {error}")
        matches = self._match(lines, query)
        offset_x, offset_y = origin
        for item in matches:
            left, top, width, height = item["box"]
            item["center_x"] = offset_x + round(left + width / 2)
            item["center_y"] = offset_y + round(top + height / 2)
        best = matches[0] if matches else None
        note = (
            "OCR kelime kutuları gerçek ekran koordinatına çevrildi. Tıklamadan önce hedefin doğru kelime "
            "olduğunu metinden doğrula; tıklama sonrası capture_screen ile sonucu denetle."
        )
        if not best:
            note = "Bu metin OCR sonucunda bulunamadı; farklı bir kelime dene veya capture_screen ile görüntüyü incele."
        return {
            "query": query,
            "language": language_tag,
            "matches": matches[:10],
            "best": best,
            "note": note,
        }
