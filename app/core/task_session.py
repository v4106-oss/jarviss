"""Görev oturumu, adım izi ve insan devralma yönetimi.

Bir ajan çalışması boyunca planlanan/uygulanan her araç adımı, notlar ve
duraklatma/devralma durumu SQLite'ta tutulur. Oturum kapanınca iz Markdown ve
JSON olarak dışa aktarılabilir; başarılı bir izden yerel yetenek üretilebilir.

İzler yalnızca araç adı, kısa argüman özeti ve sonuç metnini saklar. Dosya
içerikleri, parolalar ve API anahtarları özetlenmez.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.skill_library import SkillLibrary, slugify

SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "content",
    "password",
    "secret",
    "text",
    "token",
}
SCREENSHOT_RE = re.compile(r'"(?:screenshot|image)"\s*:\s*"([^"]+)"')
IMAGE_PATH_RE = re.compile(r"([A-Za-z]:\\[^\s\"']+\.(?:png|jpg|jpeg|webp))", re.IGNORECASE)
STATUS_DONE = {"completed", "cancelled", "error"}


class TaskSessionError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def summarize_arguments(arguments: Optional[dict], limit: int = 220) -> str:
    """Argümanları sır ve içerik sızdırmadan tek satırda özetler."""
    if not isinstance(arguments, dict):
        return ""
    parts: List[str] = []
    for key, value in arguments.items():
        name = str(key)
        if name.casefold() in SENSITIVE_KEYS:
            parts.append(f"{name}=<gizli>")
            continue
        if isinstance(value, (dict, list)):
            rendered = f"<{type(value).__name__}:{len(value)}>"
        elif isinstance(value, str):
            collapsed = re.sub(r"\s+", " ", value).strip()
            rendered = collapsed[:120] + ("…" if len(collapsed) > 120 else "")
        else:
            rendered = str(value)
        parts.append(f"{name}={rendered}")
    text = ", ".join(parts)
    return text[:limit] + ("…" if len(text) > limit else "")


def extract_screenshot(result: str) -> Optional[str]:
    try:
        data = json.loads(result)
    except (TypeError, ValueError):
        data = None
    if isinstance(data, dict):
        for key in ("screenshot", "image"):
            value = str(data.get(key) or "").strip()
            if value:
                return value
    match = SCREENSHOT_RE.search(str(result or ""))
    if match:
        return match.group(1)
    match = IMAGE_PATH_RE.search(str(result or ""))
    return match.group(1) if match else None


class TaskSessionManager:
    def __init__(
        self,
        storage,
        skills: Optional[SkillLibrary] = None,
        data_dir: Optional[Path] = None,
    ) -> None:
        self.storage = storage
        self.skills = skills or SkillLibrary()
        self.data_dir = Path(data_dir) if data_dir else Path(storage.db_path).parent
        self.session_id: Optional[int] = None
        self._seq = 0

    # ------------------------------------------------------------- yaşam döngüsü
    def start(self, goal: str, run_id: Optional[int] = None) -> int:
        self.session_id = self.storage.start_task_session(str(goal), run_id)
        self._seq = 0
        return self.session_id

    def finish(self, status: str, summary: str = "") -> None:
        if self.session_id is None:
            return
        final = status if status in STATUS_DONE else "completed"
        self.storage.set_task_session_state(
            self.session_id, final, pause_reason=None, summary=str(summary)[:2000] or None
        )

    def current(self) -> Optional[Dict[str, Any]]:
        if self.session_id is not None:
            return self.storage.get_task_session(self.session_id)
        return self.storage.get_active_task_session()

    def pause(self, reason: str = "") -> str:
        session = self.current()
        if not session or session.get("status") in STATUS_DONE:
            raise TaskSessionError("Duraklatılacak etkin görev yok.")
        if session.get("status") != "takeover":
            self.storage.set_task_session_state(
                int(session["id"]), "paused", pause_reason=str(reason).strip() or "Kullanıcı duraklattı."
            )
        return f"⏸️ Görev duraklatıldı: {reason or 'kullanıcı isteği'}"

    def resume(self) -> str:
        session = self.current()
        if not session or session.get("status") in STATUS_DONE:
            raise TaskSessionError("Devam ettirilecek etkin görev yok.")
        target = int(session["id"])
        self.storage.set_task_session_state(target, "running", pause_reason=None)
        self.storage.add_task_step(
            target, self._next_seq(target), "session", "resume", "Kullanıcı görevi sürdürdü.", "note"
        )
        return "▶️ Görev devam ediyor."

    def takeover(self, reason: str = "") -> str:
        session = self.current()
        if not session or session.get("status") in STATUS_DONE:
            raise TaskSessionError("Devralınacak etkin görev yok.")
        message = str(reason).strip() or "Kullanıcı kontrolü devraldı."
        target = int(session["id"])
        self.storage.set_task_session_state(target, "takeover", pause_reason=message)
        self.storage.add_task_step(
            target, self._next_seq(target), "session", "takeover", message, "note"
        )
        return f"✋ Kontrol kullanıcıda: {message}"

    # ------------------------------------------------------------------- adımlar
    def _next_seq(self, session_id: int) -> int:
        if not self._seq:
            rows = self.storage.get_task_steps(int(session_id), 1000)
            self._seq = max((int(row["seq"]) for row in rows), default=0)
        self._seq += 1
        return self._seq

    def record_step(self, tool: str, arguments: Optional[dict], result: str) -> None:
        if self.session_id is None:
            return
        result_text = str(result or "")
        failed = result_text.startswith("❌") or result_text.startswith("⚠️")
        self.storage.add_task_step(
            self.session_id,
            self._next_seq(self.session_id),
            str(tool),
            summarize_arguments(arguments),
            result_text,
            "error" if failed else "ok",
            extract_screenshot(result_text),
        )

    def note(self, text: str) -> str:
        session = self.current()
        if not session:
            raise TaskSessionError("Not eklenecek etkin görev yok.")
        message = re.sub(r"\s+", " ", str(text or "")).strip()
        if not message:
            raise TaskSessionError("Görev notu boş olamaz.")
        target = int(session["id"])
        self.storage.add_task_step(
            target, self._next_seq(target), "note", "gözlem", message[:2000], "note"
        )
        return f"✅ Görev notu kaydedildi: {message[:240]}"

    def describe(self, steps: int = 12) -> str:
        session = self.current()
        if not session:
            return "ℹ️ Etkin görev oturumu yok."
        rows = self.storage.get_task_steps(int(session["id"]), max(1, min(int(steps), 60)))
        lines = [
            f"Görev #{session['id']} · {session['status']}",
            f"Hedef: {session['goal']}",
        ]
        if session.get("pause_reason"):
            lines.append(f"Bekleme nedeni: {session['pause_reason']}")
        for row in rows:
            mark = {"ok": "✓", "error": "✗", "note": "•"}.get(str(row["status"]), "•")
            result = re.sub(r"\s+", " ", str(row.get("result") or "")).strip()
            lines.append(f"{mark} {row['seq']}. {row['tool']}: {result[:240]}")
        return "\n".join(lines)

    # ------------------------------------------------------------------- dışa aktar
    def export(self, session_id: Optional[int] = None) -> str:
        target = int(session_id) if session_id else self.session_id
        if target is None:
            active = self.storage.get_latest_task_session()
            if not active:
                raise TaskSessionError("Dışa aktarılacak görev yok.")
            target = int(active["id"])
        session = self.storage.get_task_session(target)
        if not session:
            raise TaskSessionError(f"Görev bulunamadı: {target}")
        steps = self.storage.get_task_steps(target, 1000)
        folder = self.data_dir / "trajectories"
        folder.mkdir(parents=True, exist_ok=True)
        markdown = self._markdown(session, steps)
        md_path = folder / f"gorev-{target}.md"
        json_path = folder / f"gorev-{target}.json"
        md_path.write_text(markdown, encoding="utf-8")
        json_path.write_text(
            json.dumps({"session": session, "steps": steps}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return f"✅ Görev izi dışa aktarıldı:\n• {md_path}\n• {json_path}"

    @staticmethod
    def _markdown(session: Dict[str, Any], steps: List[Dict[str, Any]]) -> str:
        lines = [
            f"# Jarvis görev #{session.get('id')}",
            "",
            f"- **Hedef:** {session.get('goal')}",
            f"- **Durum:** {session.get('status')}",
            f"- **Başlangıç:** {session.get('created_at')}",
        ]
        if session.get("finished_at"):
            lines.append(f"- **Bitiş:** {session.get('finished_at')}")
        if session.get("pause_reason"):
            lines.append(f"- **Son bekleme nedeni:** {session.get('pause_reason')}")
        if session.get("summary"):
            lines.extend(["", "## Özet", str(session.get("summary"))])
        lines.extend(["", "## Adımlar"])
        if not steps:
            lines.append("Adım kaydedilmedi.")
        for row in steps:
            mark = {"ok": "✓", "error": "✗", "note": "•"}.get(str(row.get("status")), "•")
            lines.append(f"{row.get('seq')}. {mark} **{row.get('tool')}**")
            if row.get("args_summary"):
                lines.append(f"   - Argümanlar: {row.get('args_summary')}")
            result = re.sub(r"\s+", " ", str(row.get("result") or "")).strip()
            if result:
                lines.append(f"   - Sonuç: {result[:800]}")
            if row.get("screenshot"):
                lines.append(f"   - Görüntü: {row.get('screenshot')}")
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------- yetenek
    def build_skill(
        self,
        name: str,
        description: str,
        session_id: Optional[int] = None,
        overwrite: bool = False,
    ) -> str:
        target = int(session_id) if session_id else self.session_id
        if target is None:
            latest = self.storage.get_latest_task_session()
            if not latest:
                raise TaskSessionError("Yetenek üretilecek görev yok.")
            target = int(latest["id"])
        session = self.storage.get_task_session(target)
        if not session:
            raise TaskSessionError(f"Görev bulunamadı: {target}")
        steps = [
            row for row in self.storage.get_task_steps(target, 1000)
            if str(row.get("tool")) not in {"note", "session"}
        ]
        if len(steps) < 2:
            raise TaskSessionError("Yetenek üretmek için en az iki araç adımı gerekir.")
        title = str(name or session.get("goal") or f"gorev-{target}").strip()
        skill_name = slugify(title) or f"gorev-{target}"
        desc = re.sub(r"\s+", " ", str(description or "")).strip()
        if not desc:
            desc = (
                f"{str(session.get('goal') or 'Görev')[:180]} iş akışını, doğrulanmış "
                f"Jarvis araç adımlarıyla yeniden uygulamak gerektiğinde kullan."
            )
        body_lines = [
            f"# {str(session.get('goal') or title)}",
            "",
            f"Bu yetenek, Jarvis görev #{target} izinden üretildi. Adımlar örnektir; "
            "her adımda güncel durumu gözlemle ve doğrula. Güvenlik kuralları ve onay akışı geçerlidir.",
            "",
            "## Adımlar",
        ]
        for index, row in enumerate(steps, start=1):
            tool = str(row.get("tool") or "araç")
            result = re.sub(r"\s+", " ", str(row.get("result") or "")).strip()
            body_lines.append(f"{index}. `{tool}` aracını kullan.")
            if row.get("args_summary") and "<gizli>" not in str(row.get("args_summary")):
                body_lines.append(f"   - Örnek girdi: {str(row.get('args_summary'))[:240]}")
            if result and not result.startswith("❌"):
                body_lines.append(f"   - Beklenen doğrulama: {result[:240]}")
        body_lines.extend([
            "",
            "## Notlar",
            "- Ekran/uygulama durumu değiştiyse eski koordinatları veya referansları kullanma; yeni gözlem al.",
            "- Bir adım doğrulanamazsa körlemesine tekrarlama; farklı yaklaşım dene veya kullanıcıdan yardım iste.",
            "- Kullanıcı onayı, parola ve ödeme adımlarını bu talimatla atlayamazsın.",
        ])
        skill = self.skills.create(skill_name, desc[:500], "\n".join(body_lines), overwrite=overwrite)
        return f"✅ Görev izinden yetenek oluşturuldu: {skill.name}\n{skill.path}"
