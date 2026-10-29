import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.steam_tools import SteamWorkshop, _parse_vdf, _WorkshopSearchParser

SEARCH_HTML = """
<html><body>
<a href="https://steamcommunity.com/sharedfiles/filedetails/?id=111">
  <img alt="Birinci Mod" src="https://example.com/1.jpg">
</a>
<a class="ugc" href="https://steamcommunity.com/sharedfiles/filedetails/?id=222">
  <div class="workshopItemTitle">İkinci Mod</div>
</a>
<a href="https://steamcommunity.com/sharedfiles/filedetails/?id=111"><img alt="Birinci Mod" src="x"></a>
</body></html>
"""

DETAILS = {
    "111": {
        "publishedfileid": "111", "title": "Birinci Mod", "file_size": 1234,
        "subscriptions": 10, "favorited": 2, "views": 100, "time_updated": 1700000000,
        "tags": [{"tag": "Scenario"}, {"tag": "Rulers"}], "visibility": 0, "banned": 0,
    },
}


class WorkshopSearchParserTests(unittest.TestCase):
    def test_collects_ids_titles_and_deduplicates(self):
        parser = _WorkshopSearchParser()
        parser.feed(SEARCH_HTML)
        self.assertEqual(parser.order, ["111", "222"])
        self.assertEqual(parser.titles["111"], "Birinci Mod")
        self.assertEqual(parser.titles["222"], "İkinci Mod")


class SteamWorkshopTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(os.path.realpath(self._tmp.name))
        self.steam = SteamWorkshop()

    def test_search_uses_public_page_and_enriches_with_api(self):
        with patch.object(
            self.steam.reader, "_get",
            return_value=(SEARCH_HTML.encode(), "text/html", "utf-8", "https://steamcommunity.com/browse"),
        ) as get, patch.object(self.steam, "_remote_details", return_value=DETAILS):
            result = json.loads(self.steam.search({"app_id": "2772750", "query": "mod"}))
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["items"][0]["title"], "Birinci Mod")
        self.assertEqual(result["items"][0]["size_bytes"], 1234)
        self.assertEqual(result["items"][0]["tags"], ["Scenario", "Rulers"])
        self.assertEqual(result["items"][1]["title"], "İkinci Mod")
        self.assertIn("searchtext=mod", get.call_args.args[0])
        self.assertIn("hesap tercihlerine", result["note"])

    def test_search_without_results_is_reported(self):
        with patch.object(
            self.steam.reader, "_get",
            return_value=(b"<html><body>bos</body></html>", "text/html", "utf-8", "https://example.com"),
        ):
            result = json.loads(self.steam.search({"app_id": "2772750"}))
        self.assertEqual(result["count"], 0)
        self.assertIn("Sonuç bulunamadı", result["note"])

    def test_open_uses_steam_item_protocol(self):
        with patch("app.core.steam_tools.os.startfile") as startfile:
            result = self.steam.open_item({"item_id": "3762029150"})
        startfile.assert_called_once_with("steam://url/CommunityFilePage/3762029150")
        self.assertIn("3762029150", result)

    def test_status_reads_acf_and_content_folder(self):
        library = self.base / "Steam"
        workshop = library / "steamapps" / "workshop"
        content = workshop / "content" / "2772750" / "111"
        content.mkdir(parents=True)
        (content / "mod.txt").write_bytes(b"x" * 123)
        (workshop / "appworkshop_2772750.acf").write_text(
            '"AppWorkshop"\n{\n'
            '\t"appid"\t"2772750"\n'
            '\t"NeedsDownload"\t"0"\n'
            '\t"SizeOnDisk"\t"123"\n'
            '\t"WorkshopItemsInstalled"\n\t{\n'
            '\t\t"111"\n\t\t{\n'
            '\t\t\t"size"\t"123"\n\t\t\t"manifest"\t"99"\n'
            '\t\t}\n\t}\n}\n',
            encoding="utf-8",
        )
        self.steam._libraries = [library]
        result = json.loads(self.steam.status({"app_id": "2772750", "item_id": "111"}))
        self.assertTrue(result["found"])
        self.assertTrue(result["subscribed"])
        self.assertTrue(result["installed"])
        self.assertEqual(result["libraries"][0]["file_count"], 1)
        self.assertEqual(result["libraries"][0]["bytes"], 123)
        self.assertIn("Abone ve içerik", result["summary"])

    def test_status_reports_missing_item(self):
        library = self.base / "Steam"
        (library / "steamapps" / "workshop").mkdir(parents=True)
        self.steam._libraries = [library]
        result = json.loads(self.steam.status({"app_id": "2772750", "item_id": "999"}))
        self.assertFalse(result["found"])
        self.assertFalse(result["subscribed"])
        self.assertIn("görünmüyor", result["summary"])

    def test_invalid_ids_are_refused(self):
        from app.core.safety import ActionForbidden

        for args in ({"app_id": "abc"}, {"app_id": "2772750", "item_id": "../x"}):
            with self.subTest(args=args), self.assertRaises(ActionForbidden):
                self.steam.status(args)


class VdfParserTests(unittest.TestCase):
    def test_parses_nested_sections_and_escaped_paths(self):
        parsed = _parse_vdf(
            '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"C:\\\\Steam"\n\t\t"apps"\n\t\t{\n\t\t\t"1"\t\t"10"\n\t\t}\n\t}\n}\n'
        )
        self.assertEqual(parsed["libraryfolders"]["0"]["path"], "C:\\Steam")
        self.assertEqual(parsed["libraryfolders"]["0"]["apps"]["1"], "10")


if __name__ == "__main__":
    unittest.main()
