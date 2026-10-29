import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.agent import ActionExecutor, ActionPlan, ActionPlanner, PlannedAction
from app.core.safety import (
    AccessMode,
    ActionForbidden,
    AuditLog,
    BackupError,
    KillSwitch,
    PathNotAllowed,
    PathPolicy,
    PermissionGate,
    Risk,
    SafetyServices,
    UndoStore,
    build_services,
    classify_action,
)
from app.core.settings import Settings


def real(path) -> Path:
    return Path(os.path.realpath(path))


class TempCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = real(self._tmp.name)
        self.root = self.tmp / "Desktop"
        self.root.mkdir()
        self.outside = self.tmp / "outside"
        self.outside.mkdir()
        (self.root / "JarvisApp").mkdir()


def make_services(case: TempCase, mode=AccessMode.APPROVAL, strict=True) -> SafetyServices:
    state = {"mode": mode}
    services = SafetyServices(
        policy=PathPolicy([case.root], [(case.root / "JarvisApp", "Jarvis'in kendi kodu")], base=case.root),
        gate=PermissionGate(lambda: state["mode"], lambda: strict),
        audit=AuditLog(case.tmp / "data" / "logs" / "audit.jsonl"),
        undo=UndoStore(case.tmp / "data" / "undo"),
        kill=KillSwitch(),
    )
    services.state = state  # testlerde modu canlı değiştirmek için
    return services


def action(kind, **args):
    return PlannedAction(kind, args, kind)


# ------------------------------------------------------------------ yol politikası
class PathPolicyTests(TempCase):
    def setUp(self):
        super().setUp()
        self.policy = PathPolicy([self.root], [(self.root / "JarvisApp", "Jarvis'in kendi kodu")], base=self.root)

    def test_allows_paths_inside_root_even_if_missing(self):
        self.assertEqual(self.policy.resolve(self.root / "a" / "b.txt"), self.root / "a" / "b.txt")

    def test_relative_paths_resolve_against_base(self):
        self.assertEqual(self.policy.resolve("sub/file.txt"), self.root / "sub" / "file.txt")

    def test_rejects_outside_and_dotdot_escape(self):
        with self.assertRaises(PathNotAllowed):
            self.policy.resolve(self.outside / "x.txt")
        with self.assertRaises(PathNotAllowed):
            self.policy.resolve(self.root / ".." / "outside" / "x.txt")

    def test_rejects_symlink_escape(self):
        try:
            os.symlink(self.outside, self.root / "link")
        except OSError as error:
            if getattr(error, "winerror", None) == 1314:
                self.skipTest("Sembolik bağ oluşturma ayrıcalığı yok.")
            raise
        with self.assertRaises(PathNotAllowed):
            self.policy.resolve(self.root / "link" / "x.txt")

    def test_rejects_forbidden_zone_inside_root(self):
        with self.assertRaises(PathNotAllowed) as caught:
            self.policy.resolve(self.root / "JarvisApp" / "app.py")
        self.assertIn("korunuyor", str(caught.exception))

    def test_rejects_secret_files_and_dirs(self):
        for name in (".env", ".env.local", "server.pem", "my.key", "id_rsa", "id_rsa.pub", "id_ed25519", "vault.kdbx"):
            with self.subTest(name=name), self.assertRaises(PathNotAllowed):
                self.policy.resolve(self.root / name)
        with self.assertRaises(PathNotAllowed):
            self.policy.resolve(self.root / ".ssh" / "config")

    def test_does_not_reject_lookalike_names(self):
        for name in ("environment.txt", "keyboard.txt", ".envrc", "monkey.txt", "notes.pem.txt"):
            with self.subTest(name=name):
                self.policy.resolve(self.root / name)

    def test_rejects_unc_streams_reserved_and_trailing_dot(self):
        for bad in ("\\\\server\\share\\x.txt", "//server/share/x.txt", str(self.root / "a.txt:evil"),
                    str(self.root / "CON"), str(self.root / "nul.txt"), str(self.root / "file."),
                    str(self.root / "bosluk "), "", "   "):
            with self.subTest(bad=bad), self.assertRaises(PathNotAllowed):
                self.policy.resolve(bad)
        self.policy.resolve(self.root / "console.txt")

    def test_protected_roots_and_executables(self):
        self.assertTrue(self.policy.is_protected_root(self.root))
        self.assertTrue(self.policy.is_protected_root(Path(Path.cwd().anchor)))
        self.assertFalse(self.policy.is_protected_root(self.root / "alt"))
        for name in ("a.BAT", "b.exe", "c.ps1", "d.py", "e.lnk", "f.vbs"):
            self.assertTrue(self.policy.is_executable(Path(name)), name)
        for name in ("a.txt", "b.pdf", "c.docx"):
            self.assertFalse(self.policy.is_executable(Path(name)), name)


