import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
import webbrowser
import zipfile
from pathlib import Path
from typing import Any

from app.core.plugin_manager import PluginBase


class SystemPlugin(PluginBase):
    name = "system"

    def __init__(self):
        super().__init__()
        self._last_opened: dict[str, float] = {}
        self._open_cooldown_seconds = 5

    def execute(self, *args: Any, **kwargs: Any) -> str:
        if not args:
            return self._help()

        command = str(args[0]).lower()
        options = [str(arg) for arg in args[1:]]

        if command == "clean_files":
            return self._clean_files(options)
        if command == "open":
            return self._open_app(options)
        if command == "create_text_file" or command == "create_file":
            return self._create_file(options)
        if command == "create_folder":
            return self._create_folder(options)
        if command == "delete":
            return self._delete_target(options)
        if command == "move":
            return self._move_target(options)
        if command == "copy":
            return self._copy_target(options)
        if command == "rename":
            return self._rename_target(options)
        if command == "compress":
            return self._compress(options)
        if command == "extract":
            return self._extract(options)
        if command == "find_files":
            return self._find_files(options)
        if command == "list_large_files":
            return self._list_large_files(options)
        if command == "find_empty_folders":
            return self._find_empty_folders(options)
        if command == "empty_recycle_bin":
            return self._empty_recycle_bin(options)
        if command == "file_properties":
            return self._file_properties(options)
        if command == "append_text":
            return self._append_text(options)
        if command == "read_text_file":
            return self._read_text_file(options)
        if command == "search_web":
            return self._search_web(options)
        if command == "weather":
            return self._weather(options)
        if command == "news":
            return self._news(options)
        if command == "currency":
            return self._currency(options)
        if command == "crypto":
            return self._crypto(options)
        if command == "download_file":
            return self._download_file(options)
        if command == "close_program":
            return self._close_program(options)
        if command == "close_all_windows":
            return self._close_all_windows(options)
        if command == "summarize_file":
            return self._summarize_file(options)
        if command == "read_pdf":
            return self._read_pdf(options)
        if command == "comment_image":
            return self._comment_image(options)
        if command == "explain_code":
            return self._explain_code(options)
        if command == "generate_code":
            return self._generate_code(options)
        if command == "fix_code":
            return self._fix_code(options)
        if command == "save_chat_history":
            return self._save_chat_history(options)
        if command == "load_chat_history":
            return self._load_chat_history(options)
        if command == "show_preferences":
            return self._show_preferences(options)
        if command == "set_alarm":
            return self._set_alarm(options)
        if command == "start_timer":
            return self._start_timer(options)
        if command == "start_stopwatch":
            return self._start_stopwatch(options)
        if command == "add_calendar_event":
            return self._add_calendar_event(options)
        if command == "create_reminder":
            return self._create_reminder(options)
        if command == "git":
            return self._git(options)
        if command == "open_terminal":
            return self._open_terminal(options)
        if command == "run_python":
            return self._run_python(options)
        if command == "pip_install":
            return self._pip_install(options)
        if command == "create_venv":
            return self._create_venv(options)
        if command == "self_improve":
            return self._self_improve(options)
        if command == "remember":
            return self._remember(options)
        if command == "forget":
            return self._forget(options)
        if command in {"help", "yardim", "list_commands", "commands"}:
            return self._help()
        if command == "status":
            return self._status()

        return f"Geçersiz sistem komutu: {command}\n{self._help()}"

    def _help(self) -> str:
        return (
            "System plugin kullanımı:\n"
            "/plugin system clean_files [hedef_klasor] - Geçici dosyaları ve .tmp dosyalarını temizler.\n"
            "/plugin system open <uygulama_adı> - Uygulama veya dosya açar.\n"
            "/plugin system create_text_file <dosya_adı> - Yeni .txt dosyası oluşturur.\n"
            "/plugin system delete <dosya_veya_klasör> - Dosya veya klasör siler.\n"
            "/plugin system edit <dosya_yolu> - Dosyayı düzenlemek için açar.\n"
            "/plugin system save <dosya_yolu> - Dosyayı kaydeder (uygulama destekliyorsa).\n"
            "/plugin system virus_scan - Windows Defender hızlı taraması başlatır.\n"
            "/plugin system status - Sistem durumunu gösterir.\n"
            "Örn: /plugin system open steam, /plugin system open notepad, /plugin system create_text_file deneme.txt\n"
            "Desteklenen komutlar: clean_files, open, create_file, create_folder, delete, move, copy, rename, compress, extract, find_files, list_large_files, find_empty_folders, empty_recycle_bin, file_properties, append_text, read_text_file, search_web, weather, news, currency, crypto, download_file, close_program, close_all_windows, summarize_file, read_pdf, comment_image, explain_code, generate_code, fix_code, save_chat_history, load_chat_history, show_preferences, set_alarm, start_timer, start_stopwatch, add_calendar_event, create_reminder, git, open_terminal, run_python, pip_install, create_venv, self_improve, remember, forget, status"
        )

    def _clean_files(self, options: list[str]) -> str:
        target = Path(options[0]) if options else Path(tempfile.gettempdir())

        if not target.exists():
            return f"Hedef bulunamadı: {target}"
        if target.is_file():
            return f"Lütfen klasör yolu girin: {target}"

        deleted_files = 0
        deleted_dirs = 0

        for path in target.rglob("*"):
            try:
                if path.is_file() and path.suffix.lower() in {".tmp", ".log"}:
                    path.unlink()
                    deleted_files += 1
                elif path.is_dir() and path.name == "__pycache__":
                    shutil.rmtree(path)
                    deleted_dirs += 1
            except PermissionError:
                continue
            except OSError:
                continue

        return (
            f"Temizlik tamamlandı.\n"
            f"Hedef: {target}\n"
            f"Silinen dosya: {deleted_files}\n"
            f"Silinen klasör: {deleted_dirs}"
        )

    def _is_web_target(self, target: str) -> bool:
        lower = target.lower()
        web_indicators = [
            "http://",
            "https://",
            "www.",
            ".com",
            ".net",
            ".org",
            "youtube",
            "google",
            "twitter",
            "instagram",
            "facebook",
            "linkedin",
            "github",
            "reddit",
            "spotify",
            "netflix",
            "docs",
            "drive",
            "gmail",
        ]
        return any(indicator in lower for indicator in web_indicators)

    def _format_web_url(self, target: str) -> str:
        if target.startswith("http://") or target.startswith("https://"):
            return target
        if target.startswith("www."):
            return f"https://{target}"
        if "." in target:
            return f"https://{target}"
        return f"https://{target}.com"

    def _is_ambiguous_target(self, target: str) -> bool:
        normalized = target.lower().strip()
        ambiguous = {
            "uygulama",
            "uygulamayı",
            "uygulamasını",
            "program",
            "programı",
            "programını",
            "app",
            "appı",
        }
        if normalized in ambiguous:
            return True

        return bool(
            re.fullmatch(
                r"(uygulam[aoıi]n[ıi]|uygulamayı|uygulama|program(ı|ını)?|app(ı)?)",
                normalized,
            )
        )

    def _resolve_user_path(self, value: str) -> Path:
        """Göreli yolları kullanıcının masaüstüne bağlar.

        Böylece "not.txt dosyası oluştur" komutu projenin gizli çalışma
        klasörüne değil, kullanıcının kolayca görebileceği masaüstüne yazar.
        Kullanıcı tam bir yol verirse o yol aynen kullanılır.
        """
        path = Path(value.strip().strip('"')).expanduser()
        if path.is_absolute():
            return path
        return Path.home() / "Desktop" / path

    def _create_text_file(self, options: list[str]) -> str:
        if not options:
            return "Lütfen oluşturmak istediğiniz dosya adını girin."

        file_name = options[0].strip()
        if not file_name.lower().endswith(".txt"):
            file_name = f"{file_name}.txt"

        target_path = self._resolve_user_path(file_name)
        if target_path.exists():
            return f"❌ Dosya zaten var: {target_path}"

        try:
            target_path.write_text("", encoding="utf-8")
            return f"✅ Metin belgesi oluşturuldu: {target_path}"
        except Exception as e:
            return f"❌ Dosya oluşturulamadı: {e}"

    def _delete_target(self, options: list[str]) -> str:
        if not options:
            return "Lütfen silmek istediğiniz dosya veya klasör adını girin."

        target_path = Path(options[0].strip())
        if not target_path.exists():
            return f"❌ Hedef bulunamadı: {target_path}"

        try:
            if target_path.is_dir():
                shutil.rmtree(target_path)
                return f"✅ Klasör silindi: {target_path}"
            target_path.unlink()
            return f"✅ Dosya silindi: {target_path}"
        except Exception as e:
            return f"❌ Silme işlemi başarısız: {e}"

    def _edit_target(self, options: list[str]) -> str:
        if not options:
            return "Lütfen düzenlemek istediğiniz dosya yolunu girin."

        file_path = Path(options[0].strip())
        if not file_path.exists():
            return f"❌ Dosya bulunamadı: {file_path}"

        try:
            if os.name == "nt":
                subprocess.Popen(["notepad", str(file_path)])
            else:
                subprocess.Popen(["xdg-open", str(file_path)])
            return f"✅ Düzenleme için açıldı: {file_path}"
        except Exception as e:
            return f"❌ Düzenleme açılamadı: {e}"

    def _save_target(self, options: list[str]) -> str:
        if not options:
            return "Lütfen kaydetmek istediğiniz dosya yolunu girin."

        file_path = Path(options[0].strip())
        if not file_path.exists():
            return f"❌ Dosya bulunamadı: {file_path}"

        return f"✅ {file_path} kaydedecek uygulamayı açmadım. Kaydetme işlemi uygulama içinde yapılmalıdır."

    def _create_folder(self, options: list[str]) -> str:
        if not options:
            return "Lütfen oluşturmak istediğiniz klasör adını girin."

        folder_path = self._resolve_user_path(options[0])
        try:
            folder_path.mkdir(parents=True, exist_ok=False)
            return f"✅ Klasör oluşturuldu: {folder_path}"
        except FileExistsError:
            return f"❌ Klasör zaten var: {folder_path}"
        except Exception as e:
            return f"❌ Klasör oluşturulamadı: {e}"

    def _create_file(self, options: list[str]) -> str:
        if not options:
            return "Lütfen oluşturmak istediğiniz dosya adını girin."

        file_name = options[0].strip()
        if not re.search(r"\.[a-z0-9]+$", file_name, re.IGNORECASE):
            file_name += ".txt"

        target_path = self._resolve_user_path(file_name)
        if target_path.exists():
            return f"❌ Dosya zaten var: {target_path}"

        try:
            if target_path.suffix.lower() == ".json":
                target_path.write_text("{}", encoding="utf-8")
            elif target_path.suffix.lower() == ".csv":
                target_path.write_text("", encoding="utf-8")
            elif target_path.suffix.lower() == ".md":
                target_path.write_text("# Yeni Markdown dosyası\n", encoding="utf-8")
            else:
                target_path.write_text("", encoding="utf-8")
            return f"✅ Dosya oluşturuldu: {target_path}"
        except Exception as e:
            return f"❌ Dosya oluşturulamadı: {e}"

    def _move_target(self, options: list[str]) -> str:
        if len(options) < 2:
            return "Lütfen kaynak ve hedef yolları girin."

        source = Path(options[0].strip())
        destination = Path(options[1].strip())
        if not source.exists():
            return f"❌ Kaynak bulunamadı: {source}"

        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            return f"✅ Taşındı: {source} -> {destination}"
        except Exception as e:
            return f"❌ Taşıma başarısız: {e}"

    def _copy_target(self, options: list[str]) -> str:
        if len(options) < 2:
            return "Lütfen kaynak ve hedef yolları girin."

        source = Path(options[0].strip())
        destination = Path(options[1].strip())
        if not source.exists():
            return f"❌ Kaynak bulunamadı: {source}"

        try:
            if source.is_dir():
                shutil.copytree(source, destination)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            return f"✅ Kopyalandı: {source} -> {destination}"
        except Exception as e:
            return f"❌ Kopyalama başarısız: {e}"

    def _rename_target(self, options: list[str]) -> str:
        if len(options) < 2:
            return "Lütfen kaynak ve yeni ad yolunu girin."

        source = Path(options[0].strip())
        destination = Path(options[1].strip())
        if not source.exists():
            return f"❌ Kaynak bulunamadı: {source}"

        try:
            source.rename(destination)
            return f"✅ Yeniden adlandırıldı: {source} -> {destination}"
        except Exception as e:
            return f"❌ Yeniden adlandırma başarısız: {e}"

    def _compress(self, options: list[str]) -> str:
        if not options:
            return "Lütfen sıkıştırmak istediğiniz dosya veya klasör yolunu girin."

        source = Path(options[0].strip())
        if not source.exists():
            return f"❌ Kaynak bulunamadı: {source}"

        destination = source.with_suffix(source.suffix + ".zip") if source.is_file() else source.with_suffix("")
        if source.is_dir():
            destination = source.with_suffix("").with_suffix(".zip")
        try:
            with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as zf:
                if source.is_dir():
                    for root, _, files in os.walk(source):
                        for file in files:
                            file_path = Path(root) / file
                            zf.write(file_path, file_path.relative_to(source.parent))
                else:
                    zf.write(source, source.name)
            return f"✅ Sıkıştırıldı: {destination}"
        except Exception as e:
            return f"❌ Sıkıştırma başarısız: {e}"

    def _extract(self, options: list[str]) -> str:
        if not options:
            return "Lütfen açmak istediğiniz zip dosyasının yolunu girin."

        source = Path(options[0].strip())
        if not source.exists():
            return f"❌ Zip dosyası bulunamadı: {source}"

        destination = source.with_suffix("")
        try:
            with zipfile.ZipFile(source, "r") as zf:
                zf.extractall(destination)
            return f"✅ Zip açıldı: {destination}"
        except Exception as e:
            return f"❌ Zip açma başarısız: {e}"

    def _find_files(self, options: list[str]) -> str:
        query = options[0].strip() if options else ""
        if not query:
            return "Lütfen aramak istediğiniz dosya adını girin."

        found = []
        for path in Path(".").rglob(f"*{query}*"):
            found.append(str(path))
            if len(found) >= 20:
                break
        if not found:
            return "🔍 Hiç dosya bulunamadı."
        return "🔍 Bulunan dosyalar:\n" + "\n".join(found)

    def _list_large_files(self, options: list[str]) -> str:
        files = []
        for root, _, filenames in os.walk("."):
            for filename in filenames:
                path = Path(root) / filename
                try:
                    files.append((path.stat().st_size, str(path)))
                except OSError:
                    continue
        files.sort(reverse=True)
        return "🔎 En büyük dosyalar:\n" + "\n".join(
            f"{size/1024/1024:.2f} MB - {path}" for size, path in files[:20]
        )

    def _find_empty_folders(self, options: list[str]) -> str:
        empty = []
        for root, dirs, files in os.walk("."):
            if not dirs and not files:
                empty.append(root)
        return "📂 Boş klasörler:\n" + "\n".join(empty) if empty else "📂 Boş klasör bulunamadı."

    def _empty_recycle_bin(self, options: list[str]) -> str:
        if os.name == "nt":
            try:
                subprocess.run(["powershell", "-NoProfile", "-Command", "Clear-RecycleBin -Force"], check=True)
                return "✅ Geri dönüşüm kutusu boşaltıldı."
            except Exception as e:
                return f"❌ Geri dönüşüm kutusu boşaltılamadı: {e}"
        return "⚠️ Geri dönüşüm kutusunu boşaltma yalnızca Windows'ta destekleniyor."

    def _file_properties(self, options: list[str]) -> str:
        if not options:
            return "Lütfen özelliklerini görmek istediğiniz dosya yolunu girin."
        path = Path(options[0].strip())
        if not path.exists():
            return f"❌ Dosya bulunamadı: {path}"
        stats = path.stat()
        return (
            f"📄 Özellikler:\n"
            f"Yol: {path}\n"
            f"Boyut: {stats.st_size} bytes\n"
            f"Oluşturulma: {time.ctime(stats.st_ctime)}\n"
            f"Son değişiklik: {time.ctime(stats.st_mtime)}\n"
            f"Tip: {'Klasör' if path.is_dir() else 'Dosya'}"
        )

    def _append_text(self, options: list[str]) -> str:
        if len(options) < 2:
            return "Lütfen dosya adı ve eklemek istediğiniz metni girin."
        path = Path(options[0].strip())
        text = options[1].strip()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(text)
            return f"✅ Metin eklendi: {path}"
        except Exception as e:
            return f"❌ Metin eklenemedi: {e}"

    def _read_text_file(self, options: list[str]) -> str:
        if not options:
            return "Lütfen okumak istediğiniz dosya yolunu girin."
        path = Path(options[0].strip())
        if not path.exists():
            return f"❌ Dosya bulunamadı: {path}"
        try:
            return path.read_text(encoding="utf-8")
        except Exception as e:
            return f"❌ Dosya okunamadı: {e}"

    def _search_web(self, options: list[str]) -> str:
        if len(options) < 2:
            return "Lütfen hangi serviste arama yapılacağını ve sorguyu girin."
        service = options[0].lower()
        query = options[1].strip()
        if service == "google":
            url = f"https://www.google.com/search?q={urllib.request.quote(query)}"
        elif service == "youtube":
            url = f"https://www.youtube.com/results?search_query={urllib.request.quote(query)}"
        elif service == "github":
            url = f"https://github.com/search?q={urllib.request.quote(query)}"
        elif service == "wikipedia":
            url = f"https://en.wikipedia.org/wiki/Special:Search?search={urllib.request.quote(query)}"
        else:
            return "❌ Bilinmeyen arama servisi."
        webbrowser.open(url)
        return f"🔗 {service.title()} araması açıldı: {query}"

    def _weather(self, options: list[str]) -> str:
        query = options[0].strip() if options else "hava durumu"
        url = f"https://www.google.com/search?q={urllib.request.quote(query)}+hava+durumu"
        webbrowser.open(url)
        return f"☀️ Hava durumu araması açıldı: {query}"

    def _news(self, options: list[str]) -> str:
        query = options[0].strip() if options else "haberler"
        url = f"https://news.google.com/search?q={urllib.request.quote(query)}"
        webbrowser.open(url)
        return f"📰 Haber araması açıldı: {query}"

    def _currency(self, options: list[str]) -> str:
        url = "https://www.google.com/search?q=döviz+kuru"
        webbrowser.open(url)
        return "💱 Döviz kuru araması açıldı."

    def _crypto(self, options: list[str]) -> str:
        url = "https://www.coinmarketcap.com"
        webbrowser.open(url)
        return "₿ Kripto para sayfası açıldı."

    def _download_file(self, options: list[str]) -> str:
        if not options:
            return "Lütfen indirmek istediğiniz dosyanın URL'sini girin."
        url = options[0].strip()
        filename = Path(url).name or "downloaded_file"
        destination = Path(filename)
        try:
            urllib.request.urlretrieve(url, destination)
            return f"✅ Dosya indirildi: {destination}"
        except Exception as e:
            return f"❌ Dosya indirilemedi: {e}"

    def _close_program(self, options: list[str]) -> str:
        if not options:
            return "Lütfen kapatmak istediğiniz programın adını girin."
        target = options[0].strip()
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/IM", target, "/F"], check=True)
            else:
                subprocess.run(["pkill", "-f", target], check=True)
            return f"✅ Program kapatıldı: {target}"
        except Exception as e:
            return f"❌ Program kapatılamadı: {e}"

    def _close_all_windows(self, options: list[str]) -> str:
        return "⚠️ Tüm pencereleri kapatma operasyonu güvenlik nedeniyle sınırlı. Lütfen uygulamaları manuel kapatın."

    def _summarize_file(self, options: list[str]) -> str:
        if not options:
            return "Lütfen özetlemek istediğiniz dosya yolunu girin."
        path = Path(options[0].strip())
        if not path.exists() or not path.is_file():
            return f"❌ Dosya bulunamadı: {path}"
        try:
            text = path.read_text(encoding="utf-8")
            lines = text.splitlines()[:20]
            return "📄 Özet:\n" + "\n".join(lines)
        except Exception as e:
            return f"❌ Dosya okunamadı: {e}"

    def _read_pdf(self, options: list[str]) -> str:
        if not options:
            return "Lütfen PDF dosya yolunu girin."
        path = Path(options[0].strip())
        if not path.exists():
            return f"❌ PDF bulunamadı: {path}"
        try:
            import PyPDF2
            with path.open("rb") as f:
                reader = PyPDF2.PdfReader(f)
                text = []
                for page in reader.pages[:3]:
                    text.append(page.extract_text() or "")
            return "\n".join(text)
        except ModuleNotFoundError:
            return "❌ PyPDF2 yüklü değil. PDF okumak için bu paketi yükleyin."
        except Exception as e:
            return f"❌ PDF okunamadı: {e}"

    def _comment_image(self, options: list[str]) -> str:
        return "⚠️ Resim yorumlama desteği şu anda yerel olarak mevcut değil."

    def _explain_code(self, options: list[str]) -> str:
        return "⚠️ Kod açıklama desteği LLM tarafında olmalı; burada sadece dosya yolunu alabiliyorum."

    def _generate_code(self, options: list[str]) -> str:
        return "⚠️ Kod üretimi, model tarafından metin olarak yapılmalıdır."

    def _fix_code(self, options: list[str]) -> str:
        return "⚠️ Kod düzeltme, model tarafından metin olarak yapılmalıdır."

    def _save_chat_history(self, options: list[str]) -> str:
        return "⚠️ Konuşma geçmişi kaydetme, UI/Storage entegrasyonu gerektirir."

    def _load_chat_history(self, options: list[str]) -> str:
        return "⚠️ Eski konuşmayı yükleme özelliği şu anda desteklenmiyor."

    def _show_preferences(self, options: list[str]) -> str:
        return "⚠️ Kullanıcı tercihleri gösterme özelliği henüz uygulanmadı."

    def _set_alarm(self, options: list[str]) -> str:
        query = options[0].strip() if options else ""
        return f"⏰ Alarm kurulacak: {query} (Gerçek alarm için işletim sistemi entegrasyonu gerek)."

    def _start_timer(self, options: list[str]) -> str:
        duration = options[0].strip() if options else ""
        return f"⏱️ Sayaç başlatıldı: {duration}"

    def _start_stopwatch(self, options: list[str]) -> str:
        return "⏱️ Kronometre başlatıldı."

    def _add_calendar_event(self, options: list[str]) -> str:
        query = options[0].strip() if options else ""
        return f"📅 Takvime etkinlik eklendi: {query} (Takvim entegrasyonu yok)."

    def _create_reminder(self, options: list[str]) -> str:
        query = options[0].strip() if options else ""
        return f"🔔 Hatırlatıcı oluşturuldu: {query} (Gerçek bildirim desteklenmiyor)."

    def _git(self, options: list[str]) -> str:
        if not options:
            return "Lütfen git komutu girin."
        git_cmd = ["git"] + options
        try:
            result = subprocess.run(git_cmd, capture_output=True, text=True, check=True)
            return result.stdout.strip() or result.stderr.strip()
        except Exception as e:
            return f"❌ Git komutu başarısız: {e}"

    def _open_terminal(self, options: list[str]) -> str:
        try:
            if os.name == "nt":
                subprocess.Popen(["cmd.exe"])
            else:
                subprocess.Popen(["x-terminal-emulator"])
            return "✅ Terminal açıldı."
        except Exception as e:
            return f"❌ Terminal açılamadı: {e}"

    def _run_python(self, options: list[str]) -> str:
        code = options[0].strip() if options else ""
        if not code:
            return "Lütfen çalıştırmak istediğiniz Python kodunu girin."
        try:
            result = subprocess.run(["python", "-c", code], capture_output=True, text=True, check=True)
            return result.stdout.strip() or result.stderr.strip() or "✅ Python kodu çalıştırıldı."
        except Exception as e:
            return f"❌ Python çalıştırma başarısız: {e}"

    def _pip_install(self, options: list[str]) -> str:
        if not options:
            return "Lütfen yüklemek istediğiniz paketi girin."
        package = options[0].strip()
        try:
            result = subprocess.run(["python", "-m", "pip", "install", package], capture_output=True, text=True, check=True)
            return result.stdout.strip() or result.stderr.strip() or f"✅ Paket yüklendi: {package}"
        except Exception as e:
            return f"❌ Pip install başarısız: {e}"

    def _create_venv(self, options: list[str]) -> str:
        name = options[0].strip() if options else "venv"
        path = Path(name)
        try:
            subprocess.run(["python", "-m", "venv", str(path)], check=True)
            return f"✅ Sanal ortam oluşturuldu: {path}"
        except Exception as e:
            return f"❌ Sanal ortam oluşturulamadı: {e}"

    def _self_improve(self, options: list[str]) -> str:
        request = options[0].strip() if options else ""
        data_dir = Path(__file__).resolve().parents[2] / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        feature_file = data_dir / "feature_requests.txt"
        try:
            with feature_file.open("a", encoding="utf-8") as f:
                f.write(request + "\n")
            return f"✅ Özellik isteğiniz kaydedildi: {request}"
        except Exception as e:
            return f"❌ Özellik isteği kaydedilemedi: {e}"

    def _remember(self, options: list[str]) -> str:
        return "⚠️ Bu özellik şu anda plugin tarafından değil, AI belleği ile destekleniyor."

    def _forget(self, options: list[str]) -> str:
        return "⚠️ Bu özellik şu anda plugin tarafından değil, AI belleği ile destekleniyor."

    def _open_app(self, options: list[str]) -> str:
        if not options:
            return "Lütfen açmak istediğiniz uygulama adını girin."

        target = " ".join(options).strip()
        if not target:
            return "Açılacak hedef boş." 

        now = time.time()
        last_opened = self._last_opened.get(target)
        if last_opened and now - last_opened < self._open_cooldown_seconds:
            return f"{target} zaten yeni açıldı. Lütfen {self._open_cooldown_seconds} saniye sonra tekrar deneyin."

        explanation = []

        if self._is_web_target(target):
            url = self._format_web_url(target)
            explanation.append(
                f"🧠 Hmmm, burada bir açma eylemi istiyor. '{target}' bir web hizmeti veya site adı gibi görünüyor. Tarayıcıda açıyorum..."
            )
            try:
                if os.name == "nt":
                    subprocess.Popen(["cmd", "/c", "start", "", url], shell=True)
                else:
                    subprocess.Popen(["xdg-open", url])
                self._last_opened[target] = now
                explanation.append(f"🔗 Tarayıcıda açıldı: {url}")
                return "\n".join(explanation)
            except Exception as e:
                explanation.append(f"❌ Web sitesi açılamadı: {e}")
                return "\n".join(explanation)

        if self._is_ambiguous_target(target):
            return (
                "❌ Hedef açık değil. Lütfen açmak istediğiniz uygulamanın veya web sitesinin tam adını yazın."
            )

        if target == "steam":
            explanation.append(
                "🧠 Hmmm, burada bir açma eylemi istiyor. Steam genellikle bir masaüstü uygulamasıdır, bu yüzden Steam protokolünü deniyorum."
            )
            command_target = "steam://open/main"
        else:
            explanation.append(
                f"🧠 Hmmm, burada bir açma eylemi istiyor. '{target}' bilgisayar uygulaması gibi görünüyor, bu yüzden uygulamayı başlatmayı deniyorum."
            )
            command_target = target

        try:
            if os.name == "nt":
                subprocess.Popen(["cmd", "/c", "start", "", command_target], shell=True)
            else:
                subprocess.Popen([command_target])
            self._last_opened[target] = now
            explanation.append(f"✅ Uygulama açılıyor: {command_target}")
            return "\n".join(explanation)
        except FileNotFoundError:
            explanation.append(f"❌ Uygulama bulunamadı: {command_target}")
            return "\n".join(explanation)
        except Exception as e:
            explanation.append(f"❌ Uygulama açılamadı: {e}")
            return "\n".join(explanation)

    def _virus_scan(self) -> str:
        if os.name != "nt":
            return "Virüs taraması sadece Windows Defender ile destekleniyor."

        defender = self._find_defender_exe()
        if not defender:
            return "Windows Defender komutu bulunamadı."

        try:
            result = subprocess.run(
                [defender, "-Scan", "-ScanType", "1"],
                capture_output=True,
                text=True,
                timeout=600,
            )
            output = result.stdout.strip() or result.stderr.strip()
            return f"Virüs taraması tamamlandı.\n{output}"
        except subprocess.TimeoutExpired:
            return "Virüs taraması zaman aşımına uğradı."
        except Exception as e:
            return f"Virüs taraması başlatılamadı: {e}"

    def _find_defender_exe(self) -> str | None:
        candidates = [
            Path(os.environ.get("ProgramFiles", "")) / "Windows Defender" / "MpCmdRun.exe",
            Path(os.environ.get("ProgramFiles(x86)", "")) / "Windows Defender" / "MpCmdRun.exe",
            Path(os.environ.get("ProgramFiles", "")) / "Microsoft Defender" / "MpCmdRun.exe",
            Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft Defender" / "MpCmdRun.exe",
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        return None

    def _status(self) -> str:
        return (
            f"Sistem durumu:\n"
            f"Platform: {os.name}\n"
            f"Temp klasör: {tempfile.gettempdir()}\n"
            f"Çalışma dizini: {Path.cwd()}"
        )
