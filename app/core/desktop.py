from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from app.core.safety import ActionForbidden


class DesktopTools:
    def __init__(self, data_dir: Path) -> None:
        self.directory = Path(data_dir) / "screenshots"

    def capture(self, args: dict) -> tuple[str, str]:
        try:
            from PIL import ImageGrab
        except ImportError:
            raise ActionForbidden("Ekran görüntüsü için Pillow gerekli; install_dependency ile Pillow kurulabilir.")
        self.directory.mkdir(parents=True, exist_ok=True)
        image = ImageGrab.grab(all_screens=True)
        original_width, original_height = image.size
        image.thumbnail((1920, 1920))
        scale = original_width / image.width if image.width else 1.0
        old = sorted(self.directory.glob("screen_*.jpg"))
        for item in old[:-19]:
            try:
                item.unlink()
            except OSError:
                pass
        path = self.directory / f"screen_{time.time_ns()}.jpg"
        image.convert("RGB").save(path, "JPEG", quality=92, optimize=True)
        return str(path), (
            f"✅ Ekran görüntüsü alındı ve sonraki model adımı için hazırlandı: {path}\n"
            f"Görüntü: {image.width}x{image.height}; gerçek ekran: {original_width}x{original_height}; "
            f"görüntüden ekrana ölçek: {scale:.3f}. Fare koordinatları gerçek ekran pikselidir; görüntü "
            f"küçültüldüyse görüntü koordinatını {scale:.3f} ile çarp."
        )

    def inspect_image(self, path: Path, on_preview=None) -> str:
        path = Path(path)
        if not path.is_file():
            raise ActionForbidden(f"Görsel bulunamadı: {path}")
        if path.stat().st_size > 25_000_000:
            raise ActionForbidden("Görsel 25 MB'tan büyük; doğrulama için çok büyük.")
        try:
            from collections import Counter
            from PIL import Image
            with Image.open(path) as source:
                image = source.convert("RGB")
                width, height = image.size
                sample = image.copy()
                sample.thumbnail((320, 320))
                sample_width, sample_height = sample.size
                try:
                    data = sample.get_flattened_data()
                except AttributeError:
                    data = sample.getdata()
                pixels = list(data)
                total = len(pixels) or 1
                non_white = 0
                buckets = Counter()
                min_x, min_y = sample_width, sample_height
                max_x = max_y = -1
                grid_counts = [[0, 0, 0] for _ in range(3)]
                grid_sizes = [[0, 0, 0] for _ in range(3)]
                for index, (r, g, b) in enumerate(pixels):
                    column = min(2, (index % sample_width) * 3 // sample_width)
                    row = min(2, (index // sample_width) * 3 // sample_height)
                    grid_sizes[row][column] += 1
                    buckets[(r // 32 * 32, g // 32 * 32, b // 32 * 32)] += 1
                    if r >= 245 and g >= 245 and b >= 245:
                        continue
                    non_white += 1
                    grid_counts[row][column] += 1
                    x, y = index % sample_width, index // sample_width
                    min_x, min_y = min(min_x, x), min(min_y, y)
                    max_x, max_y = max(max_x, x), max(max_y, y)
                dominant = [
                    {"rgb": list(color), "ratio": round(count / total, 3)}
                    for color, count in buckets.most_common(5)
                ]
                content_bbox = None
                if max_x >= 0:
                    scale_x = width / sample_width
                    scale_y = height / sample_height
                    content_bbox = [
                        round(min_x * scale_x), round(min_y * scale_y),
                        round((max_x + 1) * scale_x), round((max_y + 1) * scale_y),
                    ]
                grid = [
                    [round(grid_counts[row][column] / max(1, grid_sizes[row][column]), 3) for column in range(3)]
                    for row in range(3)
                ]
                preview = image.copy()
                preview.thumbnail((1200, 1200))
                self.directory.mkdir(parents=True, exist_ok=True)
                preview_path = self.directory / f"inspect_{time.time_ns()}.jpg"
                preview.save(preview_path, "JPEG", quality=90, optimize=True)
        except ActionForbidden:
            raise
        except Exception as error:
            raise ActionForbidden(f"Görsel açılamadı: {error}")
        if on_preview:
            on_preview(str(preview_path))
        ratio = non_white / total
        return json.dumps({
            "path": str(path),
            "size": [width, height],
            "non_white_ratio": round(ratio, 3),
            "blank": ratio < 0.005,
            "content_bbox": content_bbox,
            "grid": grid,
            "dominant_colors": dominant,
            "preview": str(preview_path),
            "warning": (
                "blank=true ise görsel boştur; başarılı sayma ve çizimi düzelt. blank=false yalnızca bir şeyin "
                "çizildiğini gösterir; istenen nesnenin tamamlandığını göstermez. content_bbox çok geniş veya grid "
                "beklenen bölgelerde dengesizse çizim hatalı olabilir."
            ),
        }, ensure_ascii=False)

    @staticmethod
    def _coordinate(args: dict, key: str, default=None) -> int:
        value = args.get(key, default)
        if value is None:
            raise ActionForbidden(f"Fare eylemi için {key} koordinatı gerekli.")
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ActionForbidden(f"{key} koordinatı sayı olmalı: {value!r}")
        if not -30000 <= number <= 30000:
            raise ActionForbidden("Ekran koordinatı geçerli aralıkta değil.")
        return number

    def mouse(self, args: dict) -> str:
        from pywinauto import mouse
        action = str(args.get("action", "click"))
        x, y = self._coordinate(args, "x"), self._coordinate(args, "y")
        if action == "click":
            mouse.click(button=str(args.get("button", "left")), coords=(x, y))
        elif action == "double_click":
            mouse.double_click(button="left", coords=(x, y))
        elif action == "right_click":
            mouse.right_click(coords=(x, y))
        elif action == "move":
            mouse.move(coords=(x, y))
        elif action == "scroll":
            mouse.scroll(coords=(x, y), wheel_dist=max(-20, min(20, int(args.get("amount", 1)))))
        elif action == "drag":
            end_x, end_y = self._coordinate(args, "end_x"), self._coordinate(args, "end_y")
            mouse.press(coords=(x, y))
            steps = 24
            for index in range(1, steps + 1):
                mouse.move(coords=(x + (end_x - x) * index // steps, y + (end_y - y) * index // steps))
                time.sleep(0.012)
            mouse.release(coords=(end_x, end_y))
        else:
            raise ActionForbidden(f"Desteklenmeyen fare eylemi: {action}")
        return f"✅ Fare eylemi uygulandı: {action} ({x}, {y})"


class CapabilityTools:
    def check(self) -> str:
        executables = ("python", "git", "node", "npm", "ffmpeg", "ollama", "winget")
        modules = ("PIL", "pywinauto", "win32api", "pytest", "requests")
        return json.dumps({
            "executables": {name: shutil.which(name) for name in executables},
            "python_modules": {name: importlib.util.find_spec(name) is not None for name in modules},
            "python": sys.version,
            "note": "Eksik Python paketi veya kullanıcı kapsamlı uygulama install_dependency ile onay alınarak kurulabilir. Sürücü ve sistem değişiklikleri yapılmaz.",
        }, ensure_ascii=False)

    def install(self, args: dict) -> str:
        package = str(args.get("package", "")).strip()
        kind = str(args.get("kind", "python"))
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", package):
            raise ActionForbidden("Paket adı yalnızca PyPI paketi veya winget kimliği olabilir; URL ve seçenek kabul edilmez.")
        if kind == "python":
            executable = "python" if getattr(sys, "frozen", False) else sys.executable
            command = [executable, "-m", "pip", "install", "--disable-pip-version-check", package]
            if getattr(sys, "frozen", False):
                command.insert(-1, "--user")
        elif kind == "application":
            if not shutil.which("winget"):
                raise ActionForbidden("winget bulunamadı; uygulamayı resmi sitesinden araştırıp kullanıcıya bildir.")
            command = [
                "winget", "install", "--id", package, "--exact", "--scope", "user",
                "--accept-package-agreements", "--accept-source-agreements", "--disable-interactivity",
            ]
        else:
            raise ActionForbidden("Kurulum türü python veya application olmalı.")
        environment = os.environ.copy()
        environment.pop("GEMINI_API_KEY", None)
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=300, env=environment, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired:
            raise ActionForbidden("Kurulum beş dakikada tamamlanmadı; paket yöneticisini kontrol et.")
        output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
        if result.returncode:
            return f"❌ Kurulum tamamlanamadı ({result.returncode}): {package}\n{output[-8000:]}"
        verification = importlib.util.find_spec(package.replace("-", "_")) is not None if kind == "python" else True
        note = "Paket kuruldu; modül adı farklıysa check_capabilities ile doğrula." if not verification and kind == "python" else "Kurulum doğrulandı."
        return f"✅ {package} kuruldu. {note}\n{output[-4000:]}"
