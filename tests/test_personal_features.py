import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from app.core.agent import ActionExecutor, PlannedAction
from app.core.ai import TOOL_DECLARATIONS
from app.core.history import ChatMessage
from app.core.hud_data import HudStore, WeatherService, local_moment
from app.core.personal_tools import (
    ClipboardHistory,
    PersonalTools,
    SystemControls,
    build_briefing_text,
    matches_query,
    messages_to_markdown,
    turkish_fold,
)
from app.core.safety import (
    AccessMode,
    ActionForbidden,
    AuditLog,
    KillSwitch,
    PathPolicy,
    PermissionGate,
    Risk,
    SafetyServices,
    UndoStore,
    classify_action,
)
from app.core.storage import Storage


class TurkishSearchTests(unittest.TestCase):
    def test_fold_handles_turkish_characters(self):
        self.assertEqual(turkish_fold("ŞİŞLİ ĞÜÇÖ"), "sisli guco")

    def test_query_tokens_must_all_match(self):
        self.assertTrue(matches_query("hatırlat çay", "Çay hatırlatıcısı", "18:30"))
        self.assertTrue(matches_query("HATIRLAT", "Çay hatırlatıcısı"))
        self.assertFalse(matches_query("kahve", "Çay hatırlatıcısı"))
        self.assertTrue(matches_query("", "herhangi"))


class ClipboardHistoryTests(unittest.TestCase):
    def test_deduplicates_and_limits(self):
        history = ClipboardHistory(limit=3)
        for text in ("bir", "iki", "üç", "bir"):
            history.add(text)
        self.assertEqual(history.items(), ["bir", "üç", "iki"])
        self.assertEqual(len(history), 3)
        self.assertFalse(history.add("   "))
        history.remove("üç")
        history.clear()
        self.assertEqual(history.items(), [])

    def test_ignores_oversized_entries(self):
        history = ClipboardHistory(limit=5, max_chars=4)
        self.assertFalse(history.add("12345"))
        self.assertTrue(history.add("1234"))


class HudStoreReminderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "hud.json"
        self.store = HudStore(self.path)

    def test_add_due_and_complete_reminder(self):
        past = (datetime.now() - timedelta(minutes=2)).isoformat(timespec="minutes")
        item = self.store.add_reminder("Suyu kapat", past)
        self.assertFalse(item["notified"])
        self.assertEqual(len(self.store.due_reminders()), 1)
        self.assertTrue(self.store.mark_reminder_notified(item["id"]))
        self.assertEqual(self.store.due_reminders(), [])
        self.assertTrue(self.store.complete_reminder(item["id"]))
        self.assertEqual(self.store.pending_reminders(), [])
        self.assertEqual(len(self.store.reminders()), 1)

    def test_reminder_survives_reload(self):
        future = (datetime.now() + timedelta(hours=2)).isoformat(timespec="minutes")
        self.store.add_reminder("Toplantı", future)
        reloaded = HudStore(self.path)
        self.assertEqual(len(reloaded.pending_reminders()), 1)
        self.assertEqual(reloaded.pending_reminders()[0]["text"], "Toplantı")

    def test_clear_completed_reminders(self):
        future = (datetime.now() + timedelta(hours=1)).isoformat(timespec="minutes")
        item = self.store.add_reminder("Tamamlanacak", future)
        self.store.complete_reminder(item["id"])
        self.assertEqual(self.store.clear_completed_reminders(), 1)
        self.assertEqual(self.store.reminders(), [])

    def test_invalid_reminder_rejected(self):
        with self.assertRaises(ValueError):
            self.store.add_reminder("", "2026-10-25T18:30")
        with self.assertRaises(ValueError):
            self.store.add_reminder("Geçersiz", "yarın akşam")

    def test_local_moment_normalizes_utc(self):
        moment = local_moment("2026-10-25T12:00:00Z")
        self.assertIsNotNone(moment)
        self.assertIsNone(moment.tzinfo)


class SystemControlTests(unittest.TestCase):
    def test_media_keys_use_expected_virtual_codes(self):
        sent = []
        controls = SystemControls(key_sender=sent.append)
        controls.action("volume_up")
        controls.action("mute")
        controls.action("media_next")
        self.assertEqual(sent, [0xAF, 0xAD, 0xB0])

    def test_shutdown_is_clamped_and_cancellable(self):
        commands = []
        controls = SystemControls(command_runner=lambda args: (commands.append(args) or (0, "")))
        controls.action("shutdown", seconds=5, reason="ders bitti")
        self.assertEqual(commands[-1], ["shutdown", "/s", "/t", "15", "/c", "ders bitti"])
        controls.action("cancel_shutdown")
        self.assertEqual(commands[-1], ["shutdown", "/a"])

    def test_cancel_without_pending_shutdown(self):
        controls = SystemControls(command_runner=lambda args: (1116, "zamanlanmış kapatma yok"))
        self.assertIn("İptal edilecek", controls.action("cancel_shutdown"))

    def test_lock_uses_locker(self):
        locked = []
        controls = SystemControls(locker=lambda: locked.append(True))
        controls.action("lock")
        self.assertEqual(locked, [True])

    def test_unknown_action_raises(self):
        with self.assertRaises(ValueError):
            SystemControls(key_sender=lambda _: None).action("format_disk")


