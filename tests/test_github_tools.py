import base64
import unittest
from unittest.mock import MagicMock, patch

import requests

from app.core.github_tools import GitHubTools
from app.core.safety import Risk, classify_action


class FakeResponse:
    def __init__(self, payload=None, status=200, headers=None, text=""):
        self.payload = payload
        self.status_code = status
        self.headers = headers or {"X-RateLimit-Remaining": "59"}
        self.text = text

    def json(self):
        if self.payload is None:
            raise ValueError("no json")
        return self.payload


def file_payload(text, path="app/main.py", size=None):
    raw = text.encode("utf-8")
    return {
        "type": "file",
        "path": path,
        "size": len(raw) if size is None else size,
        "encoding": "base64",
        "content": base64.b64encode(raw).decode("ascii"),
    }


class GitHubTests(unittest.TestCase):
    def setUp(self):
        self.tools = GitHubTools()

    def test_repo_summary_includes_license_and_cache(self):
        payload = {
            "full_name": "octo/demo",
            "description": "Demo",
            "stargazers_count": 42,
            "forks_count": 3,
            "open_issues_count": 1,
            "language": "Python",
            "license": {"spdx_id": "MIT"},
            "default_branch": "main",
            "topics": ["demo"],
            "archived": False,
            "updated_at": "2026-10-01T00:00:00Z",
            "html_url": "https://github.com/octo/demo",
        }
        with patch("app.core.github_tools.requests.get", return_value=FakeResponse(payload)) as get:
            first = self.tools.run({"action": "repo", "owner": "octo", "repo": "demo"})
            second = self.tools.run({"action": "repo", "owner": "octo", "repo": "demo"})

        self.assertIn("MIT", first)
        self.assertEqual(first, second)
        self.assertEqual(get.call_count, 1)

    def test_search_returns_ranked_repository_rows(self):
        payload = {
            "items": [
                {
                    "full_name": "a/one",
                    "description": "bir",
                    "stargazers_count": 10,
                    "language": "Rust",
                    "license": {"spdx_id": "Apache-2.0"},
                    "archived": False,
                    "updated_at": "2026-01-01T00:00:00Z",
                    "html_url": "https://github.com/a/one",
                }
            ]
        }
        with patch("app.core.github_tools.requests.get", return_value=FakeResponse(payload)) as get:
            result = self.tools.run({"action": "search", "query": "cli tool", "limit": 5})

        self.assertIn("a/one", result)
        self.assertIn("Apache-2.0", result)
        self.assertEqual(get.call_args.kwargs["params"]["q"], "cli tool")
        self.assertEqual(get.call_args.kwargs["params"]["per_page"], 5)

    def test_readme_returns_raw_text_with_warning(self):
        with patch("app.core.github_tools.requests.get", return_value=FakeResponse(text="# Demo\nKurulum")):
            result = self.tools.run({"action": "readme", "owner": "octo", "repo": "demo"})

        self.assertIn("güvenilmez", result)
        self.assertIn("# Demo", result)

    def test_tree_uses_default_branch_and_limits_entries(self):
        meta = {"default_branch": "main"}
        tree = {
            "truncated": False,
            "tree": [
                {"path": "app", "type": "tree"},
                {"path": "app/main.py", "type": "blob", "size": 12},
                {"path": "README.md", "type": "blob", "size": 8},
            ],
        }

        def fake_get(url, **kwargs):
            if "/git/trees/" in url:
                return FakeResponse(tree)
            return FakeResponse(meta)

        with patch("app.core.github_tools.requests.get", side_effect=fake_get) as get:
            result = self.tools.run({"action": "tree", "owner": "octo", "repo": "demo", "limit": 2})

        self.assertIn("app/main.py", result)
        self.assertIn('"count": 2', result)
        self.assertIn("/git/trees/main", get.call_args_list[-1].args[0])

    def test_file_decodes_text_and_marks_truncation(self):
        payload = file_payload("x" * 50)
        with patch("app.core.github_tools.requests.get", return_value=FakeResponse(payload)):
            with patch("app.core.github_tools.MAX_FILE_CHARS", 20):
                result = self.tools.run({"action": "file", "owner": "octo", "repo": "demo", "path": "app/main.py"})

        self.assertIn('"truncated": true', result)
        self.assertIn("xxxxxxxxxxxxxxxxxxxx", result)

    def test_directory_file_action_lists_entries(self):
        payload = [
            {"path": "app/main.py", "type": "file", "size": 10},
            {"path": "app/ui", "type": "dir"},
        ]
        with patch("app.core.github_tools.requests.get", return_value=FakeResponse(payload)):
            result = self.tools.run({"action": "file", "owner": "octo", "repo": "demo", "path": "app"})

        self.assertIn('"kind": "directory"', result)
        self.assertIn("app/ui", result)

    def test_invalid_repo_path_and_ref_are_rejected_without_request(self):
        with patch("app.core.github_tools.requests.get") as get:
            self.assertIn("❌", self.tools.run({"action": "repo", "owner": "../x", "repo": "demo"}))
            self.assertIn("❌", self.tools.run({"action": "file", "owner": "octo", "repo": "demo", "path": "../secret"}))
            self.assertIn("❌", self.tools.run({"action": "tree", "owner": "octo", "repo": "demo", "ref": "-bad"}))
        get.assert_not_called()

    def test_rate_limit_and_missing_repo_get_clear_messages(self):
        limited = FakeResponse(
            {"message": "API rate limit exceeded"},
            status=403,
            headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1760000000"},
        )
        with patch("app.core.github_tools.requests.get", return_value=limited):
            result = self.tools.run({"action": "repo", "owner": "octo", "repo": "demo"})
        self.assertIn("istek sınırı", result)

        with patch("app.core.github_tools.requests.get", return_value=FakeResponse(status=404)):
            missing = self.tools.run({"action": "readme", "owner": "octo", "repo": "yok"})
        self.assertIn("bulunamadı", missing)

    def test_binary_file_is_rejected(self):
        raw = b"\x00\x01\x02"
        payload = {
            "type": "file",
            "path": "logo.bin",
            "size": len(raw),
            "encoding": "base64",
            "content": base64.b64encode(raw).decode("ascii"),
        }
        with patch("app.core.github_tools.requests.get", return_value=FakeResponse(payload)):
            result = self.tools.run({"action": "file", "owner": "octo", "repo": "demo", "path": "logo.bin"})
        self.assertIn("İkili dosya", result)

    def test_network_error_is_reported(self):
        with patch("app.core.github_tools.requests.get", side_effect=requests.ConnectionError("down")):
            result = self.tools.run({"action": "repo", "owner": "octo", "repo": "demo"})
        self.assertIn("ulaşılamadı", result)

    def test_releases_skip_drafts_and_include_assets(self):
        payload = [
            {
                "tag_name": "v2.0.0",
                "name": "Sürüm 2",
                "draft": False,
                "prerelease": False,
                "published_at": "2026-10-01T00:00:00Z",
                "html_url": "https://github.com/octo/demo/releases/v2.0.0",
                "body": "Yenilikler\n" + "a" * 2000,
                "assets": [
                    {"name": "demo.zip", "size": 1234, "download_count": 7},
                    {"name": "demo.tar.gz", "size": 999, "download_count": 1},
                ],
            },
            {
                "tag_name": "v3.0.0-rc",
                "name": "Aday",
                "draft": True,
                "prerelease": True,
                "body": "taslak",
                "assets": [],
            },
            {
                "tag_name": "v1.5.0",
                "name": None,
                "draft": False,
                "prerelease": True,
                "published_at": "2026-05-01T00:00:00Z",
                "html_url": "https://github.com/octo/demo/releases/v1.5.0",
                "body": "",
                "assets": [],
            },
        ]
        with patch("app.core.github_tools.requests.get", return_value=FakeResponse(payload)) as get:
            result = self.tools.run({"action": "releases", "owner": "octo", "repo": "demo", "limit": 5})

        self.assertIn("v2.0.0", result)
        self.assertIn('"prerelease": false', result)
        self.assertIn("demo.zip", result)
        self.assertNotIn("v3.0.0-rc", result)
        self.assertIn("v1.5.0", result)
        self.assertIn('"count": 2', result)
        self.assertLessEqual(len(result), 9000)
        self.assertEqual(get.call_args.kwargs["params"]["per_page"], 5)

    def test_releases_limit_is_clamped(self):
        payload = [
            {"tag_name": f"v{i}", "draft": False, "prerelease": False, "body": "", "assets": []}
            for i in range(10)
        ]
        with patch("app.core.github_tools.requests.get", return_value=FakeResponse(payload)) as get:
            result = self.tools.run({"action": "releases", "owner": "octo", "repo": "demo", "limit": 99})

        self.assertIn('"count": 10', result)
        self.assertEqual(get.call_args.kwargs["params"]["per_page"], 10)

    def test_commits_return_recent_messages_without_body(self):
        payload = [
            {
                "sha": "abcdef1234567890",
                "html_url": "https://github.com/octo/demo/commit/abcdef1",
                "commit": {
                    "message": "Hata düzeltmesi\n\nUzun açıklama satırı",
                    "author": {"name": "Ada", "date": "2026-10-20T10:00:00Z"},
                },
            },
            {
                "sha": "9876543210fedcba",
                "html_url": "https://github.com/octo/demo/commit/9876543",
                "commit": {"message": "Yeni özellik", "author": {"name": "Bora", "date": "2026-10-19T09:00:00Z"}},
            },
        ]
        with patch("app.core.github_tools.requests.get", return_value=FakeResponse(payload)) as get:
            result = self.tools.run(
                {"action": "commits", "owner": "octo", "repo": "demo", "ref": "main", "limit": 3}
            )

        self.assertIn('"sha": "abcdef123456"', result)
        self.assertIn("Hata düzeltmesi", result)
        self.assertNotIn("Uzun açıklama", result)
        self.assertIn('"count": 2', result)
        self.assertIn('"ref": "main"', result)
        self.assertEqual(get.call_args.kwargs["params"], {"per_page": 3, "sha": "main"})

    def test_commits_limit_is_clamped(self):
        payload = [
            {"sha": f"{index:040x}", "commit": {"message": f"c{index}", "author": {}}}
            for index in range(10)
        ]
        with patch("app.core.github_tools.requests.get", return_value=FakeResponse(payload)) as get:
            result = self.tools.run({"action": "commits", "owner": "octo", "repo": "demo", "limit": 99})

        self.assertIn('"count": 10', result)
        self.assertEqual(get.call_args.kwargs["params"], {"per_page": 10})

    def test_commit_detail_includes_files_and_capped_patch(self):
        payload = {
            "sha": "abcdef1234567890abcdef1234567890abcdef12",
            "html_url": "https://github.com/octo/demo/commit/abcdef1",
            "commit": {
                "message": "Kritik düzeltme\n\nAyrıntılı açıklama",
                "author": {"name": "Ada", "date": "2026-10-20T10:00:00Z"},
            },
            "author": {"login": "ada"},
            "stats": {"additions": 12, "deletions": 3, "total": 15},
            "parents": [{"sha": "1234567890abcdef1234567890abcdef12345678"}],
            "files": [
                {
                    "filename": "app/main.py", "status": "modified",
                    "additions": 10, "deletions": 2, "changes": 12,
                    "patch": "x" * 1200,
                },
                {
                    "filename": "eski.py", "previous_filename": "yeni.py",
                    "status": "renamed", "additions": 2, "deletions": 1, "changes": 3,
                },
            ],
        }
        with patch("app.core.github_tools.requests.get", return_value=FakeResponse(payload)) as get:
            result = self.tools.run({
                "action": "commit", "owner": "octo", "repo": "demo", "ref": "abcdef1", "limit": 2,
            })

        self.assertIn("/commits/abcdef1", get.call_args.args[0])
        self.assertIn("Kritik düzeltme", result)
        self.assertIn("Ayrıntılı açıklama", result)
        self.assertIn('"login": "ada"', result)
        self.assertIn('"total": 15', result)
        self.assertIn('"files_count": 2', result)
        self.assertIn("app/main.py", result)
        self.assertIn("yeni.py", result)
        self.assertIn('"patch": "' + "x" * 800 + "…", result)

    def test_commit_requires_ref_without_request(self):
        with patch("app.core.github_tools.requests.get") as get:
            result = self.tools.run({"action": "commit", "owner": "octo", "repo": "demo"})
        self.assertIn("ref", result)
        get.assert_not_called()

    def test_compare_reports_range_commits_and_files(self):
        payload = {
            "status": "ahead",
            "ahead_by": 2,
            "behind_by": 0,
            "total_commits": 2,
            "base_commit": {"sha": "1111111111111111111111111111111111111111"},
            "head_commit": {"sha": "2222222222222222222222222222222222222222"},
            "commits": [{
                "sha": "abcdef1234567890",
                "html_url": "https://github.com/octo/demo/commit/abcdef1",
                "commit": {"message": "Sürüm hazırlığı\n\nAyrıntı", "author": {"name": "Ada", "date": "2026-10-20T10:00:00Z"}},
            }],
            "files": [{
                "filename": "app/main.py", "status": "modified",
                "additions": 4, "deletions": 2, "changes": 6, "patch": "z" * 1000,
            }],
        }
        with patch("app.core.github_tools.requests.get", return_value=FakeResponse(payload)) as get:
            result = self.tools.run({
                "action": "compare", "owner": "octo", "repo": "demo",
                "base": "v1.0", "head": "v2.0", "limit": 1,
            })

        self.assertIn("/compare/v1.0...v2.0", get.call_args.args[0])
        self.assertIn('"ahead_by": 2', result)
        self.assertIn('"base_sha": "111111111111"', result)
        self.assertIn('"head_sha": "222222222222"', result)
        self.assertIn("Sürüm hazırlığı", result)
        self.assertIn("app/main.py", result)
        self.assertIn('"files_count": 1', result)
        self.assertIn('"patch": "' + "z" * 800 + "…", result)

    def test_compare_derives_head_sha_from_last_commit(self):
        payload = {
            "status": "diverged",
            "ahead_by": 2,
            "behind_by": 1,
            "total_commits": 2,
            "base_commit": {"sha": "1111111111111111111111111111111111111111"},
            "commits": [
                {"sha": "aaaaaaaaaaaa1111", "commit": {"message": "İlk", "author": {}}},
                {"sha": "bbbbbbbbbbbb2222", "commit": {"message": "Son", "author": {}}},
            ],
            "files": [],
        }
        with patch("app.core.github_tools.requests.get", return_value=FakeResponse(payload)):
            result = self.tools.run({
                "action": "compare", "owner": "octo", "repo": "demo",
                "base": "main", "head": "feature",
            })

        self.assertIn('"head_sha": "bbbbbbbbbbbb"', result)

    def test_compare_requires_base_and_head(self):
        with patch("app.core.github_tools.requests.get") as get:
            result = self.tools.run({"action": "compare", "owner": "octo", "repo": "demo", "base": "main"})
        self.assertIn("base ve head", result)
        get.assert_not_called()

    def test_issues_skip_pull_requests_and_include_labels(self):
        payload = [
            {
                "number": 12,
                "title": "Çökme düzeltmesi",
                "state": "open",
                "user": {"login": "ada"},
                "labels": [{"name": "bug"}, {"name": "öncelikli"}],
                "comments": 4,
                "created_at": "2026-10-20T10:00:00Z",
                "updated_at": "2026-10-21T10:00:00Z",
                "html_url": "https://github.com/octo/demo/issues/12",
                "body": "Hata ayrıntısı " + "x" * 900,
            },
            {
                "number": 13,
                "title": "Bir PR",
                "state": "open",
                "pull_request": {"url": "https://api.github.com/repos/octo/demo/pulls/13"},
            },
            {
                "number": 9,
                "title": "Eski hata",
                "state": "closed",
                "user": {"login": "bora"},
                "labels": [],
                "comments": 0,
                "html_url": "https://github.com/octo/demo/issues/9",
            },
        ]
        with patch("app.core.github_tools.requests.get", return_value=FakeResponse(payload)) as get:
            result = self.tools.run({
                "action": "issues", "owner": "octo", "repo": "demo",
                "state": "all", "labels": "bug,öncelikli", "limit": 2,
            })

        self.assertIn("/issues", get.call_args.args[0])
        self.assertIn('"number": 12', result)
        self.assertIn("Çökme düzeltmesi", result)
        self.assertIn("ada", result)
        self.assertIn("öncelikli", result)
        self.assertIn('"comments": 4', result)
        self.assertNotIn("Bir PR", result)
        self.assertIn("Eski hata", result)
        self.assertIn('"count": 2', result)
        self.assertEqual(get.call_args.kwargs["params"]["state"], "all")
        self.assertEqual(get.call_args.kwargs["params"]["labels"], "bug,öncelikli")
        self.assertEqual(get.call_args.kwargs["params"]["per_page"], 4)

    def test_pulls_include_branch_and_merge_fields(self):
        payload = [{
            "number": 21,
            "title": "Yeni özellik",
            "state": "closed",
            "user": {"login": "ada"},
            "labels": [{"name": "feature"}],
            "comments": 2,
            "html_url": "https://github.com/octo/demo/pull/21",
            "draft": False,
            "merged_at": "2026-10-22T10:00:00Z",
            "head": {"ref": "feature-x"},
            "base": {"ref": "main"},
        }]
        with patch("app.core.github_tools.requests.get", return_value=FakeResponse(payload)) as get:
            result = self.tools.run({
                "action": "pulls", "owner": "octo", "repo": "demo", "state": "closed",
            })

        self.assertIn("/pulls", get.call_args.args[0])
        self.assertIn("Yeni özellik", result)
        self.assertIn('"head": "feature-x"', result)
        self.assertIn('"base": "main"', result)
        self.assertIn('"merged_at": "2026-10-22T10:00:00Z"', result)
        self.assertIn('"state": "closed"', result)

    def test_issues_reject_invalid_state_without_request(self):
        with patch("app.core.github_tools.requests.get") as get:
            result = self.tools.run({
                "action": "issues", "owner": "octo", "repo": "demo", "state": "pending",
            })
        self.assertIn("❌", result)
        get.assert_not_called()

    def test_github_is_read_risk(self):
        self.assertEqual(classify_action("github", {"action": "repo"}, MagicMock()), Risk.READ)


if __name__ == "__main__":
    unittest.main()
