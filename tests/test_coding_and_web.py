import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image, ImageDraw

from app.core.ai import AI
from app.core.desktop import CapabilityTools, DesktopTools
from app.core.project_tools import WORKSPACE_MARKER, ProjectTools
from app.core.safety import ActionForbidden, AuditLog, KillSwitch, PathPolicy, PermissionGate, SafetyError, SafetyServices, UndoStore
from app.core.web_tools import BrowserReader, DownloadTools, WebReader


class FakeWebResponse:
    def __init__(self, body=b"", status=200, headers=None, url="https://example.com"):
        self.status_code = status
        self.ok = status < 400
        self.headers = headers or {"Content-Type": "text/html; charset=utf-8"}
        self.encoding = "utf-8"
        self.content = body
        self.url = url
        self.is_redirect = "Location" in self.headers
        self.closed = False

    def iter_content(self, size):
        for index in range(0, len(self.content), size):
            yield self.content[index:index + size]

    def close(self):
        self.closed = True


def make_services(root: Path, data: Path):
    return SafetyServices(
        PathPolicy([root], base=root), PermissionGate(),
        AuditLog(data / "audit.jsonl"), UndoStore(data / "undo"), KillSwitch(),
    )


class ProjectToolsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(os.path.realpath(self._tmp.name))
        self.root = self.base / "Documents"
        self.root.mkdir()
        self.services = make_services(self.root, self.base / "data")
        self.tools = ProjectTools(self.services.policy, self.services.undo, MagicMock())

    def test_lists_reads_searches_and_exact_replaces_with_undo(self):
        source = self.root / "proje"
        source.mkdir()
        target = source / "main.py"
        target.write_text("def eski():\n    return 1\n", encoding="utf-8")
        (source / "notes.txt").write_text("aranacak metin", encoding="utf-8")

        listed = self.tools.list_files({"path": str(source)})
        self.assertIn("main.py", listed)
        read = self.tools.read_file({"path": str(target), "start_line": 1, "end_line": 1})
        self.assertIn("1: def eski", read)
        found = self.tools.search({"path": str(source), "query": "aranacak"})
        self.assertIn("notes.txt", found)
        preview = self.tools.preview_replace({
            "path": str(target), "old_text": "def eski():", "new_text": "def yeni():",
        })
        self.assertIn("ÖNİZLEME", preview)
        self.assertIn("eski", target.read_text(encoding="utf-8"))

        result = self.tools.replace({
            "path": str(target), "old_text": "def eski():", "new_text": "def yeni():",
        })
        self.assertTrue(result.startswith("✅"), result)
        self.assertIn("def yeni", target.read_text(encoding="utf-8"))
        self.services.undo.undo_last()
        self.assertIn("def eski", target.read_text(encoding="utf-8"))

    def test_ambiguous_replace_is_refused(self):
        target = self.root / "a.txt"
        target.write_text("aynı aynı", encoding="utf-8")
        with self.assertRaises(ActionForbidden):
            self.tools.replace({"path": str(target), "old_text": "aynı", "new_text": "farklı"})

    def test_create_project_and_zip(self):
        with patch("app.core.project_tools.subprocess.Popen") as opened:
            result = self.tools.create_project({"name": "Deneme", "template": "python"})
        self.assertTrue(result.startswith("✅"), result)
        project = self.root / "Jarvis_Projeler" / "Deneme"
        self.assertTrue((project / "main.py").is_file())
        self.assertTrue((self.root / "Jarvis_Projeler" / "Deneme.zip").is_file())
        opened.assert_called_once()

    def test_self_change_without_test_is_denied(self):
        workspace = self.root / "Jarvis_Projeler" / "Jarvis_Gelistirme_test"
        workspace.mkdir(parents=True)
        (workspace / WORKSPACE_MARKER).write_text(
            '{"source": "' + str(Path(__file__).resolve().parents[1]).replace("\\", "\\\\") + '"}', encoding="utf-8"
        )
        (workspace / "app.py").write_text("print('değişti')", encoding="utf-8")
        with self.assertRaisesRegex(ActionForbidden, "run_tests"):
            self.tools.apply_self_changes({"path": str(workspace)})