class BriefingTests(unittest.TestCase):
    def test_briefing_contains_real_sections_only(self):
        now = datetime(2026, 10, 25, 9, 30)
        weather = {
            "location": "Derince · Turkey",
            "current": {"temp": 18, "text": "Parçalı bulutlu", "wind": 12, "humidity": 54},
            "daily": [{"min": 11, "max": 21}],
        }
        events = [{"title": "Diş randevusu", "when": "2026-10-25T10:00"}]
        reminders = [{"text": "İlaç", "when": "2026-10-25T10:15", "done": False}]
        notes = [{"text": "Süt al"}]
        snapshot = {"available": True, "cpu": 12, "memory_percent": 48, "disk_percent": 61}
        text = build_briefing_text(now, weather, events, reminders, notes, snapshot)
        self.assertIn("HAVA · Derince · 18°", text)
        self.assertIn("TAKVİM · Bugün 10:00 · Diş randevusu", text)
        self.assertIn("HATIRLATICI · 1 bekleyen", text)
        self.assertIn("NOT · 1 kayıtlı · Son: Süt al", text)
        self.assertIn("SİSTEM · CPU %12", text)

    def test_briefing_without_data_does_not_invent_values(self):
        text = build_briefing_text(datetime(2026, 10, 25, 9, 30))
        self.assertIn("Veri henüz hazır değil", text)
        self.assertIn("etkinlik yok", text)
        self.assertIn("Kayıtlı not yok", text)
        self.assertNotIn("°C", text)


class MarkdownExportTests(unittest.TestCase):
    def test_chat_message_default_timestamp_is_fresh(self):
        message = ChatMessage(role="user", content="Merhaba")
        self.assertIsNotNone(message.created_at.tzinfo)
        self.assertLess(abs((datetime.now(message.created_at.tzinfo) - message.created_at).total_seconds()), 5)

    def test_chat_messages_and_dicts_export(self):
        created = datetime(2026, 10, 25, 9, 30)
        messages = [
            ChatMessage(role="user", content="Merhaba", created_at=created),
            {"role": "assistant", "content": "Selam", "created_at": "2026-10-25T09:31:00"},
        ]
        text = messages_to_markdown(messages)
        self.assertIn("# Jarvis Sohbet Geçmişi", text)
        self.assertIn("### Sen · 25.10.2026 09:30", text)
        self.assertIn("### Jarvis · 25.10.2026 09:31", text)
        self.assertIn("Merhaba", text)


class PersonalToolsAgentTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = HudStore(Path(self._tmp.name) / "hud.json")
        self.tools = PersonalTools(self.store)

    def test_reminder_lifecycle_through_agent_tool(self):
        output = self.tools.run(
            {"resource": "reminder", "action": "add", "text": "Çayı kapat", "in_minutes": 5}
        )
        self.assertIn("Hatırlatıcı kuruldu", output)
        pending = self.store.pending_reminders()
        self.assertEqual(len(pending), 1)
        listed = self.tools.run({"resource": "reminder", "action": "list"})
        self.assertIn("Çayı kapat", listed)
        done = self.tools.run({"resource": "reminder", "action": "complete", "id": pending[0]["id"]})
        self.assertIn("tamamlandı", done)
        self.assertEqual(self.store.pending_reminders(), [])

    def test_note_and_event_actions(self):
        self.assertIn("Not eklendi", self.tools.run({"resource": "note", "action": "add", "text": "Süt"}))
        notes = self.store.notes()
        self.assertEqual(notes[0]["text"], "Süt")
        self.assertIn("Not silindi", self.tools.run({"resource": "note", "action": "delete", "id": notes[0]["id"]}))
        added = self.tools.run(
            {
                "resource": "event",
                "action": "add",
                "text": "Toplantı",
                "when": "2026-10-25T18:30",
            }
        )
        self.assertIn("Etkinlik eklendi", added)
        self.assertEqual(self.store.events()[0]["title"], "Toplantı")

    def test_invalid_arguments_raise_value_error(self):
        with self.assertRaises(ValueError):
            self.tools.run({"resource": "reminder", "action": "add", "text": "Zaman yok"})
        with self.assertRaises(ValueError):
            self.tools.run({"resource": "bilinmeyen", "action": "list"})