# ----------------------------------------------------------------------- izin kapısı
class PermissionGateTests(unittest.TestCase):
    def decide(self, mode, risk, tainted=False, strict=True):
        return PermissionGate(lambda: mode, lambda: strict).decide(risk, tainted).action

    def test_approval_mode_matrix(self):
        m = AccessMode.APPROVAL
        self.assertEqual(self.decide(m, Risk.READ), "allow")
        self.assertEqual(self.decide(m, Risk.CREATE), "allow")
        self.assertEqual(self.decide(m, Risk.MODIFY), "ask")
        self.assertEqual(self.decide(m, Risk.DANGEROUS), "ask")
        self.assertEqual(self.decide(m, Risk.READ, tainted=True), "allow")
        self.assertEqual(self.decide(m, Risk.CREATE, tainted=True), "ask")
        self.assertEqual(self.decide(m, Risk.FORBIDDEN), "deny")

    def test_full_mode_matrix(self):
        m = AccessMode.FULL
        for risk in (Risk.READ, Risk.CREATE, Risk.MODIFY, Risk.DANGEROUS):
            self.assertEqual(self.decide(m, risk), "allow", risk)
        self.assertEqual(self.decide(m, Risk.CREATE, tainted=True), "allow")
        self.assertEqual(self.decide(m, Risk.MODIFY, tainted=True), "ask")
        self.assertEqual(self.decide(m, Risk.MODIFY, tainted=True, strict=False), "allow")
        self.assertEqual(self.decide(m, Risk.DANGEROUS, tainted=True), "ask")
        self.assertEqual(self.decide(m, Risk.DANGEROUS, tainted=True, strict=False), "allow")
        self.assertEqual(self.decide(m, Risk.FORBIDDEN), "deny")

    def test_forbidden_is_denied_in_every_mode_and_unknown_mode_is_approval(self):
        for mode in (AccessMode.APPROVAL, AccessMode.FULL, "saçma"):
            self.assertEqual(self.decide(mode, Risk.FORBIDDEN, strict=False), "deny")
        self.assertEqual(self.decide("saçma", Risk.MODIFY), "ask")

    def test_mode_is_read_live(self):
        state = {"mode": AccessMode.APPROVAL}
        gate = PermissionGate(lambda: state["mode"])
        self.assertEqual(gate.decide(Risk.DANGEROUS).action, "ask")
        state["mode"] = AccessMode.FULL
        self.assertEqual(gate.decide(Risk.DANGEROUS).action, "allow")


class SystemUriRiskTests(TempCase):
    def test_system_uri_is_dangerous_and_needs_approval(self):
        policy = PathPolicy([self.root], base=self.root)
        risk = classify_action("open_system_uri", {"uri": "steam://openurl/https://example.com"}, policy)
        self.assertEqual(risk, Risk.DANGEROUS)


class SaveFileDialogRiskTests(TempCase):
    def test_save_dialog_path_is_scoped_and_created_or_modified(self):
        policy = PathPolicy([self.root], base=self.root)
        target = self.root / "ev.png"
        self.assertEqual(classify_action("save_file_dialog", {"path": str(target)}, policy), Risk.CREATE)
        target.write_text("x", encoding="utf-8")
        self.assertEqual(classify_action("save_file_dialog", {"path": str(target)}, policy), Risk.MODIFY)
        with self.assertRaises(PathNotAllowed):
            classify_action("save_file_dialog", {"path": "C:/Windows/System32/evil.png"}, policy)


