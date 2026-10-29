import faulthandler
import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_CRASH_LOG = None


def _enable_crash_log() -> None:
    """Native çökmelerde Python yığınlarını kalıcı olarak kaydet."""
    global _CRASH_LOG
    try:
        log_dir = ROOT / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        _CRASH_LOG = (log_dir / "jarvis_crash.log").open("a", encoding="utf-8", buffering=1)
        _CRASH_LOG.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} pid={os.getpid()} ===\n")
        faulthandler.enable(file=_CRASH_LOG, all_threads=True)
    except Exception:
        _CRASH_LOG = None


def _report_startup_error(error: BaseException) -> None:
    """Başlatma hatasını sessizce yutmak yerine log'a yazar ve ekranda gösterir."""
    details = traceback.format_exc()
    try:
        log_dir = ROOT / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "baslatma_hatasi.log").write_text(details, encoding="utf-8")
    except OSError:
        pass

    message = (
        "Jarvis başlatılamadı.\n\n"
        f"{type(error).__name__}: {error}\n\n"
        "Eksik bileşen hatasıysa 'Jarvis Kur.bat' dosyasını çalıştırın.\n"
        "Ayrıntı: logs\\baslatma_hatasi.log"
    )
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "Jarvis", 0x10)
    except Exception:
        print(message, file=sys.stderr)


def main() -> int:
    _enable_crash_log()
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

    app = QApplication(sys.argv)
    app.setApplicationName("Jarvis")

    from app.ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    window.raise_()
    window.activateWindow()
    return app.exec()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        _report_startup_error(error)
        raise SystemExit(1)