class SafetyClassificationTests(unittest.TestCase):
    def risk(self, action_type, **arguments):
        return classify_action(action_type, arguments, None)

    def test_personal_rules(self):
        self.assertEqual(self.risk("personal", resource="note", action="list"), Risk.READ)
        self.assertEqual(self.risk("personal", resource="reminder", action="add"), Risk.CREATE)
        self.assertEqual(self.risk("personal", resource="event", action="delete"), Risk.MODIFY)
        with self.assertRaises(ActionForbidden):
            self.risk("personal", resource="note", action="complete")
        with self.assertRaises(ActionForbidden):
            self.risk("personal", resource="dosya", action="list")

    def test_system_control_rules(self):
        self.assertEqual(self.risk("system_control", action="volume_up"), Risk.CREATE)
        self.assertEqual(self.risk("system_control", action="lock"), Risk.MODIFY)
        self.assertEqual(
            self.risk("system_control", action="shutdown", confirm=True), Risk.DANGEROUS
        )
        with self.assertRaises(ActionForbidden):
            self.risk("system_control", action="shutdown")
        with self.assertRaises(ActionForbidden):
            self.risk("system_control", action="rm_rf")

    def test_clipboard_rules(self):
        self.assertEqual(self.risk("clipboard", action="read"), Risk.READ)
        self.assertEqual(self.risk("clipboard", action="write"), Risk.CREATE)
        with self.assertRaises(ActionForbidden):
            self.risk("clipboard", action="delete")


class StorageSearchTests(unittest.TestCase):
    def test_search_matches_content_and_escapes_wildcards(self):
        with tempfile.TemporaryDirectory() as directory:
            with Storage(str(Path(directory) / "jarvis.db")) as storage:
                storage.add_message("user", "Merhaba Jarvis")
                storage.add_message("assistant", "Selam Cem")
                storage.add_message("user", "İndirim %50 tamam")
                found = storage.search_messages("selam")
                self.assertEqual(len(found), 1)
                self.assertEqual(found[0].content, "Selam Cem")
                percent = storage.search_messages("%")
                self.assertEqual(len(percent), 1)
                self.assertEqual(percent[0].content, "İndirim %50 tamam")
                by_role = storage.search_messages("user")
                self.assertEqual(len(by_role), 2)


class WeatherLocationTests(unittest.TestCase):
    class Settings:
        home_location = {"name": "İzmit", "region": "Kocaeli", "lat": 40.76, "lon": 29.94}

    def test_parse_uses_home_location(self):
        service = WeatherService(self.Settings())
        location = service._location()
        self.assertEqual(location["name"], "İzmit")
        parsed = WeatherService._parse(
            {
                "current": {"temperature_2m": 17.4, "weather_code": 2},
                "daily": {"time": ["2026-10-25"], "temperature_2m_max": [21], "temperature_2m_min": [11]},
            },
            location,
        )
        self.assertEqual(parsed["location"], "İzmit · Kocaeli")
        self.assertAlmostEqual(parsed["lat"], 40.76)

    def test_cache_from_another_location_is_not_marked_valid(self):
        service = WeatherService(self.Settings())
        service.cached = {"lat": 41.0, "lon": 29.0}
        self.assertFalse(service._cache_matches_location(service._location()))
        service.cached = {"lat": 40.76, "lon": 29.94}
        self.assertTrue(service._cache_matches_location(service._location()))

    def test_city_geocoding_resolves_location_and_reuses_cache(self):
        class Response:
            def __init__(self, payload):
                self.payload = payload

            def raise_for_status(self):
                pass

            def json(self):
                return self.payload

        geocode = Response({"results": [{
            "name": "İstanbul", "admin1": "İstanbul", "country": "Türkiye",
            "latitude": 41.01, "longitude": 28.97,
        }]})
        weather = Response({
            "current": {"temperature_2m": 19, "weather_code": 1},
            "daily": {"time": ["2026-10-26"], "temperature_2m_max": [22], "temperature_2m_min": [14]},
        })
        with tempfile.TemporaryDirectory() as directory:
            with patch("app.core.hud_data.WEATHER_CACHE", Path(directory) / "weather.json"):
                service = WeatherService(self.Settings())
                with patch("requests.get", side_effect=[geocode, weather]) as get:
                    first = service.fetch("İstanbul")
                    second = service.fetch("İstanbul")
                self.assertEqual(get.call_count, 2)
        self.assertEqual(first["location"], "İstanbul · İstanbul")
        self.assertAlmostEqual(first["lat"], 41.01)
        self.assertTrue(second["cached"])
        self.assertEqual(get.call_args.kwargs["params"]["latitude"], 41.01)

    def test_unknown_city_reports_not_found(self):
        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return {"results": []}

        with patch("requests.get", return_value=Response()):
            with self.assertRaisesRegex(ValueError, "konum bulunamadı"):
                WeatherService(self.Settings()).fetch("Olmayan Şehir")


