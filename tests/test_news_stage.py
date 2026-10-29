import json
import os
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from app.core import news_tools
from app.core.news_tools import (
    NewsService,
    _feed_items,
    _http_url,
    _publish_news_items,
    add_news_listener,
    download_image,
    extract_og_image,
    fetch_article_image,
    remove_news_listener,
)
from app.ui import news_stage as stage_module
from app.ui.news_stage import NewsStage, _relative_time

APP = QApplication.instance() or QApplication([])


class FakeStream:
    def __init__(self, data=b"", content_type="image/jpeg", length=None, url="https://news.test/x"):
        self._data = data
        self.headers = {}
        if content_type:
            self.headers["Content-Type"] = content_type
        if length is not None:
            self.headers["Content-Length"] = str(length)
        self.encoding = "utf-8"
        self.url = url
        self.closed = False
        self.status_code = 200

    def raise_for_status(self):
        if self.status_code >= 400:
            raise news_tools.requests.HTTPError(str(self.status_code))

    def iter_content(self, chunk_size):
        yield self._data

    def close(self):
        self.closed = True


def rss_with_images():
    return """<?xml version="1.0"?>
    <rss xmlns:media="http://search.yahoo.com/mrss/"><channel>
      <item>
        <title>Zirve görüşmesi</title>
        <link>https://example.test/a</link>
        <description>Özet</description>
        <media:content url="https://cdn.test/a.jpg" medium="image"/>
      </item>
      <item>
        <title>Ekonomi paketi</title>
        <link>https://example.test/b</link>
        <enclosure url="https://cdn.test/b.jpeg" type="image/jpeg"/>
      </item>
      <item>
        <title>Kültür haberi</title>
        <link>https://example.test/news/3</link>
        <description>&lt;img src="/rel.png"/&gt; metin</description>
      </item>
      <item>
        <title>Görselsiz haber</title>
        <link>http://127.0.0.1/gizli.jpg</link>
        <description>Yalnızca metin</description>
      </item>
    </channel></rss>""".encode()


class NewsImageExtractionTests(unittest.TestCase):
    def test_feed_images_and_relative_resolution(self):
        items = _feed_items(rss_with_images(), "Kaynak", "gundem")
        self.assertEqual(len(items), 4)
        self.assertEqual(items[0]["image"], "https://cdn.test/a.jpg")
        self.assertEqual(items[1]["image"], "https://cdn.test/b.jpeg")
        self.assertEqual(items[2]["image"], "https://example.test/rel.png")
        self.assertEqual(items[3]["image"], "")

    def test_og_image_variants(self):
        html = """
        <html><head>
          <meta content="/img/kapak.jpg" property="og:image">
          <link rel="image_src" href="https://cdn.test/alt.png">
        </head></html>
        """
        self.assertEqual(
            extract_og_image(html, "https://news.test/story/1"),
            "https://news.test/img/kapak.jpg",
        )
        twitter = '<meta name="twitter:image" content="https://cdn.test/twitter.webp">'
        self.assertEqual(extract_og_image(twitter), "https://cdn.test/twitter.webp")
        secure = '<meta property="og:image:secure_url" content="https://cdn.test/s.jpg">'
        self.assertEqual(extract_og_image(secure), "https://cdn.test/s.jpg")
        self.assertEqual(extract_og_image('<meta property="og:image" content="http://localhost/a.jpg">'), "")

    def test_http_url_guard(self):
        self.assertEqual(_http_url("https://example.test/a.jpg"), "https://example.test/a.jpg")
        for blocked in (
            "file:///C:/gizli.jpg",
            "http://localhost:8080/a.jpg",
            "http://10.0.0.2/a.jpg",
            "http://192.168.1.4/a.jpg",
            "http://169.254.0.1/a.jpg",
            "javascript:alert(1)",
            "",
        ):
            self.assertEqual(_http_url(blocked), "", blocked)

    def test_publish_sanitizes_and_notifies(self):
        received = []
        add_news_listener(received.append)
        add_news_listener(received.append)
        try:
            _publish_news_items([
                {
                    "title": "Başlık",
                    "source": "Kaynak",
                    "url": "http://127.0.0.1/x",
                    "image": "https://cdn.test/a.jpg",
                    "other_sources": ["A", "B"],
                },
                {"title": "", "url": "https://example.test/y"},
            ])
        finally:
            remove_news_listener(received.append)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0][0]["url"], "")
        self.assertEqual(received[0][0]["image"], "https://cdn.test/a.jpg")
        self.assertEqual(received[0][0]["other_sources"], ["A", "B"])

    def test_service_get_publishes_snapshot(self):
        received = []
        add_news_listener(received.append)
        now = datetime.now(timezone.utc).isoformat()
        items = [{
            "title": "Zirve görüşmesi",
            "summary": "Özet",
            "source": "A",
            "category": "gundem",
            "published_at": now,
            "url": "https://example.test/a",
            "image": "https://cdn.test/a.jpg",
        }]
        service = NewsService(feeds=[{"source": "A", "url": "http://a", "category": "gundem"}])
        try:
            with patch.object(service, "_get_feed", return_value=items):
                text = service.get({"limit": 1})
        finally:
            remove_news_listener(received.append)
        payload = json.loads(text[text.index("{"):])
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0][0]["image"], "https://cdn.test/a.jpg")

    def test_download_image_guards(self):
        with patch("app.core.news_tools.requests.get") as get:
            self.assertIsNone(download_image("http://127.0.0.1/a.jpg"))
            get.assert_not_called()

        png = b"\x89PNG\r\n\x1a\n" + b"x" * 5000
        ok = FakeStream(png)
        with patch("app.core.news_tools.requests.get", return_value=ok):
            self.assertEqual(download_image("https://cdn.test/a.jpg"), png)
        self.assertTrue(ok.closed)

        for response in (
            FakeStream(png, "image/svg+xml"),
            FakeStream(png[:100], "image/jpeg"),
            FakeStream(png, "image/jpeg", length=9_000_000),
            FakeStream(png, "text/html"),
            FakeStream(b"<html>" + b"x" * 5000, ""),
            FakeStream(b"x" * 5000, ""),
        ):
            with patch("app.core.news_tools.requests.get", return_value=response):
                self.assertIsNone(download_image("https://cdn.test/a.jpg"))
            self.assertTrue(response.closed)

    def test_fetch_article_image(self):
        html = (
            '<html><head><meta property="og:image" content="/img/kapak.jpg"></head></html>'
        ).encode()
        page = FakeStream(html, "text/html; charset=utf-8", url="https://news.test/story/1")
        with patch("app.core.news_tools.requests.get", return_value=page):
            self.assertEqual(
                fetch_article_image("https://news.test/story/1"),
                "https://news.test/img/kapak.jpg",
            )
        self.assertTrue(page.closed)

        pdf = FakeStream(b"%PDF", "application/pdf")
        with patch("app.core.news_tools.requests.get", return_value=pdf):
            self.assertEqual(fetch_article_image("https://news.test/dosya.pdf"), "")