class DrawShapeRiskTests(TempCase):
    def test_draw_shape_is_dangerous_ui_action(self):
        policy = PathPolicy([self.root], base=self.root)
        risk = classify_action("draw_shape", {"window_title": "Paint", "shape": "rectangle"}, policy)
        self.assertEqual(risk, Risk.DANGEROUS)


class ContentToolRiskTests(TempCase):
    def test_download_is_size_gated_and_path_scoped(self):
        policy = PathPolicy([self.root], base=self.root)
        target = self.root / "indirilen.bin"
        self.assertEqual(
            classify_action("download_file", {"url": "https://example.com/x", "path": str(target)}, policy),
            Risk.CREATE,
        )
        target.write_bytes(b"eski")
        self.assertEqual(
            classify_action(
                "download_file",
                {"url": "https://example.com/x", "path": str(target), "overwrite": True},
                policy,
            ),
            Risk.MODIFY,
        )
        with self.assertRaises(PathNotAllowed):
            classify_action("download_file", {"url": "https://example.com/x", "path": str(self.outside / "x.bin")}, policy)

    def test_steam_workshop_open_is_dangerous_but_reads_are_not(self):
        policy = PathPolicy([self.root], base=self.root)
        self.assertEqual(classify_action("steam_workshop", {"action": "search"}, policy), Risk.READ)
        self.assertEqual(classify_action("steam_workshop", {"action": "status"}, policy), Risk.READ)
        self.assertEqual(classify_action("steam_workshop", {"action": "open", "item_id": "1"}, policy), Risk.DANGEROUS)
        with self.assertRaises(ActionForbidden):
            classify_action("steam_workshop", {"action": "subscribe"}, policy)

    def test_ocr_text_search_is_read_but_click_is_dangerous(self):
        policy = PathPolicy([self.root], base=self.root)
        self.assertEqual(
            classify_action("find_text_on_screen", {"text": "Abone Ol", "window_title": "Steam"}, policy),
            Risk.READ,
        )
        self.assertEqual(
            classify_action(
                "find_text_on_screen", {"text": "Abone Ol", "window_title": "Steam", "click": True}, policy
            ),
            Risk.DANGEROUS,
        )

    def test_home_simulation_status_is_read_and_changes_are_create(self):
        policy = PathPolicy([self.root], base=self.root)
        self.assertEqual(classify_action("home", {"action": "status"}, policy), Risk.READ)
        self.assertEqual(classify_action("home", {"action": "coffee_status"}, policy), Risk.READ)
        self.assertEqual(
            classify_action("home", {"action": "light", "room": "salon", "on": True}, policy),
            Risk.CREATE,
        )
        self.assertEqual(
            classify_action("home", {"action": "coffee_brew", "kind": "turk"}, policy),
            Risk.CREATE,
        )
        with self.assertRaises(ActionForbidden):
            classify_action("home", {"action": "unlock_front_door"}, policy)

    def test_window_control_risk_and_required_target(self):
        policy = PathPolicy([self.root], base=self.root)
        self.assertEqual(classify_action("window_control", {"action": "list"}, policy), Risk.READ)
        self.assertEqual(
            classify_action("window_control", {"action": "focus", "title": "Not Defteri"}, policy),
            Risk.MODIFY,
        )
        self.assertEqual(
            classify_action("window_control", {"action": "close", "handle": 42}, policy),
            Risk.DANGEROUS,
        )
        with self.assertRaises(ActionForbidden):
            classify_action("window_control", {"action": "focus"}, policy)
        with self.assertRaises(ActionForbidden):
            classify_action("window_control", {"action": "patlat", "title": "x"}, policy)

    def test_clipboard_clear_is_create_and_unknown_action_is_rejected(self):
        policy = PathPolicy([self.root], base=self.root)
        self.assertEqual(classify_action("clipboard", {"action": "read"}, policy), Risk.READ)
        self.assertEqual(classify_action("clipboard", {"action": "write", "text": "x"}, policy), Risk.CREATE)
        self.assertEqual(classify_action("clipboard", {"action": "clear"}, policy), Risk.CREATE)
        with self.assertRaises(ActionForbidden):
            classify_action("clipboard", {"action": "format"}, policy)

    def test_rich_screen_and_ui_actions_are_accepted(self):
        policy = PathPolicy([self.root], base=self.root)
        for action in (
            "set_value", "clear", "toggle", "check", "uncheck", "expand", "collapse",
            "select", "scroll", "scroll_to", "hover",
        ):
            self.assertEqual(
                classify_action("screen_act", {"element_id": 1, "action": action}, policy),
                Risk.DANGEROUS,
            )
            self.assertEqual(
                classify_action(
                    "ui_control", {"window_title": "W", "control_name": "C", "action": action}, policy
                ),
                Risk.DANGEROUS,
            )
        with self.assertRaises(ActionForbidden):
            classify_action("screen_act", {"element_id": 1, "action": "drag"}, policy)
        self.assertEqual(
            classify_action(
                "screen_act", {"element_id": 1, "action": "drag", "to_ref": "s1:e2"}, policy
            ),
            Risk.DANGEROUS,
        )


