import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from app.core.ai import AI, TOOL_DECLARATIONS
from app.core.companion import CompanionPlanner, in_quiet_hours
from app.core.diagnostics import build_report, redact
from app.core.media_tools import YouTubeFeedService, _parse_feed
from app.core.photo_tools import PhotoLookup, _license_ok, _safe_url
from app.core.safety import PathPolicy, Risk, classify_action
from app.core.settings import Settings


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)


class QuietHoursTests(unittest.TestCase):
    def test_overnight_range(self):
        self.assertTrue(in_quiet_hours(datetime(2026, 10, 27, 23, 30), "23:00", "08:00"))
        self.assertTrue(in_quiet_hours(datetime(2026, 10, 27, 3, 0), "23:00", "08:00"))
        self.assertFalse(in_quiet_hours(datetime(2026, 10, 27, 12, 0), "23:00", "08:00"))

    def test_same_bounds_disable_quiet(self):
        self.assertFalse(in_quiet_hours(datetime(2026, 10, 27, 12, 0), "00:00", "00:00"))


class CompanionPlannerTests(TempDirCase):
    def make(self):
        return CompanionPlanner(self.tmp / "companion.json")

    def test_agenda_event_wins_and_is_not_repeated(self):
        planner = self.make()
        now = datetime(2026, 10, 27, 10, 0)
        events = [{"id": "e1", "title": "Toplantı", "when": "2026-10-27T10:30:00"}]
        nudge = planner.next(now=now, events=events)
        self.assertIsNotNone(nudge)
        self.assertEqual(nudge.kind, "event")
        self.assertIn("Toplantı", nudge.text)
        planner.mark(nudge, now=now)
        self.assertIsNone(planner.next(now=now, events=events))
        reloaded = CompanionPlanner(self.tmp / "companion.json")
        self.assertIsNone(reloaded.next(now=now + timedelta(minutes=11), events=events))

    def test_interval_limits_breaks(self):
        planner = self.make()
        now = datetime(2026, 10, 27, 10, 0)
        nudge = planner.next(now=now, interval_minutes=90)
        self.assertEqual(nudge.kind, "break")
        planner.mark(nudge, now=now)
        self.assertIsNone(planner.next(now=now + timedelta(minutes=30), interval_minutes=90))
        self.assertIsNotNone(planner.next(now=now + timedelta(minutes=100), interval_minutes=90))

    def test_hydration_after_two_hours(self):
        planner = self.make()
        now = datetime(2026, 10, 27, 10, 0)
        planner.mark(planner.next(now=now), now=now)
        later = now + timedelta(minutes=125)
        nudge = planner.next(now=later, interval_minutes=90)
        self.assertEqual(nudge.kind, "hydration")
        planner.mark(nudge, now=later)
        self.assertIsNone(planner.next(now=later + timedelta(minutes=1), interval_minutes=15))

    def test_quiet_hours_block_everything(self):
        planner = self.make()
        now = datetime(2026, 10, 27, 23, 30)
        events = [{"id": "e1", "title": "Gece", "when": "2026-10-27T23:50:00"}]
        self.assertIsNone(
            planner.next(now=now, events=events, quiet_start="23:00", quiet_end="08:00")
        )


class DiagnosticsTests(TempDirCase):
    def test_report_redacts_secrets_and_names_privacy(self):
        report = build_report(
            data_dir=self.tmp,
            flags={"screen_context": False, "companion": True},
            dependency_check=lambda name: False,
            now=datetime(2026, 10, 27, 12, 0),
        )
        self.assertIn("JARVIS TANILAMA VE GİZLİLİK RAPORU", report)
        self.assertIn("Eşlikçi: açık", report)
        self.assertIn("[YOK] Mikrofon (speech_recognition)", report)
        self.assertIn("API anahtarı, token, parola ve ortam değişkenleri", report)
        self.assertLessEqual(len(report), 12_001)

    def test_redact_common_tokens(self):
        text = "api_key=abc123 token: xyz AIza" + "a" * 35 + " ghp_" + "b" * 30
        cleaned = redact(text)
        self.assertNotIn("abc123", cleaned)
        self.assertNotIn("AIza" + "a" * 35, cleaned)
        self.assertNotIn("ghp_" + "b" * 30, cleaned)


FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/" xmlns:yt="http://www.youtube.com/xml/schemas/2015">
  <title>Test Kanal - YouTube</title>
  <entry>
    <id>yt:video:abc123</id>
    <yt:videoId>abc123</yt:videoId>
    <title>Uzun video</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=abc123"/>
    <published>2026-10-26T10:00:00+00:00</published>
    <media:group>
      <media:statistics views="5000"/>
      <media:description>Merhaba</media:description>
    </media:group>
  </entry>
  <entry>
    <id>yt:video:def456</id>
    <yt:videoId>def456</yt:videoId>
    <title>Kısa #shorts</title>
    <link rel="alternate" href="https://www.youtube.com/shorts/def456"/>
    <published>2026-10-27T10:00:00+00:00</published>
    <media:group>
      <media:statistics views="9000"/>
    </media:group>
  </entry>
  <entry>
    <id>yt:video:ghi789</id>
    <yt:videoId>ghi789</yt:videoId>
    <title>Daha eski</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=ghi789"/>
    <published>2026-10-20T10:00:00+00:00</published>
    <media:group>
      <media:statistics views="1000"/>
    </media:group>
  </entry>
</feed>
"""

CHANNEL_PAGE = (
    '<html><script>var x = {"externalId":"UCabcdefghijklmnopqrstuv"};</script></html>'
)


class YouTubeFeedTests(TempDirCase):
    def fetcher(self, url):
        if "feeds/videos.xml" in url:
            return 200, FEED.encode("utf-8")
        return 200, CHANNEL_PAGE.encode("utf-8")

    def test_parse_feed_reads_views_and_title(self):
        title, videos = _parse_feed(FEED.encode("utf-8"))
        self.assertEqual(title, "Test Kanal")
        self.assertEqual(len(videos), 3)
        self.assertEqual(videos[0]["video_id"], "def456")
        self.assertEqual(videos[1]["views"], 5000)

    def test_latest_filters_shorts_and_highlights_sort_by_views(self):
        service = YouTubeFeedService(fetcher=self.fetcher, cache_seconds=60)
        latest = service.latest("@test", limit=5)
        self.assertEqual(latest["channel_id"], "UCabcdefghijklmnopqrstuv")
        self.assertEqual([video["video_id"] for video in latest["videos"]], ["abc123", "ghi789"])
        self.assertEqual(latest["filtered_shorts"], 1)

        highlights = YouTubeFeedService(fetcher=self.fetcher, cache_seconds=60).highlights("@test", limit=5)
        self.assertEqual(highlights["videos"][0]["video_id"], "abc123")
        self.assertTrue(highlights["views_available"])

    def test_channel_id_accepts_legacy_channel_id_field(self):
        page = b'<html>{"channelId":"UCabcdefghijklmnopqrstuv"}</html>'
        service = YouTubeFeedService(fetcher=lambda url: (200, page), cache_seconds=60)
        self.assertEqual(service._resolve_channel_id("@test"), "UCabcdefghijklmnopqrstuv")

    def test_get_output_is_readable_and_marks_data_untrusted(self):
        service = YouTubeFeedService(fetcher=self.fetcher, cache_seconds=60)
        text = service.get({"action": "latest", "channel": "@test", "limit": 2})
        self.assertIn("Test Kanal", text)
        self.assertIn("güvenilmez veridir", text)
        self.assertIn("https://www.youtube.com/watch?v=abc123", text)
        self.assertIn("1 Shorts video filtrelendi", text)


def commons_page(title, license_name, host="commons.wikimedia.org"):
    return {
        "title": f"File:{title}",
        "imageinfo": [
            {
                "url": f"https://{host}/images/original.jpg",
                "thumburl": f"https://{host}/images/thumb.jpg",
                "descriptionurl": f"https://{host}/wiki/File:{title}",
                "thumbwidth": 800,
                "thumbheight": 600,
                "extmetadata": {
                    "LicenseShortName": {"value": license_name},
                    "Artist": {"value": "<a href='#'>Fotoğrafçı</a>"},
                    "ImageDescription": {"value": "Açıklama"},
                },
            }
        ],
    }


class PhotoLookupTests(TempDirCase):
    def test_license_filter(self):
        self.assertTrue(_license_ok("CC BY-SA 4.0"))
        self.assertTrue(_license_ok("CC0"))
        self.assertTrue(_license_ok("Public domain"))
        self.assertFalse(_license_ok("All rights reserved"))
        self.assertFalse(_license_ok("Unknown"))
        self.assertFalse(_license_ok(""))

    def test_safe_url_limits_hosts_and_scheme(self):
        self.assertTrue(_safe_url("https://upload.wikimedia.org/a.jpg"))
        self.assertEqual(_safe_url("http://upload.wikimedia.org/a.jpg"), "")
        self.assertEqual(_safe_url("https://example.com/a.jpg"), "")

    def test_search_skips_non_free_results(self):
        payload = {
            "query": {
                "pages": [
                    commons_page("Serbest.jpg", "CC BY-SA 4.0"),
                    commons_page("Kapali.jpg", "All rights reserved"),
                ]
            }
        }
        service = PhotoLookup(fetcher=lambda url, params=None: (200, payload))
        rows = service.search("Ankara Kalesi", limit=5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Serbest.jpg")
        self.assertIn("Fotoğrafçı", rows[0]["artist"])

    def test_get_mentions_license_and_no_download(self):
        payload = {"query": {"pages": [commons_page("Serbest.jpg", "CC BY 4.0")]}}
        service = PhotoLookup(fetcher=lambda url, params=None: (200, payload))
        text = service.get({"query": "Ankara Kalesi"})
        self.assertIn("Lisans: CC BY 4.0", text)
        self.assertIn("görseli indirmez", text)
        self.assertIn("commons.wikimedia.org/wiki/File:Serbest.jpg", text)


class NewToolSafetyTests(TempDirCase):
    def setUp(self):
        super().setUp()
        root = self.tmp / "Desktop"
        root.mkdir()
        self.policy = PathPolicy([root], [], base=root)

    def test_youtube_feed_is_read_only(self):
        risk = classify_action(
            "youtube", {"action": "latest", "channel": "@kanal"}, self.policy
        )
        self.assertEqual(risk, Risk.READ)
        risk = classify_action(
            "youtube", {"action": "highlights", "query": "kanal adı"}, self.policy
        )
        self.assertEqual(risk, Risk.READ)

    def test_youtube_open_remains_create(self):
        risk = classify_action("youtube", {"query": "muzik"}, self.policy)
        self.assertEqual(risk, Risk.CREATE)

    def test_photo_is_read_only(self):
        self.assertEqual(classify_action("photo", {"query": "kahve"}, self.policy), Risk.READ)


class ToolDeclarationTests(unittest.TestCase):
    def test_youtube_feed_and_photo_tools_are_declared(self):
        declarations = {
            item["name"]: item
            for group in TOOL_DECLARATIONS
            for item in group["functionDeclarations"]
        }
        youtube = declarations["youtube"]
        self.assertEqual(
            youtube["parameters"]["properties"]["action"]["enum"],
            ["open", "latest", "highlights"],
        )
        self.assertIn("channel", youtube["parameters"]["properties"])
        photo = declarations["photo"]
        self.assertEqual(photo["parameters"]["required"], ["query"])
        self.assertIn("özgür lisans", photo["description"])

    def test_system_prompt_mentions_new_tools(self):
        prompt = AI.DEFAULT_SYSTEM_PROMPT
        self.assertIn("action=latest", prompt)
        self.assertIn("photo", prompt)
        self.assertIn("atfı", prompt)


class CompanionSettingsTests(TempDirCase):
    def test_companion_settings_persist_and_clamp(self):
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.tmp)}):
            settings = Settings()
            settings.set_companion(
                enabled=False,
                interval=5,
                quiet_start="22:30",
                quiet_end="7:5",
                hydration=False,
                speak=True,
            )
            reloaded = Settings()
        self.assertFalse(reloaded.companion_enabled)
        self.assertEqual(reloaded.companion_interval, 15)
        self.assertEqual(reloaded.companion_quiet_start, "22:30")
        self.assertEqual(reloaded.companion_quiet_end, "07:05")
        self.assertFalse(reloaded.companion_hydration)
        self.assertTrue(reloaded.companion_speak)


if __name__ == "__main__":
    unittest.main()
