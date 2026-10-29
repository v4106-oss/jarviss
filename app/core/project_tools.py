from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

from app.core.safety import ActionForbidden, PathPolicy, SafetyError, UndoStore

IGNORED_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build", ".idea", ".vscode"}
WORKSPACE_MARKER = ".jarvis-workspace.json"
TEST_MARKER = ".jarvis-last-test.json"


class ProjectTools:
    def __init__(self, policy: PathPolicy, undo: UndoStore, settings) -> None:
        self.policy = policy
        self.undo = undo
        self.settings = settings

    def _directory(self, raw) -> Path:
        path = self.policy.resolve(raw)
        if not path.is_dir():
            raise ActionForbidden(f"Proje klasörü bulunamadı: {path}")
        return path

    @staticmethod
    def _walk(root: Path):
        for current, directories, files in os.walk(root):
            directories[:] = sorted(name for name in directories if name not in IGNORED_DIRS and not name.startswith("."))
            for name in sorted(files):
                if not name.startswith("."):
                    yield Path(current) / name

    def list_files(self, args: dict) -> str:
        root = self._directory(args.get("path") or ".")
        recursive = bool(args.get("recursive", True))
        limit = min(max(int(args.get("limit", 200)), 1), 500)
        candidates = self._walk(root) if recursive else sorted(p for p in root.iterdir() if p.is_file() and not p.name.startswith("."))
        rows = []
        for path in candidates:
            rows.append(str(path.relative_to(root)))
            if len(rows) >= limit:
                break
        total = sum(1 for _ in self._walk(root)) if recursive else sum(1 for p in root.iterdir() if p.is_file())
        return json.dumps({"root": str(root), "files": rows, "shown": len(rows), "total": total}, ensure_ascii=False)

    def read_file(self, args: dict) -> str:
        path = self.policy.resolve(args["path"])
        if not path.is_file():
            raise ActionForbidden(f"Dosya bulunamadı: {path}")
        if path.stat().st_size > 2_000_000:
            raise ActionForbidden("Dosya 2 MB sınırını aşıyor; satır aralığıyla daha küçük bir dosya seç.")
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            raise ActionForbidden("Yalnızca UTF-8 metin/kod dosyaları okunabilir.")
        start = min(max(int(args.get("start_line", 1)), 1), max(len(lines), 1))
        end = min(max(int(args.get("end_line", start + 399)), start), start + 999)
        selected = lines[start - 1:end]
        content = "\n".join(f"{number}: {line}" for number, line in enumerate(selected, start))
        return f"Dosya: {path}\nSatırlar: {start}-{min(end, len(lines))}/{len(lines)}\n{content[:200000]}"

    def search(self, args: dict) -> str:
        root = self._directory(args.get("path") or ".")
        query = str(args["query"])
        if not query:
            raise ActionForbidden("Aranacak metin boş olamaz.")
        pattern = re.compile(query if args.get("regex") else re.escape(query), re.IGNORECASE)
        suffix = str(args.get("file_pattern", "*"))
        matches = []
        scanned = 0
        for path in self._walk(root):
            if not path.match(suffix) or path.stat().st_size > 1_000_000:
                continue
            scanned += 1
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for number, line in enumerate(lines, 1):
                if pattern.search(line):
                    matches.append({"file": str(path.relative_to(root)), "line": number, "text": line.strip()[:500]})
                    if len(matches) >= 100:
                        return json.dumps({"matches": matches, "truncated": True, "scanned": scanned}, ensure_ascii=False)
        return json.dumps({"matches": matches, "truncated": False, "scanned": scanned}, ensure_ascii=False)

    @staticmethod
    def _diff(path: Path, before: str, after: str) -> str:
        return "".join(difflib.unified_diff(
            before.splitlines(True), after.splitlines(True),
            fromfile=f"a/{path.name}", tofile=f"b/{path.name}",
        ))[:50000]

    def preview_replace(self, args: dict) -> str:
        path = self.policy.resolve(args["path"])
        before = path.read_text(encoding="utf-8")
        old, new = str(args["old_text"]), str(args["new_text"])
        count = before.count(old) if old else 0
        if count != 1:
            raise ActionForbidden(f"Değiştirilecek metin tam bir kez bulunmalı; bulunan: {count}")
        diff = self._diff(path, before, before.replace(old, new, 1))
        return f"ÖNİZLEME - dosya henüz değiştirilmedi: {path}\n{diff or 'Fark yok.'}"

    def replace(self, args: dict) -> str:
        path = self.policy.resolve(args["path"])
        if not path.is_file():
            raise ActionForbidden(f"Dosya bulunamadı: {path}")
        before = path.read_text(encoding="utf-8")
        old, new = str(args["old_text"]), str(args["new_text"])
        count = before.count(old) if old else 0
        expected = count if args.get("replace_all") else 1
        if count == 0 or (not args.get("replace_all") and count != 1):
            raise ActionForbidden(f"Metin beklenen biçimde bulunamadı; eşleşme: {count}. Önce dosyayı okuyup tam metni ver.")
        after = before.replace(old, new) if args.get("replace_all") else before.replace(old, new, 1)
        if after == before:
            raise ActionForbidden("Eski ve yeni metin aynı; değişiklik yapılmadı.")
        if len(after) > 5_000_000:
            raise ActionForbidden("Sonuç dosyası 5 milyon karakter sınırını aşıyor.")
        changes = self.undo.begin("replace_exact")
        try:
            changes.backup(path)
            path.write_text(after, encoding="utf-8")
            if path.read_text(encoding="utf-8") != after:
                raise OSError("Yazma doğrulaması başarısız.")
            changes.commit()
        except BaseException:
            changes.discard()
            raise
        diff = self._diff(path, before, after)
        return f"✅ {expected} eşleşme değiştirildi ve doğrulandı: {path}\n{diff[:20000]}"

    def _workspace_digest(self, root: Path) -> str:
        digest = hashlib.sha256()
        for path in self._walk(root):
            if path.name in {WORKSPACE_MARKER, TEST_MARKER} or path.suffix == ".pyc":
                continue
            digest.update(str(path.relative_to(root)).encode("utf-8"))
            digest.update(path.read_bytes())
        return digest.hexdigest()

    def run_tests(self, args: dict) -> str:
        root = self._directory(args.get("working_directory") or ".")
        runner = str(args.get("runner", "python_unittest"))
        target = str(args.get("target", ".")).strip()
        if any(char in target for char in "\x00\r\n;&|<>"):
            raise ActionForbidden("Test hedefi komut veya kabuk operatörü içeremez.")
        target_path = self.policy.resolve(root / target)
        if not target_path.exists():
            raise ActionForbidden(f"Test hedefi bulunamadı: {target_path}")
        python = "python" if getattr(sys, "frozen", False) else sys.executable
        commands = {
            "python_unittest": [python, "-m", "unittest", "discover", "-s", str(target_path)],
            "pytest": [python, "-m", "pytest", str(target_path)],
            "npm_test": ["npm", "test"],
            "npm_build": ["npm", "run", "build"],
            "typescript": ["npx", "tsc", "--noEmit"],
        }
        if runner not in commands:
            raise ActionForbidden("Desteklenmeyen test türü.")
        environment = os.environ.copy()
        environment.pop("GEMINI_API_KEY", None)
        environment["PYTHONIOENCODING"] = "utf-8"
        try:
            result = subprocess.run(
                commands[runner], cwd=str(root), env=environment, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=120,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except FileNotFoundError:
            raise ActionForbidden(f"Test çalıştırıcısı kurulu değil: {commands[runner][0]}")
        except subprocess.TimeoutExpired:
            raise ActionForbidden("Test 120 saniyede zaman aşımına uğradı.")
        output = ((result.stdout or "") + ("\n" + result.stderr if result.stderr else "")).strip()
        success = result.returncode == 0
        if (root / WORKSPACE_MARKER).is_file():
            (root / TEST_MARKER).write_text(json.dumps({
                "runner": runner, "success": success, "finished": time.time(),
                "digest": self._workspace_digest(root),
            }), encoding="utf-8")
        return f"{'✅' if success else '❌'} Test çıkış kodu: {result.returncode}\n{output[-16000:] or 'Çıkış yok.'}"

    def create_project(self, args: dict) -> str:
        name = str(args["name"]).strip()
        if not re.fullmatch(r"[\wçğıöşüÇĞİÖŞÜ -]{1,80}", name):
            raise ActionForbidden("Proje adı yalnızca harf, sayı, boşluk, tire ve alt çizgi içerebilir.")
        documents = next((root for root in self.policy.roots if root.name.casefold() == "documents"), None)
        if documents is None:
            documents = Path.home() / "Documents"
        root = self.policy.resolve(documents / "Jarvis_Projeler" / name)
        if root.exists():
            raise ActionForbidden(f"Proje zaten var: {root}")
        template = str(args.get("template", "empty"))
        changes = self.undo.begin("create_project")
        try:
            root.mkdir(parents=True)
            if template == "python":
                (root / "main.py").write_text('def main():\n    print("Merhaba")\n\n\nif __name__ == "__main__":\n    main()\n', encoding="utf-8")
                (root / "test_main.py").write_text("import unittest\n\n\nclass SmokeTests(unittest.TestCase):\n    def test_smoke(self):\n        self.assertTrue(True)\n", encoding="utf-8")
            elif template == "web":
                (root / "index.html").write_text("<!doctype html>\n<html lang=\"tr\"><meta charset=\"utf-8\"><title>Yeni Proje</title><h1>Yeni Proje</h1>\n", encoding="utf-8")
            elif template != "empty":
                raise ActionForbidden("Şablon empty, python veya web olmalı.")
            changes.created(root)
            changes.commit()
        except BaseException:
            changes.discard()
            raise
        archive = shutil.make_archive(str(root), "zip", root.parent, root.name)
        subprocess.Popen(["explorer.exe", f"/select,{archive}"])
        return f"✅ Proje oluşturuldu: {root}\nZip: {archive}"

    def prepare_git_branch(self, args: dict) -> str:
        root = self._directory(args["working_directory"])
        branch = str(args["branch"]).strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,79}", branch) or ".." in branch or branch.endswith("/"):
            raise ActionForbidden("Geçerli bir Git dal adı gerekli.")
        if not (root / ".git").exists():
            raise ActionForbidden("Bu klasör bir Git deposu değil.")
        result = subprocess.run(
            ["git", "switch", "-c", branch], cwd=str(root), capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        if result.returncode:
            raise ActionForbidden(f"Git dalı oluşturulamadı: {result.stderr.strip()[:1000]}")
        return f"✅ Ayrı Git dalı hazır: {branch}. Commit yalnızca kullanıcı açıkça isterse yapılır."

    def prepare_self_workspace(self, args: dict) -> str:
        source = Path(__file__).resolve().parents[2]
        documents = next((root for root in self.policy.roots if root.name.casefold() == "documents"), None)
        if documents is None:
            raise ActionForbidden("Öz-geliştirme kopyası için Belgeler klasörü gerekli.")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        destination = self.policy.resolve(documents / "Jarvis_Projeler" / f"Jarvis_Gelistirme_{stamp}")
        changes = self.undo.begin("prepare_self_workspace")
        try:
            shutil.copytree(source, destination, ignore=shutil.ignore_patterns(*IGNORED_DIRS, "*.pyc", "logs", "undo"))
            (destination / WORKSPACE_MARKER).write_text(json.dumps({"source": str(source), "created": time.time()}), encoding="utf-8")
            changes.created(destination)
            changes.commit()
        except BaseException:
            changes.discard()
            raise
        self.settings.add_root(str(destination))
        return f"✅ Ayrı çalışma kopyası hazır: {destination}\nBurada düzenle, testleri çalıştır; apply_self_changes ile ancak test başarılıysa uygula."

    def _self_workspace(self, raw) -> tuple[Path, Path]:
        workspace = self._directory(raw)
        marker = workspace / WORKSPACE_MARKER
        if not marker.is_file():
            raise ActionForbidden("Bu klasör Jarvis öz-geliştirme çalışma alanı değil.")
        data = json.loads(marker.read_text(encoding="utf-8"))
        source = Path(data["source"]).resolve()
        expected = Path(__file__).resolve().parents[2]
        if source != expected or not (source / "app").is_dir():
            raise ActionForbidden("Çalışma alanının kaynak Jarvis yolu doğrulanamadı.")
        return workspace, source

    def diff_self_workspace(self, args: dict) -> str:
        workspace, source = self._self_workspace(args.get("path"))
        rows = []
        for path in self._walk(workspace):
            if path.name in {WORKSPACE_MARKER, TEST_MARKER} or path.suffix == ".pyc":
                continue
            relative = path.relative_to(workspace)
            target = source / relative
            before = target.read_text(encoding="utf-8") if target.is_file() else ""
            try:
                after = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if before != after:
                rows.append(self._diff(relative, before, after))
        return "\n".join(rows)[:50000] or "Değişiklik yok."

    def apply_self_changes(self, args: dict) -> str:
        workspace, source = self._self_workspace(args.get("path"))
        report_path = workspace / TEST_MARKER
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ActionForbidden("Önce bu çalışma kopyasında run_tests ile başarılı test çalıştır.")
        if not report.get("success") or time.time() - float(report.get("finished", 0)) > 3600:
            raise ActionForbidden("Başarılı test kaydı yok veya bir saatten eski; testleri yeniden çalıştır.")
        if report.get("digest") != self._workspace_digest(workspace):
            raise ActionForbidden("Testten sonra dosyalar değişmiş; uygulamadan önce testleri yeniden çalıştır.")
        candidates = []
        for path in self._walk(workspace):
            if path.name in {WORKSPACE_MARKER, TEST_MARKER} or path.suffix not in {".py", ".json", ".md", ".txt", ".html", ".css", ".qss"}:
                continue
            relative = path.relative_to(workspace)
            target = source / relative
            if not path.is_file() or path.stat().st_size > 2_000_000 or not target.exists() and len(candidates) > 100:
                continue
            if target.is_file() and target.read_bytes() == path.read_bytes():
                continue
            candidates.append((path, target))
        if not candidates:
            return "Uygulanacak kod değişikliği yok."
        compile_result = subprocess.run(
            [sys.executable, "-m", "compileall", "-q", str(workspace / "app")],
            capture_output=True, text=True, timeout=60,
        )
        if compile_result.returncode:
            raise ActionForbidden(f"Kod derlenemedi: {compile_result.stderr[-2000:]}")
        changes = self.undo.begin("apply_self_changes")
        try:
            for path, target in candidates:
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    changes.backup(target)
                shutil.copy2(path, target)
                if not target.exists() or target.read_bytes() != path.read_bytes():
                    raise OSError(f"Doğrulama başarısız: {target}")
                if not (workspace / target.relative_to(source)).exists():
                    raise OSError("Çalışma kopyası doğrulanamadı.")
                if not any(entry.get("path") == str(target) for entry in changes.entries):
                    changes.created(target)
            changes.commit()
        except BaseException:
            changes.discard()
            raise
        return f"✅ Test edilmiş {len(candidates)} dosya Jarvis'e uygulandı. Değişiklikler yeniden başlatınca etkinleşir; geri alma yedeği hazır."