class NewsStageTests(unittest.TestCase):
    def setUp(self):
        loader_patch = patch.object(stage_module._ImageLoader, "start", autospec=True)
        loader_patch.start()
        self.addCleanup(loader_patch.stop)
        self.parent = QWidget()
        self.parent.resize(1200, 820)
        self.parent.show()
        self.busy = True
        self.stage = NewsStage(self.parent, is_busy=lambda: self.busy)
        self.stage.show_items(self._items(3))
        QTest.qWait(520)

    def tearDown(self):
        self.stage.dismiss()
        self.parent.deleteLater()
        QTest.qWait(30)

    @staticmethod
    def _items(count):
        return [
            {
                "title": f"Gündem başlığı {index}",
                "source": "Kaynak",
                "category": "gundem",
                "published_at": datetime.now(timezone.utc).isoformat(),
                "url": f"https://example.test/{index}",
                "image": "",
            }
            for index in range(count)
        ]

    def _paint_slide(self, pixmap_data):
        pixmap = QPixmap(640, 360)
        pixmap.fill(QColor("#334455" if pixmap_data == "a" else "#553344"))
        return pixmap

    def test_expands_from_pill(self):
        self.assertTrue(self.stage.isVisible())
        self.assertTrue(self.stage._expanded)
        self.assertEqual(self.stage.geometry(), self.stage._expanded_rect())
        self.assertEqual(len(self.stage._slides), 3)
        self.assertEqual(self.stage._index, 0)

    def test_advance_and_guard_rapid_clicks(self):
        self.stage.next_slide()
        self.assertEqual(self.stage._index, 1)
        self.assertEqual(self.stage._previous, 0)
        self.stage.next_slide()
        self.assertEqual(self.stage._index, 1)
        self.stage._transition_start = time.monotonic() - 1.0
        self.stage._tick()
        self.assertEqual(self.stage._previous, -1)

    def test_paints_transition_and_fallback(self):
        self.stage._pixmaps[0] = self._paint_slide("a")
        self.stage._pixmaps[1] = self._paint_slide("b")
        self.stage.next_slide()
        self.stage._transition = 0.5
        frame = self.stage.grab()
        self.assertFalse(frame.isNull())
        self.assertEqual(frame.size(), self.stage.size())
        self.stage._pixmaps[0] = None
        self.stage._pixmaps[1] = None
        self.assertFalse(self.stage.grab().isNull())

    def test_re_expands_when_new_items_arrive_while_collapsing(self):
        self.stage.collapse()
        self.assertFalse(self.stage._expanded)
        self.assertTrue(self.stage._geometry_hiding)
        self.stage.show_items(self._items(2))
        self.assertTrue(self.stage._expanded)
        self.assertFalse(self.stage._geometry_hiding)
        self.assertTrue(self.stage.isVisible())
        self.assertEqual(len(self.stage._slides), 2)
        QTest.qWait(520)
        self.assertEqual(self.stage.geometry(), self.stage._expanded_rect())

    def test_auto_collapse_when_idle(self):
        self.busy = False
        self.stage._shown_at -= 20.0
        self.stage._slide_started -= 3.0
        self.stage._last_busy_check -= 10.0
        self.stage._tick()
        self.assertFalse(self.stage._expanded)
        QTest.qWait(520)
        self.assertFalse(self.stage.isVisible())

    def test_close_click_and_dismiss(self):
        self.stage.grab()
        point = self.stage._close_rect.center()
        QTest.mouseClick(self.stage, Qt.MouseButton.LeftButton, pos=point)
        self.assertFalse(self.stage._expanded)
        QTest.qWait(520)
        self.assertFalse(self.stage.isVisible())

        self.stage.show_items(self._items(1))
        QTest.qWait(520)
        self.assertTrue(self.stage.isVisible())
        self.stage.dismiss()
        self.assertFalse(self.stage.isVisible())
        self.assertEqual(self.stage._slides, [])

    def test_relative_time(self):
        now = datetime.now(timezone.utc)
        self.assertEqual(_relative_time(now.isoformat()), "az önce")
        self.assertEqual(_relative_time((now - timedelta(minutes=5)).isoformat()), "5 dk önce")
        self.assertEqual(_relative_time((now - timedelta(hours=3)).isoformat()), "3 sa önce")
        self.assertEqual(_relative_time((now - timedelta(days=2)).isoformat()), "2 gün önce")
        self.assertEqual(_relative_time("bozuk"), "")


if __name__ == "__main__":
    unittest.main()