class WeatherCacheTests(unittest.TestCase):
    class Settings:
        home_location = {"name": "İzmit", "region": "Kocaeli", "lat": 40.76, "lon": 29.94}

    class Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "current": {"temperature_2m": 17.4, "weather_code": 2},
                "daily": {
                    "time": ["2026-10-26"],
                    "temperature_2m_max": [21],
                    "temperature_2m_min": [11],
                    "weather_code": [2],
                },
            }

    def _run(self, directory, action):
        cache = Path(directory) / "weather.json"
        with patch("app.core.hud_data.WEATHER_CACHE", cache):
            return action(cache)

    def test_fresh_disk_cache_skips_http(self):
        with tempfile.TemporaryDirectory() as directory:
            def first(cache):
                with patch("requests.get", return_value=self.Response()):
                    data = WeatherService(self.Settings()).fetch()
                self.assertIn("fetched_at", data)
                self.assertFalse(data["stale"])
                return data

            self._run(directory, first)
            def second(cache):
                with patch("requests.get") as get:
                    data = WeatherService(self.Settings()).fetch()
                get.assert_not_called()
                self.assertTrue(data["cached"])
                self.assertFalse(data["stale"])
                return data

            self._run(directory, second)

    def test_expired_cache_refetches(self):
        with tempfile.TemporaryDirectory() as directory:
            def first(cache):
                with patch("requests.get", return_value=self.Response()):
                    return WeatherService(self.Settings()).fetch()

            original = self._run(directory, first)
            cache_file = Path(directory) / "weather.json"
            payload = json.loads(cache_file.read_text(encoding="utf-8"))
            payload["fetched_at"] = time.time() - 301
            cache_file.write_text(json.dumps(payload), encoding="utf-8")

            def second(cache):
                with patch("requests.get", return_value=self.Response()) as get:
                    data = WeatherService(self.Settings()).fetch()
                self.assertEqual(get.call_count, 1)
                self.assertGreater(data["fetched_at"], original["fetched_at"])
                self.assertFalse(data.get("cached"))
                return data

            self._run(directory, second)

    def test_old_cache_without_timestamp_refetches(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_file = Path(directory) / "weather.json"
            cache_file.write_text(
                json.dumps({"lat": 40.76, "lon": 29.94, "location": "İzmit · Kocaeli"}),
                encoding="utf-8",
            )
            with patch("app.core.hud_data.WEATHER_CACHE", cache_file):
                with patch("requests.get", return_value=self.Response()) as get:
                    data = WeatherService(self.Settings()).fetch()
            self.assertEqual(get.call_count, 1)
            self.assertIn("fetched_at", data)

    def test_network_failure_returns_stale_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_file = Path(directory) / "weather.json"
            cache_file.write_text(
                json.dumps({
                    "lat": 40.76,
                    "lon": 29.94,
                    "fetched_at": time.time() - 4000,
                    "location": "İzmit · Kocaeli",
                    "current": {},
                    "daily": [],
                }),
                encoding="utf-8",
            )
            with patch("app.core.hud_data.WEATHER_CACHE", cache_file):
                with patch("requests.get", side_effect=RuntimeError("ağ yok")):
                    data = WeatherService(self.Settings()).fetch()
            self.assertTrue(data["stale"])
            self.assertIn("ağ yok", data["error"])


class AgentIntegrationTests(unittest.TestCase):
    def test_personal_action_executes_through_executor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = HudStore(root / "hud.json")
            services = SafetyServices(
                policy=PathPolicy([root], base=root),
                gate=PermissionGate(lambda: AccessMode.APPROVAL, lambda: True),
                audit=AuditLog(root / "audit.jsonl"),
                undo=UndoStore(root / "undo"),
                kill=KillSwitch(),
            )
            executor = ActionExecutor(services, personal_store=store)
            result = executor.execute(
                PlannedAction("personal", {"resource": "note", "action": "add", "text": "Test notu"}, "not")
            )
            self.assertIn("Not eklendi", result)
            self.assertEqual(store.notes()[0]["text"], "Test notu")

    def test_tool_declarations_include_new_agents(self):
        names = {
            declaration["name"]
            for group in TOOL_DECLARATIONS
            for declaration in group["functionDeclarations"]
        }
        self.assertIn("personal", names)
        self.assertIn("system_control", names)
        self.assertIn("clipboard", names)
        self.assertGreaterEqual(len(names), 40)


if __name__ == "__main__":
    unittest.main()
