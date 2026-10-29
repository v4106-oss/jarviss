import unittest
from unittest.mock import patch

import hiz_testi
from tests.test_ai_gemini import FakeResponse, FakeSettings, FakeStream, model_list, sse


class StubSettings(FakeSettings):
    pass


class SpeedTestTests(unittest.TestCase):
    def run_main(self, current="gemini-3.5-flash", reply="e"):
        settings = StubSettings(model=current)
        lines = []
        names = ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.8-flash-tts"]

        def fake_post(url, **kwargs):
            if "gemini-3.5-flash-lite" in url:
                return FakeStream(sse("Hızlı", " cevap"))
            if "gemini-3.5-flash:" in url or "gemini-3.5-flash:stream" in url:
                return FakeResponse(503, {"error": {"message": "high demand"}})
            return FakeStream(sse("Yavaş", " cevap"))

        with patch("app.core.ai.SESSION.get", return_value=model_list(*names)), \
             patch("app.core.ai.SESSION.post", side_effect=fake_post):
            code = hiz_testi.main(settings, input_fn=lambda _p: reply, out=lines.append)
        return code, settings, "\n".join(lines)

    def test_candidates_skip_tts_and_include_current(self):
        names = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash",
                 "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.8-flash-tts", "gemini-2.0-flash"]
        chosen = hiz_testi.candidate_models(names, "gemini-9.9-custom")
        self.assertEqual(chosen[:3], ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash"])
        self.assertIn("gemini-3.5-flash-lite", chosen)
        self.assertIn("gemini-9.9-custom", chosen)
        self.assertNotIn("gemini-3.8-flash-tts", chosen)

    def test_reports_errors_and_offers_fastest_model(self):
        code, settings, output = self.run_main(reply="e")
        self.assertEqual(code, 0)
        self.assertIn("hata: HTTP 503", output)
        self.assertIn("En hızlı model:", output)
        self.assertIn("Kaydedildi", output)
        self.assertIn(settings.model, ("gemini-3.8-flash", "gemini-3.5-flash-lite"))
        self.assertNotEqual(settings.model, "gemini-3.5-flash")

    def test_declining_keeps_current_model(self):
        _code, settings, output = self.run_main(reply="h")
        self.assertEqual(settings.model, "gemini-3.5-flash")
        self.assertIn("Model değiştirilmedi", output)
        self.assertNotIn("Kaydedildi", output)

    def test_no_api_key_stops_early(self):
        lines = []
        code = hiz_testi.main(StubSettings(api_key=""), out=lines.append)
        self.assertEqual(code, 1)
        self.assertIn("API anahtarı yok", lines[0])


if __name__ == "__main__":
    unittest.main()