# --------------------------------------------------------------------------- günlük
class AuditLogTests(TempCase):
    def test_records_and_tails_entries(self):
        log = AuditLog(self.tmp / "audit.jsonl")
        log.record("create_folder", {"path": "x"}, Risk.CREATE, result="ok")
        log.record("delete_path", {"path": "y"}, Risk.DANGEROUS, result="ok")
        entries = log.tail(5)
        self.assertEqual([e["tool"] for e in entries], ["create_folder", "delete_path"])
        self.assertEqual(entries[1]["risk"], 3)

    def test_never_stores_file_content_or_secrets(self):
        log = AuditLog(self.tmp / "audit.jsonl")
        log.record("write_text_file", {"path": "x", "content": "ÇOK-GİZLİ-METİN", "api_key": "AQ.abc"}, Risk.CREATE)
        raw = (self.tmp / "audit.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("ÇOK-GİZLİ-METİN", raw)
        self.assertNotIn("AQ.abc", raw)
        entry = json.loads(raw)
        self.assertEqual(entry["args"]["content"], {"chars": len("ÇOK-GİZLİ-METİN")})

    def test_clips_long_values_and_rotates(self):
        log = AuditLog(self.tmp / "audit.jsonl", max_bytes=300)
        log.record("t", {"path": "p" * 1000})
        self.assertLessEqual(len(log.tail(1)[0]["args"]["path"]), 301)
        for _ in range(5):
            log.record("t", {"path": "p" * 200})
        self.assertTrue((self.tmp / "audit.1.jsonl").exists())

    def test_logging_failure_never_raises(self):
        blocker = self.tmp / "dosya"
        blocker.write_text("x")
        AuditLog(blocker / "alt" / "audit.jsonl").record("t", {"a": 1})  # klasör olamaz; sessizce geçmeli


# ------------------------------------------------------------------ geri alma deposu
class UndoStoreTests(TempCase):
    def setUp(self):
        super().setUp()
        self.store = UndoStore(self.tmp / "undo")

    def test_restores_modified_file_then_reports_nothing_left(self):
        target = self.root / "a.txt"
        target.write_text("eski")
        changes = self.store.begin("yaz")
        changes.backup(target)
        target.write_text("yeni")
        changes.commit()
        result = self.store.undo_last()
        self.assertTrue(result.ok)
        self.assertEqual(target.read_text(), "eski")
        again = self.store.undo_last()
        self.assertFalse(again.ok)

    def test_removes_created_file_only_if_unchanged(self):
        untouched, edited = self.root / "u.txt", self.root / "e.txt"
        for path in (untouched, edited):
            path.write_text("ilk")
        changes = self.store.begin("oluştur")
        changes.created(untouched)
        changes.created(edited)
        changes.commit()
        edited.write_text("kullanıcı değiştirdi")
        result = self.store.undo_last()
        self.assertFalse(untouched.exists())
        self.assertTrue(edited.exists())
        self.assertTrue(any("değiştirilmiş" in m for m in result.messages))

    def test_created_folder_tree_keeps_user_files(self):
        tree = self.root / "proje"
        (tree / "alt").mkdir(parents=True)
        (tree / "a.txt").write_text("1")
        (tree / "alt" / "b.txt").write_text("2")
        changes = self.store.begin("kopyala")
        changes.created(tree)
        changes.commit()
        (tree / "benim.txt").write_text("kullanıcı ekledi")
        result = self.store.undo_last()
        self.assertTrue((tree / "benim.txt").exists())
        self.assertFalse((tree / "a.txt").exists())
        self.assertTrue(any("korundu" in m for m in result.messages))

    def test_created_empty_and_full_tree_are_removed(self):
        tree = self.root / "t"
        (tree / "x").mkdir(parents=True)
        (tree / "x" / "f.txt").write_text("1")
        changes = self.store.begin("kopyala")
        changes.created(tree)
        changes.commit()
        self.store.undo_last()
        self.assertFalse(tree.exists())

    def test_moved_goes_back_and_refuses_to_clobber(self):
        src, dst = self.root / "s.txt", self.root / "d.txt"
        src.write_text("x")
        dst_target = dst
        src.rename(dst_target)
        changes = self.store.begin("taşı")
        changes.moved(src, dst)
        changes.commit()
        self.store.undo_last()
        self.assertTrue(src.exists() and not dst.exists())
        # eski konuma başka dosya konmuşsa üzerine yazmaz
        src.rename(dst)
        changes = self.store.begin("taşı")
        changes.moved(src, dst)
        changes.commit()
        src.write_text("başka")
        result = self.store.undo_last()
        self.assertEqual(src.read_text(), "başka")
        self.assertTrue(dst.exists())
        self.assertTrue(any("geri taşınmadı" in m for m in result.messages))

    def test_trashed_entry_explains_manual_restore(self):
        changes = self.store.begin("sil")
        changes.trashed(self.root / "x.txt")
        changes.commit()
        self.assertIn("Geri Dönüşüm Kutusu", self.store.undo_last().messages[0])

    def test_backup_limits(self):
        store = UndoStore(self.tmp / "u2", max_backup_bytes=10)
        big = self.root / "big.bin"
        big.write_bytes(b"x" * 100)
        with self.assertRaises(BackupError):
            store.begin("x").backup(big)
        with self.assertRaises(BackupError):
            self.store.begin("x").backup(self.root)

    def test_prunes_old_sets_and_ignores_empty_commits(self):
        store = UndoStore(self.tmp / "u3", max_sets=3)
        for index in range(5):
            f = self.root / f"f{index}.txt"
            f.write_text(str(index))
            changes = store.begin(f"islem{index}")
            changes.created(f)
            changes.commit()
        self.assertEqual(len(store._sets()), 3)
        self.assertEqual(store.last_label(), "islem4")
        empty = store.begin("bos")
        empty.commit()
        self.assertFalse(empty.dir.exists())
        self.assertEqual(store.last_label(), "islem4")

    def test_last_set_is_always_the_newest_even_within_the_same_instant(self):
        store = UndoStore(self.tmp / "u4", max_sets=100)
        for index in range(30):
            f = self.root / f"q{index}.txt"
            f.write_text(str(index))
            changes = store.begin(f"islem{index}")
            changes.created(f)
            changes.commit()
        for index in reversed(range(30)):
            self.assertEqual(store.last_label(), f"islem{index}")
            store.undo_last()

    def test_discard_removes_orphan_backup(self):
        f = self.root / "a.txt"
        f.write_text("x")
        changes = self.store.begin("yarım")
        changes.backup(f)
        self.assertTrue(changes.dir.exists())
        changes.discard()
        self.assertFalse(changes.dir.exists())


# ------------------------------------------------------------- yürütücü (uçtan uca)
class ExecutorTests(TempCase):
    def setUp(self):
        super().setUp()
        self.services = make_services(self)
        self.executor = ActionExecutor(self.services)

    def run_plan(self, *actions):
        return self.executor.execute_plan(ActionPlan(list(actions)))

    def test_nested_folder_creation_and_undo(self):
        deep = self.root / "a" / "b" / "c"
        self.assertTrue(self.run_plan(action("create_folder", path=str(deep)))[0].startswith("✅"))
        self.assertTrue(deep.is_dir())
        out = self.run_plan(action("undo_last"))[0]
        self.assertTrue(out.startswith("✅ Son değişiklik geri alındı"))
        self.assertFalse((self.root / "a").exists())
        self.assertTrue(self.run_plan(action("undo_last"))[0].startswith("❌"))

    def test_new_file_in_new_folder_and_undo(self):
        target = self.root / "mehmet" / "akif.txt"
        results = self.run_plan(
            action("create_folder", path=str(target.parent)),
            action("write_text_file", path=str(target), content="adana", overwrite=False),
        )
        self.assertTrue(all(r.startswith("✅") for r in results))
        self.assertEqual(target.read_text(), "adana")
        self.run_plan(action("undo_last"))  # dosya
        self.assertFalse(target.exists())
        self.run_plan(action("undo_last"))  # klasör
        self.assertFalse(target.parent.exists())

    def test_overwrite_is_modify_asks_in_approval_allows_in_full_and_is_undoable(self):
        target = self.root / "v.txt"
        target.write_text("v1")
        plan = ActionPlan([action("write_text_file", path=str(target), content="v2", overwrite=True)])
        self.assertEqual(self.executor.assess(plan).decision, "ask")
        self.services.state["mode"] = AccessMode.FULL
        self.assertEqual(self.executor.assess(plan).decision, "allow")
        self.assertTrue(self.executor.execute_plan(plan)[0].startswith("✅"))
        self.assertEqual(target.read_text(), "v2")
        self.run_plan(action("undo_last"))
        self.assertEqual(target.read_text(), "v1")

    def test_write_without_overwrite_refuses_and_leaves_no_undo_entry(self):
        target = self.root / "var.txt"
        target.write_text("kalsın")
        out = self.run_plan(action("write_text_file", path=str(target), content="x", overwrite=False))[0]
        self.assertTrue(out.startswith("❌"))
        self.assertEqual(target.read_text(), "kalsın")
        self.assertIsNone(self.services.undo.last_label())

    def test_append_creates_backup_and_is_undoable(self):
        target = self.root / "log.txt"
        target.write_text("1\n")
        self.run_plan(action("append_text_file", path=str(target), content="2\n"))
        self.assertEqual(target.read_text(), "1\n2\n")
        self.run_plan(action("undo_last"))
        self.assertEqual(target.read_text(), "1\n")

    def test_copy_and_move_and_their_undo(self):
        src = self.root / "src"
        src.mkdir()
        (src / "f.txt").write_text("x")
        copy, moved = self.root / "kopya", self.root / "tasindi"
        self.assertTrue(self.run_plan(action("copy_path", source=str(src), destination=str(copy)))[0].startswith("✅"))
        self.assertTrue((copy / "f.txt").exists())
        self.run_plan(action("undo_last"))
        self.assertFalse(copy.exists())
        self.assertTrue(self.run_plan(action("move_path", source=str(src), destination=str(moved)))[0].startswith("✅"))
        self.assertTrue((moved / "f.txt").exists() and not src.exists())
        self.run_plan(action("undo_last"))
        self.assertTrue((src / "f.txt").exists() and not moved.exists())
        self.assertTrue(self.run_plan(action("move_path", source=str(src), destination=str(self.root)))[0].startswith("❌"))

    def test_delete_uses_recycle_bin_and_asks_in_approval_mode(self):
        target = self.root / "sil.txt"
        target.write_text("x")
        fake = types.ModuleType("send2trash")
        fake.send2trash = lambda path: os.remove(path)
        plan = ActionPlan([action("delete_path", path=str(target))])
        self.assertEqual(self.executor.assess(plan).decision, "ask")
        with patch.dict(sys.modules, {"send2trash": fake}):
            self.assertTrue(self.executor.execute_plan(plan)[0].startswith("✅ Geri Dönüşüm"))
        self.assertFalse(target.exists())
        self.assertIn("Geri Dönüşüm Kutusu", self.run_plan(action("undo_last"))[0])

    def test_forbidden_actions_are_denied_and_never_executed_in_any_mode(self):
        (self.root / "x.bat").write_text("echo hi")
        cases = [
            action("delete_path", path=str(self.root)),                         # kök klasör
            action("move_path", source=str(self.root), destination=str(self.root / "z")),
            action("write_text_file", path=str(self.root / ".env"), content="K=1", overwrite=False),
            action("write_text_file", path=str(self.root / "JarvisApp" / "a.py"), content="", overwrite=False),
            action("write_text_file", path=str(self.outside / "o.txt"), content="", overwrite=False),
            action("open_path", path=str(self.root / "x.bat")),                 # çalıştırılabilir
            action("shell", command="del /f /s /q C:\\"),                        # bilinmeyen araç
        ]
        for mode in (AccessMode.APPROVAL, AccessMode.FULL):
            self.services.state["mode"] = mode
            for case in cases:
                with self.subTest(mode=mode, action=case.action_type, args=case.arguments):
                    plan = ActionPlan([case])
                    self.assertEqual(self.executor.assess(plan).decision, "deny")
                    self.assertTrue(self.executor.execute_plan(plan)[0].startswith("❌"))
        self.assertFalse((self.outside / "o.txt").exists())
        self.assertFalse((self.root / ".env").exists())
        self.assertTrue(self.root.exists())

    def test_assess_levels(self):
        plan = ActionPlan([action("create_folder", path=str(self.root / "yeni"))])
        self.assertEqual(self.executor.assess(plan).decision, "allow")
        self.assertEqual(self.executor.assess(plan, tainted=True).decision, "ask")
        mixed = ActionPlan([action("create_folder", path=str(self.root / "k")), action("undo_last")])
        self.assertEqual(self.executor.assess(mixed).decision, "ask")

    def test_kill_switch_stops_remaining_steps(self):
        plan = ActionPlan([action("create_folder", path=str(self.root / name)) for name in ("k1", "k2", "k3")])
        results = self.executor.execute_plan(plan, on_progress=lambda _m: self.services.kill.request())
        self.assertTrue(results[0].startswith("✅"))
        self.assertTrue(results[1].startswith("⛔"))
        self.assertEqual(len(results), 2)
        self.assertFalse((self.root / "k2").exists())

    def test_every_executed_action_is_audited_without_content(self):
        target = self.root / "g.txt"
        self.run_plan(action("write_text_file", path=str(target), content="SAKLI-İÇERİK", overwrite=False))
        raw = self.services.audit.path.read_text(encoding="utf-8")
        self.assertNotIn("SAKLI-İÇERİK", raw)
        entry = self.services.audit.tail(1)[0]
        self.assertEqual((entry["tool"], entry["risk"]), ("write_text_file", int(Risk.CREATE)))
        self.assertTrue(entry["result"].startswith("✅"))

    def test_plan_stops_after_first_failure(self):
        results = self.run_plan(
            action("delete_path", path=str(self.root / "yok.txt")),
            action("create_folder", path=str(self.root / "hic-olmamali")),
        )
        self.assertEqual(len(results), 1)
        self.assertFalse((self.root / "hic-olmamali").exists())


class UndoCommandPlannerTests(unittest.TestCase):
    def test_geri_al_variants(self):
        planner = ActionPlanner()
        for text in ("geri al", "Geri al!", "son işlemi geri al", "son değişikliği geri al", "yaptığını geri al"):
            with self.subTest(text=text):
                plan = planner.plan(text)
                self.assertIsNotNone(plan)
                self.assertEqual(plan.actions[0].action_type, "undo_last")

    def test_does_not_hijack_other_sentences(self):
        plan = ActionPlanner().plan("kitabı geri almak istiyorum")
        self.assertTrue(plan is None or plan.actions[0].action_type != "undo_last")


# ------------------------------------------------------------------------- ayarlar
class SettingsAccessTests(TempCase):
    def settings(self):
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.tmp / "appdata")}):
            return Settings()

    def test_defaults_to_approval_and_strict(self):
        s = self.settings()
        self.assertEqual((s.access_mode, s.strict_after_web), ("approval", True))

    def test_set_access_persists_and_rejects_unknown_modes(self):
        s = self.settings()
        s.set_access("full", False)
        again = self.settings()
        self.assertEqual((again.access_mode, again.strict_after_web), ("full", False))
        again.set_access("herşeyeizinver")
        self.assertEqual(self.settings().access_mode, "approval")

    def test_ollama_provider_settings_persist(self):
        s = self.settings()
        s.save("", "gemini-3.8-flash", "ollama", "http://localhost:11434/", "qwen2.5-coder:1.5b")
        again = self.settings()
        self.assertEqual(again.provider, "ollama")
        self.assertEqual(again.ollama_url, "http://localhost:11434")
        self.assertEqual(again.ollama_model, "qwen2.5-coder:1.5b")

    def test_tokenharbor_provider_settings_persist(self):
        s = self.settings()
        s.save(
            "",
            "gemini-3.8-flash",
            "tokenharbor",
            tokenharbor_api_key=" th-key ",
            tokenharbor_model="vendor/model:free",
        )
        again = self.settings()
        self.assertEqual(again.provider, "tokenharbor")
        self.assertEqual(again.provider_label, "Token Harbor")
        self.assertEqual(again.tokenharbor_api_key, "th-key")
        self.assertEqual(again.tokenharbor_model, "vendor/model:free")
        self.assertEqual(again.active_model, "vendor/model:free")
        self.assertEqual(again.tokenharbor_url, "https://tokenharbor.ai/v1")

    def test_voice_replies_default_on_and_persist(self):
        s = self.settings()
        self.assertTrue(s.voice_replies)
        s.set_voice_replies(False)
        self.assertFalse(self.settings().voice_replies)
        self.settings().set_voice_replies(True)
        self.assertTrue(self.settings().voice_replies)

    def test_extra_roots_add_remove(self):
        s = self.settings()
        s.add_root(str(self.outside))
        s.add_root(str(self.outside))
        self.assertEqual(self.settings().extra_roots, [str(self.outside)])
        s.remove_root(str(self.outside))
        self.assertEqual(self.settings().extra_roots, [])

    def test_build_services_wires_live_mode_and_protects_jarvis_data(self):
        s = self.settings()
        services = build_services(s)
        self.assertEqual(services.gate.decide(Risk.MODIFY).action, "ask")
        s.set_access("full")
        self.assertEqual(services.gate.decide(Risk.MODIFY).action, "allow")
        zones = [zone for zone, _ in services.policy.forbidden]
        self.assertIn(real(s.data_dir), zones)
        self.assertIn(real(Path(__file__).resolve().parents[1]), zones)
        with self.assertRaises(PathNotAllowed):
            services.policy.resolve(s.data_dir / "settings.json")

    def test_extra_root_becomes_usable(self):
        s = self.settings()
        s.add_root(str(self.outside))
        with patch("app.core.safety.Path.home", return_value=self.tmp / "home"):
            services = build_services(s)
        self.assertEqual(services.policy.resolve(self.outside / "ok.txt"), self.outside / "ok.txt")


if __name__ == "__main__":
    unittest.main()