class WebReaderTests(unittest.TestCase):
    def setUp(self):
        self.reader = WebReader()

    def test_rejects_local_private_and_non_http_urls(self):
        for url in ("file:///C:/Windows/win.ini", "http://localhost/x", "http://127.0.0.1/x", "http://169.254.169.254/meta"):
            with self.subTest(url=url), self.assertRaises(ActionForbidden):
                self.reader._validate_url(url)

    @patch("app.core.web_tools.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))])
    def test_reads_html_as_plain_source_text(self, _dns):
        response = FakeWebResponse("<html><title>Başlık</title><script>kötü()</script><p>Yararlı metin</p></html>".encode())
        with patch("app.core.web_tools.requests.get", return_value=response) as get:
            result = self.reader.read_page({"url": "https://example.com/haber"})
        self.assertIn("GÜVENİLMEZ", result)
        self.assertIn("Yararlı metin", result)
        self.assertIn("Başlık", result)
        self.assertNotIn("kötü()", result)
        get.assert_called_once()

    @patch("app.core.web_tools.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))])
    def test_redirect_to_local_address_is_ssrf_blocked(self, _dns):
        redirect = FakeWebResponse(headers={"Location": "http://127.0.0.1/admin"})
        with patch("app.core.web_tools.requests.get", return_value=redirect) as get:
            with self.assertRaises(ActionForbidden):
                self.reader.read_page({"url": "https://example.com"})
        self.assertEqual(get.call_count, 1)

    def test_browser_reader_blocks_private_addresses_before_engine(self):
        with self.assertRaises(ActionForbidden):
            BrowserReader().read({"url": "http://192.168.1.10/router"})

    @patch("app.core.web_tools.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))])
    def test_rss_items_are_parsed(self, _dns):
        body = b"""<rss><channel><item><title>Haber</title><link>https://example.com/1</link>
        <pubDate>Thu, 23 Oct 2026 10:00:00 GMT</pubDate><description>Ozet</description></item></channel></rss>"""
        response = FakeWebResponse(body, headers={"Content-Type": "application/rss+xml"})
        with patch("app.core.web_tools.requests.get", return_value=response):
            result = self.reader.read_rss({"feed_url": "https://example.com/rss", "limit": 5})
        self.assertIn("Haber", result)
        self.assertIn("https://example.com/1", result)

    @patch("app.core.web_tools.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))])
    def test_page_and_rss_reads_are_cached(self, _dns):
        page = FakeWebResponse("<html><title>Başlık</title><p>Metin</p></html>".encode())
        feed = FakeWebResponse(
            b"<rss><channel><item><title>Haber</title></item></channel></rss>",
            headers={"Content-Type": "application/rss+xml"},
        )
        with patch("app.core.web_tools.requests.get", side_effect=[page, feed]) as get:
            first = self.reader.read_page({"url": "https://example.com/haber"})
            second = self.reader.read_page({"url": "https://example.com/haber"})
            first_feed = self.reader.read_rss({"feed_url": "https://example.com/rss", "limit": 5})
            second_feed = self.reader.read_rss({"feed_url": "https://example.com/rss", "limit": 5})
        self.assertEqual(get.call_count, 2)
        self.assertNotIn("ÖNBELLEK", first)
        self.assertIn("ÖNBELLEK", second)
        self.assertIn("Metin", second)
        self.assertNotIn("ÖNBELLEK", first_feed)
        self.assertIn("ÖNBELLEK", second_feed)
        self.assertIn("Haber", second_feed)

    @patch("app.core.web_tools.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))])
    def test_expired_page_cache_refetches(self, _dns):
        first = FakeWebResponse("<html><title>Eski</title><p>Eski metin</p></html>".encode())
        second = FakeWebResponse("<html><title>Yeni</title><p>Yeni metin</p></html>".encode())
        with patch("app.core.web_tools.requests.get", side_effect=[first, second]) as get:
            self.reader.read_page({"url": "https://example.com/haber"})
            self.reader._cache["page:https://example.com/haber"] = (0.0, "eski")
            result = self.reader.read_page({"url": "https://example.com/haber"})
        self.assertEqual(get.call_count, 2)
        self.assertIn("Yeni metin", result)
        self.assertNotIn("ÖNBELLEK", result)

    @patch("app.core.web_tools.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))])
    def test_pdf_page_is_read_as_text(self, _dns):
        class Page:
            def extract_text(self):
                return "Yararlı PDF metni"

        class Metadata:
            title = "Rapor"

        class Reader:
            is_encrypted = False
            metadata = Metadata()
            pages = [Page(), Page(), Page()]

        response = FakeWebResponse(b"%PDF-1.7 test", headers={"Content-Type": "application/pdf"})
        with patch("app.core.web_tools.requests.get", return_value=response):
            with patch("pypdf.PdfReader", return_value=Reader()):
                result = self.reader.read_page({"url": "https://example.com/rapor.pdf"})
        self.assertIn("GÜVENİLMEZ", result)
        self.assertIn("Tür: PDF · 3 sayfa", result)
        self.assertIn("Rapor", result)
        self.assertIn("Yararlı PDF metni", result)

    @patch("app.core.web_tools.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))])
    def test_scanned_pdf_without_text_is_rejected(self, _dns):
        class Page:
            def extract_text(self):
                return ""

        class Reader:
            is_encrypted = False
            metadata = None
            pages = [Page()]

        response = FakeWebResponse(b"%PDF-1.7 test", headers={"Content-Type": "application/pdf"})
        with patch("app.core.web_tools.requests.get", return_value=response):
            with patch("pypdf.PdfReader", return_value=Reader()):
                with self.assertRaisesRegex(ActionForbidden, "metin yok"):
                    self.reader.read_page({"url": "https://example.com/tarama.pdf"})


class DownloadToolsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(os.path.realpath(self._tmp.name))
        self.root = self.base / "Desktop"
        self.root.mkdir()
        self.services = make_services(self.root, self.base / "data")
        self.tools = DownloadTools(self.services.policy, self.services.undo, audit=self.services.audit)

    @patch("app.core.web_tools.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))])
    def test_download_writes_file_and_undo_removes_it(self, _dns):
        body = b"merhaba dunya"
        response = FakeWebResponse(body, headers={
            "Content-Type": "application/octet-stream", "Content-Length": str(len(body)),
        })
        target = self.root / "indirilen.bin"
        with patch("app.core.web_tools.requests.get", return_value=response):
            result = self.tools.download({"url": "https://example.com/dosya.bin", "path": str(target)})
        self.assertTrue(result.startswith("✅"), result)
        self.assertEqual(target.read_bytes(), body)
        self.assertIn("13 bayt", result)
        self.services.undo.undo_last()
        self.assertFalse(target.exists())

    @patch("app.core.web_tools.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))])
    def test_existing_file_needs_overwrite_and_undo_restores_it(self, _dns):
        target = self.root / "eski.txt"
        target.write_text("eski içerik", encoding="utf-8")
        with patch("app.core.web_tools.requests.get") as get:
            refused = self.tools.download({"url": "https://example.com/yeni.txt", "path": str(target)})
        self.assertIn("zaten var", refused)
        get.assert_not_called()
        self.assertEqual(target.read_text(encoding="utf-8"), "eski içerik")

        response = FakeWebResponse(b"yeni", headers={"Content-Type": "text/plain"})
        with patch("app.core.web_tools.requests.get", return_value=response):
            result = self.tools.download({
                "url": "https://example.com/yeni.txt", "path": str(target), "overwrite": True,
            })
        self.assertTrue(result.startswith("✅"), result)
        self.assertEqual(target.read_text(encoding="utf-8"), "yeni")
        self.services.undo.undo_last()
        self.assertEqual(target.read_text(encoding="utf-8"), "eski içerik")

    @patch("app.core.web_tools.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))])
    def test_download_rejects_forbidden_path_and_private_redirect(self, _dns):
        with self.assertRaises(SafetyError):
            self.tools.download({"url": "https://example.com/x", "path": "C:/Windows/evil.bin"})
        redirect = FakeWebResponse(headers={"Location": "http://127.0.0.1/gizli"})
        with patch("app.core.web_tools.requests.get", return_value=redirect) as get:
            with self.assertRaises(ActionForbidden):
                self.tools.download({"url": "https://example.com/x", "path": str(self.root / "x.bin")})
        self.assertEqual(get.call_count, 1)
        self.assertFalse((self.root / "x.bin").exists())

    @patch("app.core.web_tools.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))])
    def test_downloads_up_to_limit_skip_approval(self, _dns):
        self.tools.approval = MagicMock(return_value=False)
        self.tools.AUTO_APPROVE_BYTES = 13
        body = b"merhaba dunya"
        response = FakeWebResponse(body, headers={"Content-Type": "application/octet-stream"})
        target = self.root / "sinirda.bin"
        with patch("app.core.web_tools.requests.get", return_value=response):
            result = self.tools.download({"url": "https://example.com/sinirda.bin", "path": str(target)})
        self.assertTrue(result.startswith("✅"), result)
        self.tools.approval.assert_not_called()
        self.assertEqual(target.read_bytes(), body)

    @patch("app.core.web_tools.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))])
    def test_declared_size_over_limit_requests_approval(self, _dns):
        over = DownloadTools.AUTO_APPROVE_BYTES + 1
        response = FakeWebResponse(b"x", headers={"Content-Length": str(over)})
        target = self.root / "buyuk.bin"
        with patch("app.core.web_tools.requests.get", return_value=response):
            with self.assertRaises(ActionForbidden):
                self.tools.download({"url": "https://example.com/buyuk.bin", "path": str(target)})
        self.assertFalse(target.exists())

        self.tools.approval = MagicMock(return_value=True)
        approved_response = FakeWebResponse(b"x", headers={"Content-Length": str(over)})
        with patch("app.core.web_tools.requests.get", return_value=approved_response):
            result = self.tools.download({"url": "https://example.com/buyuk.bin", "path": str(target)})
        self.assertTrue(result.startswith("✅"), result)
        self.assertIn("2 GB üstü indirme onaylandı", result)
        self.assertEqual(target.read_bytes(), b"x")
        self.assertEqual(self.tools.approval.call_count, 1)
        self.assertIn("2 GB sınırını aşıyor", self.tools.approval.call_args.args[0])
        entries = self.services.audit.tail(10)
        self.assertTrue(
            any(entry.get("reason") == "2 GB üstü indirme" and entry.get("approved") is True for entry in entries)
        )

    @patch("app.core.web_tools.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))])
    def test_unknown_size_crossing_limit_requests_approval_once(self, _dns):
        self.tools.AUTO_APPROVE_BYTES = 4
        self.tools.approval = MagicMock(return_value=True)
        body = b"0123456789"
        response = FakeWebResponse(body, headers={"Content-Type": "application/octet-stream"})
        target = self.root / "bilinmeyen.bin"
        with patch("app.core.web_tools.requests.get", return_value=response):
            result = self.tools.download({"url": "https://example.com/bilinmeyen.bin", "path": str(target)})
        self.assertTrue(result.startswith("✅"), result)
        self.assertEqual(target.read_bytes(), body)
        self.assertEqual(self.tools.approval.call_count, 1)

    @patch("app.core.web_tools.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))])
    def test_unknown_size_over_limit_is_discarded_when_denied(self, _dns):
        self.tools.AUTO_APPROVE_BYTES = 4
        self.tools.approval = MagicMock(return_value=False)
        response = FakeWebResponse(b"0123456789", headers={"Content-Type": "application/octet-stream"})
        target = self.root / "reddedilen.bin"
        with patch("app.core.web_tools.requests.get", return_value=response):
            with self.assertRaises(ActionForbidden):
                self.tools.download({"url": "https://example.com/reddedilen.bin", "path": str(target)})
        self.assertFalse(target.exists())
        self.assertEqual(list(self.root.glob(".jarvis-*.part")), [])


class DesktopAndCapabilityTests(unittest.TestCase):
    def test_inspect_image_detects_blank_and_drawn_results(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = DesktopTools(Path(directory))
            blank = Path(directory) / "blank.png"
            Image.new("RGB", (120, 80), "white").save(blank)
            blank_data = json.loads(tools.inspect_image(blank))
            self.assertTrue(blank_data["blank"])
            self.assertEqual(blank_data["non_white_ratio"], 0.0)
            self.assertIsNone(blank_data["content_bbox"])
            drawn = Path(directory) / "drawn.png"
            image = Image.new("RGB", (200, 200), "white")
            ImageDraw.Draw(image).rectangle((20, 50, 100, 150), fill=(120, 70, 20))
            image.save(drawn)
            drawn_data = json.loads(tools.inspect_image(drawn))
            self.assertFalse(drawn_data["blank"])
            self.assertGreater(drawn_data["non_white_ratio"], 0.1)
            self.assertEqual(drawn_data["content_bbox"], [20, 50, 101, 151])
            self.assertGreater(drawn_data["grid"][1][1], 0)
            self.assertTrue(Path(drawn_data["preview"]).exists())

    def test_mouse_coordinates_are_validated_and_click_dispatched(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = DesktopTools(Path(directory))
            with patch("pywinauto.mouse.click") as click:
                result = tools.mouse({"action": "click", "x": 100, "y": 200})
            click.assert_called_once_with(button="left", coords=(100, 200))
            self.assertTrue(result.startswith("✅"))
            with self.assertRaises(ActionForbidden):
                tools.mouse({"action": "click", "x": "saçma", "y": 2})

    def test_install_rejects_urls_and_options(self):
        tools = CapabilityTools()
        for package in ("https://evil.example/x", "--index-url", "paket;calc"):
            with self.subTest(package=package), self.assertRaises(ActionForbidden):
                tools.install({"kind": "python", "package": package})

    def test_python_dependency_install_is_verified(self):
        tools = CapabilityTools()
        completed = MagicMock(returncode=0, stdout="ok", stderr="")
        with patch("app.core.desktop.subprocess.run", return_value=completed) as run, \
             patch("app.core.desktop.importlib.util.find_spec", return_value=object()):
            result = tools.install({"kind": "python", "package": "requests"})
        self.assertTrue(result.startswith("✅"))
        self.assertEqual(run.call_args.args[0][-1], "requests")


class ScreenshotContextTests(unittest.TestCase):
    def test_screenshots_are_base64_loaded_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "screen.jpg"
            path.write_bytes(b"sahte-jpeg")
            ai = AI.__new__(AI)
            ai._screenshots = []
            ai.add_screenshot(str(path))
            encoded = ai._take_screenshots()
            self.assertEqual(len(encoded), 1)
            self.assertEqual(ai._take_screenshots(), [])


if __name__ == "__main__":
    unittest.main()
