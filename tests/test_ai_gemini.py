import unittest
import time
from unittest.mock import patch

import requests

from app.core.ai import AI, TOOL_DECLARATIONS, best_model, model_rank


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self.ok = status < 400
        self._body = body if body is not None else {}
        self.text = str(self._body)

    def json(self):
        return self._body


def answer(text="Merhaba!"):
    return FakeResponse(200, {"candidates": [{"content": {"parts": [{"text": text}]}}]})


def tool_call(name, args, signature=None):
    part = {"functionCall": {"name": name, "args": args}}
    if signature:
        part["thoughtSignature"] = signature
    return FakeResponse(200, {"candidates": [{"content": {"role": "model", "parts": [part]}}]})


def ollama_tool_call(name, args):
    return FakeResponse(200, {"message": {
        "role": "assistant", "content": "",
        "tool_calls": [{"function": {"name": name, "arguments": args}}],
    }})


def openai_answer(text="Merhaba!"):
    return FakeResponse(200, {"choices": [{"message": {"role": "assistant", "content": text}}]})


def openai_tool_call(name, args, call_id="call_1"):
    import json
    return FakeResponse(200, {"choices": [{"message": {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": call_id,
            "function": {"name": name, "arguments": json.dumps(args)},
        }],
    }}]})


class FakeStream(FakeResponse):
    """Google'ın SSE akışını taklit eder."""

    def __init__(self, events, status=200):
        super().__init__(status, {})
        self._lines = [("data: " + __import__("json").dumps(e)).encode() for e in events]
        self.closed = False

    def iter_lines(self, chunk_size=512):
        for line in self._lines:
            yield line
            yield b""

    def close(self):
        self.closed = True


def sse(*texts, finish="STOP"):
    events = [{"candidates": [{"content": {"parts": [{"text": t}]}}]} for t in texts]
    events[-1]["candidates"][0]["finishReason"] = finish
    return events


def model_list(*names):
    return FakeResponse(200, {"models": [
        {"name": f"models/{n}", "supportedGenerationMethods": ["generateContent"]} for n in names
    ]})


class FakeStorage:
    def __init__(self):
        self.messages = []

    def add_message(self, role, content, source=None):
        self.messages.append((role, content))

    def get_all_memory(self):
        return []

    def get_conversation_history(self, limit=16):
        return []


class FakeSettings:
    def __init__(self, api_key="AQ.test-key-123", model="gemini-1.5-flash", access_mode="approval",
                 agent_step_limit=0, agent_seconds_limit=0, agent_loop_limit=0):
        self.api_key = api_key
        self.model = model
        self.provider = "gemini"
        self.ollama_url = "http://localhost:11434"
        self.ollama_model = "qwen2.5-coder:3b"
        self.access_mode = access_mode
        self.agent_step_limit = agent_step_limit
        self.agent_seconds_limit = agent_seconds_limit
        self.agent_loop_limit = agent_loop_limit

    def set_model(self, model):
        self.model = model


def make_ai(**kwargs):
    return AI(storage=FakeStorage(), settings=FakeSettings(**kwargs))


class ToolDeclarationTests(unittest.TestCase):
    def test_download_and_steam_tools_are_declared_with_required_fields(self):
        declarations = {
            item["name"]: item
            for group in TOOL_DECLARATIONS
            for item in group["functionDeclarations"]
        }
        download = declarations["download_file"]
        self.assertEqual(download["parameters"]["required"], ["url", "path"])
        steam = declarations["steam_workshop"]
        self.assertEqual(steam["parameters"]["required"], ["action"])
        self.assertEqual(
            steam["parameters"]["properties"]["action"]["enum"],
            ["search", "open", "status"],
        )
        self.assertIn("yaş", download["description"].casefold())
        ocr = declarations["find_text_on_screen"]
        self.assertEqual(ocr["parameters"]["required"], ["text"])
        self.assertIn("OCR", ocr["description"])

    def test_system_prompt_guides_vision_fallback_and_content_boundaries(self):
        prompt = AI.DEFAULT_SYSTEM_PROMPT
        self.assertIn("steam_workshop", prompt)
        self.assertIn("download_file", prompt)
        self.assertIn("UIA", prompt)
        self.assertIn("find_text_on_screen", prompt)
        self.assertIn("atlatılmaz", prompt)
        self.assertIn("undo_last", prompt)


class ModelPickTests(unittest.TestCase):
    def test_prefers_newest_flash_and_skips_non_chat_models(self):
        names = [
            "gemini-2.5-flash", "gemini-3-flash-preview", "gemini-3.7-flash", "gemini-3.8-flash",
            "gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts", "gemini-2.5-flash-image",
            "gemini-3.1-flash-live-preview", "gemini-omni-1.1-flash", "gemini-3.5-flash-lite",
            "gemini-embedding-001", "antigravity-preview-09-2026",
        ]
        self.assertEqual(best_model(names), "gemini-3.8-flash")
        self.assertEqual(best_model(names, exclude="gemini-3.8-flash"), "gemini-3.7-flash")

    def test_non_chat_models_have_no_rank(self):
        for name in ("gemini-3.8-flash-tts", "gemini-2.5-flash-image", "gemini-3.1-flash-live-preview", "gemma-3-27b"):
            self.assertIsNone(model_rank(name), name)

    def test_returns_none_when_nothing_fits(self):
        self.assertIsNone(best_model(["gemini-3.8-flash-tts"]))


class AskTests(unittest.TestCase):
    def setUp(self):
        AI.reset_quota()

    def test_key_goes_in_header_not_url(self):
        ai = make_ai(model="gemini-3.8-flash")
        with patch("app.core.ai.SESSION.post", return_value=answer("Selam")) as post:
            self.assertEqual(ai.ask("merhaba"), "Selam")
        args, kwargs = post.call_args
        self.assertTrue(args[0].endswith("/models/gemini-3.8-flash:generateContent"))
        self.assertNotIn("key=", args[0])
        self.assertNotIn("params", kwargs)
        self.assertEqual(kwargs["headers"]["x-goog-api-key"], "AQ.test-key-123")
        self.assertEqual(ai.storage.messages[-1], ("assistant", "Selam"))

    def test_missing_model_404_switches_to_available_model_and_saves_it(self):
        ai = make_ai(model="gemini-1.5-flash")
        listing = model_list("gemini-3.8-flash", "gemini-3.8-flash-tts", "gemini-3.7-flash")
        with patch("app.core.ai.SESSION.post", side_effect=[FakeResponse(404, {"error": {"message": "not found"}}), answer("Çalıştı")]) as post, \
             patch("app.core.ai.SESSION.get", return_value=listing):
            self.assertEqual(ai.ask("merhaba"), "Çalıştı")
        self.assertTrue(post.call_args_list[1].args[0].endswith("/models/gemini-3.8-flash:generateContent"))
        self.assertEqual(ai.settings.model, "gemini-3.8-flash")

    def test_404_with_no_working_alternative_lists_models_and_keeps_setting(self):
        ai = make_ai(model="gemini-1.5-flash")
        not_found = FakeResponse(404, {"error": {"message": "not found"}})
        with patch("app.core.ai.SESSION.post", return_value=not_found), \
             patch("app.core.ai.SESSION.get", return_value=model_list("gemini-3.8-flash")):
            result = ai.ask("merhaba")
        self.assertTrue(result.startswith("❌ Gemini API hatası (404)"))
        self.assertIn("gemini-3.8-flash", result)
        self.assertEqual(ai.settings.model, "gemini-1.5-flash")

    def test_errors_never_leak_the_api_key(self):
        ai = make_ai(model="gemini-3.8-flash")
        boom = RuntimeError("istek patladı: AQ.test-key-123 geçersiz")
        with patch("app.core.ai.SESSION.post", side_effect=boom):
            result = ai.ask("merhaba")
        self.assertNotIn("AQ.test-key-123", result)
        self.assertIn("***", result)

    def test_connection_error_gives_friendly_message(self):
        ai = make_ai(model="gemini-3.8-flash")
        with patch("app.core.ai.SESSION.post", side_effect=requests.exceptions.ConnectionError("x")):
            self.assertIn("bağlanılamadı", ai.ask("merhaba"))

    def test_bad_key_and_quota_hints(self):
        ai = make_ai(model="gemini-3.8-flash")
        bad = FakeResponse(400, {"error": {"message": "API key not valid. Please pass a valid API key."}})
        with patch("app.core.ai.SESSION.post", return_value=bad):
            self.assertIn("aistudio.google.com/apikey", ai.ask("merhaba"))
        quota = FakeResponse(429, {"error": {"message": "quota"}})
        with patch("app.core.ai.SESSION.post", return_value=quota), \
             patch("app.core.ai.SESSION.get", return_value=model_list()):
            self.assertIn("kota", ai.ask("merhaba"))

    def test_thought_parts_are_hidden_and_blocked_prompt_is_not_stored(self):
        ai = make_ai(model="gemini-3.8-flash")
        body = {"candidates": [{"content": {"parts": [{"text": "iç düşünce", "thought": True}, {"text": "Cevap"}]}}]}
        with patch("app.core.ai.SESSION.post", return_value=FakeResponse(200, body)):
            self.assertEqual(ai.ask("a"), "Cevap")
        blocked = FakeResponse(200, {"promptFeedback": {"blockReason": "SAFETY"}})
        with patch("app.core.ai.SESSION.post", return_value=blocked):
            result = ai.ask("b")
        self.assertIn("SAFETY", result)
        self.assertNotIn(("assistant", result), ai.storage.messages)

    def test_empty_api_key_never_calls_google(self):
        ai = make_ai(api_key="")
        with patch("app.core.ai.SESSION.post") as post:
            self.assertIn("anahtarı yok", ai.ask("merhaba"))
        post.assert_not_called()


class ToolLoopTests(unittest.TestCase):
    def test_tool_result_and_thought_signature_are_returned_to_gemini(self):
        ai = make_ai(model="gemini-3.8-flash")
        calls = []
        responses = [
            tool_call("create_folder", {"path": "çaydarskm"}, signature="imza-123"),
            answer("Klasör oluşturuldu."),
        ]
        with patch("app.core.ai.SESSION.post", side_effect=responses) as post:
            result = ai.ask(
                "masa üstümde çaydarskm adlı klasör oluştur",
                tool_handler=lambda name, args: calls.append((name, args)) or "✅ Klasör oluşturuldu: çaydarskm",
            )
        self.assertEqual(result, "Klasör oluşturuldu.")
        self.assertEqual(calls, [("create_folder", {"path": "çaydarskm"})])
        first_payload = post.call_args_list[0].kwargs["json"]
        self.assertIn("tools", first_payload)
        second_contents = post.call_args_list[1].kwargs["json"]["contents"]
        self.assertEqual(second_contents[1]["parts"][0]["thoughtSignature"], "imza-123")
        function_response = second_contents[2]["parts"][0]["functionResponse"]
        self.assertEqual(function_response["name"], "create_folder")
        self.assertIn("Klasör oluşturuldu", function_response["response"]["result"])

    def test_tool_requests_are_not_streamed_as_fake_success_text(self):
        ai = make_ai(model="gemini-3.8-flash")
        chunks = []
        with patch("app.core.ai.SESSION.post", side_effect=[
            tool_call("open_application", {"application": "Microsoft Edge"}),
            answer("Microsoft Edge açıldı."),
        ]) as post:
            result = ai.ask(
                "microsoft edge aç",
                stream=True,
                on_chunk=chunks.append,
                tool_handler=lambda name, args: "✅ Uygulama başlatıldı: Microsoft Edge",
            )
        self.assertEqual(result, "Microsoft Edge açıldı.")
        self.assertEqual(chunks, [])
        self.assertNotIn("streamGenerateContent", post.call_args_list[0].args[0])

    def test_action_request_forces_a_tool_call_instead_of_advice(self):
        ai = make_ai(model="gemini-3.8-flash")
        responses = [
            answer("Steam'i açıp Atölye'den kendin abone olabilirsin."),
            tool_call("open_application", {"application": "Steam"}),
            answer("Steam açıldı, atölye sayfasına gidiyorum."),
        ]
        with patch("app.core.ai.SESSION.post", side_effect=responses) as post:
            result = ai.ask(
                "aoh3'te 1910 atolye modunu kur ve oyuna gir",
                tool_handler=lambda name, args: "✅ Uygulama başlatıldı: Steam",
            )
        self.assertEqual(result, "Steam açıldı, atölye sayfasına gidiyorum.")
        first_payload = post.call_args_list[0].kwargs["json"]
        self.assertEqual(first_payload["toolConfig"]["functionCallingConfig"]["mode"], "ANY")
        second_contents = post.call_args_list[1].kwargs["json"]["contents"]
        self.assertIn("SİSTEM DENETİMİ", second_contents[-1]["parts"][0]["text"])
        self.assertEqual(
            post.call_args_list[1].kwargs["json"]["toolConfig"]["functionCallingConfig"]["mode"], "ANY"
        )
        self.assertEqual(ai.storage.messages[-1], ("assistant", "Steam açıldı, atölye sayfasına gidiyorum."))

    def test_handoff_after_a_tool_call_is_nudged_into_continuing(self):
        ai = make_ai(model="gemini-3.8-flash")
        responses = [
            tool_call("open_application", {"application": "Steam"}),
            answer("Atölye sayfasında Abone Ol düğmesine kendin tıklayabilirsin."),
            tool_call("ui_control", {"window_title": "Steam", "control_name": "Abone Ol", "action": "click"}),
            answer("1910 moduna abone olundu ve oyun açıldı."),
        ]
        with patch("app.core.ai.SESSION.post", side_effect=responses) as post:
            result = ai.ask(
                "1910 modunu kur ve oyuna gir",
                tool_handler=lambda name, args: "✅ İşlem yapıldı",
            )
        self.assertEqual(result, "1910 moduna abone olundu ve oyun açıldı.")
        self.assertNotIn("toolConfig", post.call_args_list[1].kwargs["json"])
        self.assertEqual(
            post.call_args_list[2].kwargs["json"]["toolConfig"]["functionCallingConfig"]["mode"], "ANY"
        )

    def test_information_question_is_answered_without_forcing_tools(self):
        ai = make_ai(model="gemini-3.8-flash")
        with patch("app.core.ai.SESSION.post", return_value=answer("Steam bir oyun platformudur.")) as post:
            result = ai.ask("steam nedir?", tool_handler=lambda name, args: "✅")
        self.assertEqual(result, "Steam bir oyun platformudur.")
        self.assertNotIn("toolConfig", post.call_args.kwargs["json"])

    def test_failed_tool_triggers_thinking_turn(self):
        ai = make_ai(model="gemini-3.8-flash")
        responses = [
            tool_call("open_application", {"application": "OBS Studio"}),
            tool_call("open_application", {"application": "obs64"}),
            answer("OBS Studio açıldı."),
        ]

        def handler(name, args):
            if args.get("application") == "OBS Studio":
                return "❌ eşleşen kısayol bulunamadı"
            return "✅ Uygulama başlatıldı: obs64"

        with patch("app.core.ai.SESSION.post", side_effect=responses) as post:
            result = ai.ask("obs studio aç", tool_handler=handler)
        self.assertEqual(result, "OBS Studio açıldı.")
        second_contents = post.call_args_list[1].kwargs["json"]["contents"]
        texts = [part.get("text", "") for message in second_contents for part in message["parts"]]
        self.assertTrue(any("DÜŞÜNME TURU" in text for text in texts), texts)

    def test_gave_up_text_after_failure_forces_another_attempt(self):
        ai = make_ai(model="gemini-3.8-flash")
        responses = [
            tool_call("open_application", {"application": "OBS Studio"}),
            answer("OBS Studio kurulu değil, bulunamadı."),
            tool_call("open_application", {"application": "obs64"}),
            answer("OBS Studio açıldı."),
        ]

        def handler(name, args):
            if args.get("application") == "OBS Studio":
                return "❌ eşleşen kısayol bulunamadı"
            return "✅ Uygulama başlatıldı: obs64"

        with patch("app.core.ai.SESSION.post", side_effect=responses) as post:
            result = ai.ask("obs studio aç", tool_handler=handler)
        self.assertEqual(result, "OBS Studio açıldı.")
        third_payload = post.call_args_list[2].kwargs["json"]
        texts = [
            part.get("text", "")
            for message in third_payload["contents"]
            for part in message["parts"]
        ]
        self.assertTrue(any("DÜŞÜNME TURU" in text for text in texts), texts)
        self.assertEqual(third_payload["toolConfig"]["functionCallingConfig"]["mode"], "ANY")

    def test_repeated_handoff_ends_with_honest_failure(self):
        ai = make_ai(model="gemini-3.8-flash")
        advice = answer("Siteye gidip kendin abone olman gerekir.")
        with patch("app.core.ai.SESSION.post", return_value=advice):
            result = ai.ask("1910 modunu kur", tool_handler=lambda name, args: "✅")
        self.assertTrue(result.startswith("❌ İşlem tamamlanmadı"), result)
        self.assertNotIn(("assistant", result), ai.storage.messages)

    def test_full_access_does_not_block_repeated_identical_calls(self):
        ai = make_ai(model="gemini-3.8-flash", access_mode="full")
        calls = []
        responses = [tool_call("mouse_control", {"action": "click", "x": 10, "y": 20})] * 8 + [answer("Tıklandı.")]
        with patch("app.core.ai.SESSION.post", side_effect=responses):
            result = ai.ask(
                "ekrandaki düğmeye tıkla",
                tool_handler=lambda name, args: calls.append(name) or "✅ Tıklandı",
            )
        self.assertEqual(result, "Tıklandı.")
        self.assertEqual(len(calls), 8)

    def test_step_limit_stops_agent(self):
        ai = make_ai(model="gemini-3.8-flash", agent_step_limit=3)
        calls = []
        responses = [tool_call("mouse_control", {"action": "click", "x": 10, "y": 20})] * 10
        with patch("app.core.ai.SESSION.post", side_effect=responses):
            result = ai.ask(
                "ekrandaki düğmeye tıkla",
                tool_handler=lambda name, args: calls.append(name) or "✅ Tıklandı",
            )
        self.assertEqual(len(calls), 3)
        self.assertIn("3 Gemini adımını aştı", result)

    def test_step_limit_zero_means_unlimited(self):
        ai = make_ai(model="gemini-3.8-flash", agent_step_limit=0)
        calls = []
        responses = [tool_call("mouse_control", {"action": "click", "x": 10, "y": 20})] * 6 + [answer("Bitti.")]
        with patch("app.core.ai.SESSION.post", side_effect=responses):
            result = ai.ask(
                "ekrandaki düğmeye tıkla",
                tool_handler=lambda name, args: calls.append(name) or "✅ Tıklandı",
            )
        self.assertEqual(result, "Bitti.")
        self.assertEqual(len(calls), 6)

    def test_approval_mode_warns_then_stops_stagnant_repeats(self):
        ai = make_ai(model="gemini-3.8-flash", agent_loop_limit=5)
        calls = []
        responses = [tool_call("mouse_control", {"action": "click", "x": 10, "y": 20})] * 7
        with patch("app.core.ai.SESSION.post", side_effect=responses) as post:
            result = ai.ask(
                "ekrandaki düğmeye tıkla",
                tool_handler=lambda name, args: calls.append(name) or "✅ Tıklandı",
            )
        self.assertEqual(len(calls), 6)
        self.assertTrue(result.startswith("❌"), result)
        self.assertIn("döngü durduruldu", result)
        surfaced = [
            part["functionResponse"]["response"]["result"]
            for call in post.call_args_list
            for message in call.kwargs["json"]["contents"]
            for part in message.get("parts", [])
            if "functionResponse" in part
        ]
        self.assertTrue(any("sonuç değişmedi" in text for text in surfaced), surfaced)

    def test_identical_calls_are_allowed_while_results_keep_changing(self):
        ai = make_ai(model="gemini-3.8-flash")
        steps = [0]

        def handler(name, args):
            steps[0] += 1
            return f"✅ Adım {steps[0]} tamamlandı"

        responses = [tool_call("mouse_control", {"action": "click", "x": 10, "y": 20})] * 8 + [answer("Bitti.")]
        with patch("app.core.ai.SESSION.post", side_effect=responses):
            result = ai.ask("ekrandaki düğmeye tıkla", tool_handler=handler)
        self.assertEqual(result, "Bitti.")
        self.assertEqual(steps[0], 8)

    def test_send_keys_repeats_are_not_treated_as_a_stagnant_loop(self):
        ai = make_ai(model="gemini-3.8-flash")
        calls = []
        responses = [
            tool_call("send_keys", {"window_title": "Notepad", "keys": "{DOWN}"})
        ] * 8 + [answer("Bitti.")]
        with patch("app.core.ai.SESSION.post", side_effect=responses):
            result = ai.ask(
                "not defterinde aşağı in ve değişikliği gönder",
                tool_handler=lambda name, args: calls.append(name) or "✅ Tuşlar gönderildi",
            )
        self.assertEqual(result, "Bitti.")
        self.assertEqual(len(calls), 8)


class OllamaTests(unittest.TestCase):
    def test_local_chat_needs_no_api_key(self):
        ai = make_ai(api_key="")
        ai.settings.provider = "ollama"
        response = FakeResponse(200, {"message": {"role": "assistant", "content": "Yerel cevap"}})
        with patch("app.core.ai.SESSION.post", return_value=response) as post:
            result = ai.ask("merhaba")
        self.assertEqual(result, "Yerel cevap")
        self.assertTrue(post.call_args.args[0].endswith("/api/chat"))
        self.assertEqual(post.call_args.kwargs["json"]["model"], "qwen2.5-coder:3b")

    def test_local_tool_call_returns_result_to_model(self):
        ai = make_ai(api_key="")
        ai.settings.provider = "ollama"
        first = FakeResponse(200, {"message": {
            "role": "assistant", "content": "",
            "tool_calls": [{"function": {"name": "create_folder", "arguments": {"path": "yerel-test"}}}],
        }})
        second = FakeResponse(200, {"message": {"role": "assistant", "content": "Klasör oluşturuldu."}})
        calls = []
        with patch("app.core.ai.SESSION.post", side_effect=[first, second]) as post:
            result = ai.ask("yerel-test klasörü oluştur", tool_handler=lambda name, args: calls.append(name) or "✅ Oluşturuldu")
        self.assertEqual(result, "Klasör oluşturuldu.")
        self.assertEqual(calls, ["create_folder"])
        first_body = post.call_args_list[0].kwargs["json"]
        self.assertIn("tools", first_body)
        self.assertEqual(first_body["tools"][0]["function"]["parameters"]["type"], "object")
        second_messages = post.call_args_list[1].kwargs["json"]["messages"]
        self.assertEqual(second_messages[-1]["role"], "tool")
        self.assertEqual(second_messages[-1]["content"], "✅ Oluşturuldu")

    def test_connection_error_is_clear(self):
        ai = make_ai(api_key="")
        ai.settings.provider = "ollama"
        with patch("app.core.ai.SESSION.post", side_effect=requests.exceptions.ConnectionError("x")):
            result = ai.ask("merhaba")
        self.assertIn("Ollama'ya bağlanılamadı", result)

    def test_local_failed_tool_triggers_thinking_turn(self):
        ai = make_ai(api_key="")
        ai.settings.provider = "ollama"
        responses = [
            ollama_tool_call("open_application", {"application": "OBS Studio"}),
            FakeResponse(200, {"message": {"role": "assistant", "content": "OBS Studio bulunamadı."}}),
            ollama_tool_call("open_application", {"application": "obs64"}),
            FakeResponse(200, {"message": {"role": "assistant", "content": "OBS Studio açıldı."}}),
        ]

        def handler(name, args):
            if args.get("application") == "OBS Studio":
                return "❌ eşleşen kısayol bulunamadı"
            return "✅ Uygulama başlatıldı: obs64"

        with patch("app.core.ai.SESSION.post", side_effect=responses) as post:
            result = ai.ask("obs studio aç", tool_handler=handler)
        self.assertEqual(result, "OBS Studio açıldı.")
        third_messages = post.call_args_list[2].kwargs["json"]["messages"]
        contents = [str(message.get("content", "")) for message in third_messages]
        self.assertTrue(any("DÜŞÜNME TURU" in content for content in contents), contents)

    def test_local_approval_mode_stops_stagnant_repeats(self):
        ai = make_ai(api_key="", agent_loop_limit=5)
        ai.settings.provider = "ollama"
        calls = []
        responses = [ollama_tool_call("mouse_control", {"action": "click", "x": 5, "y": 5})] * 7
        with patch("app.core.ai.SESSION.post", side_effect=responses):
            result = ai.ask(
                "ekrana tıkla",
                tool_handler=lambda name, args: calls.append(name) or "✅ Tıklandı",
            )
        self.assertEqual(len(calls), 6)
        self.assertIn("döngü durduruldu", result)

    def test_local_full_access_repeats_are_not_blocked(self):
        ai = make_ai(api_key="", access_mode="full")
        ai.settings.provider = "ollama"
        calls = []
        responses = [ollama_tool_call("mouse_control", {"action": "click", "x": 5, "y": 5})] * 8
        responses.append(FakeResponse(200, {"message": {"role": "assistant", "content": "Tamam."}}))
        with patch("app.core.ai.SESSION.post", side_effect=responses):
            result = ai.ask(
                "ekrana tıkla",
                tool_handler=lambda name, args: calls.append(name) or "✅ Tıklandı",
            )
        self.assertEqual(result, "Tamam.")
        self.assertEqual(len(calls), 8)


class TokenHarborTests(unittest.TestCase):
    def make(self, **kwargs):
        ai = make_ai(api_key="", **kwargs)
        ai.settings.provider = "tokenharbor"
        ai.settings.tokenharbor_api_key = "th-test-key"
        ai.settings.tokenharbor_model = "vendor/model:free"
        ai.settings.tokenharbor_url = "https://tokenharbor.ai/v1"
        return ai

    def test_chat_uses_openai_format_and_bearer_key(self):
        ai = self.make()
        with patch("app.core.ai.SESSION.post", return_value=openai_answer("Selam!")) as post:
            result = ai.ask("merhaba")
        self.assertEqual(result, "Selam!")
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://tokenharbor.ai/v1/chat/completions")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer th-test-key")
        self.assertEqual(kwargs["json"]["model"], "vendor/model:free")
        self.assertEqual(kwargs["json"]["messages"][-1]["role"], "user")

    def test_model_is_auto_selected_from_free_models(self):
        ai = self.make()
        ai.settings.tokenharbor_model = ""
        models = FakeResponse(200, {"data": [{"id": "paid/model"}, {"id": "free/model:free"}]})
        with patch("app.core.ai.SESSION.get", return_value=models) as get:
            with patch("app.core.ai.SESSION.post", return_value=openai_answer("Tamam")) as post:
                result = ai.ask("merhaba")
        self.assertEqual(result, "Tamam")
        self.assertTrue(get.call_args.args[0].endswith("/models"))
        self.assertEqual(post.call_args.kwargs["json"]["model"], "free/model:free")

    def test_tool_call_round_trip_uses_tool_call_id(self):
        ai = self.make()
        calls = []
        responses = [
            openai_tool_call("create_folder", {"path": "th-test"}),
            openai_answer("Klasör oluşturuldu."),
        ]

        def handler(name, args):
            calls.append((name, args))
            return "✅ Oluşturuldu"

        with patch("app.core.ai.SESSION.post", side_effect=responses) as post:
            result = ai.ask("th-test klasörü oluştur", tool_handler=handler)
        self.assertEqual(result, "Klasör oluşturuldu.")
        self.assertEqual(calls, [("create_folder", {"path": "th-test"})])
        self.assertIn("tools", post.call_args_list[0].kwargs["json"])
        second_messages = post.call_args_list[1].kwargs["json"]["messages"]
        tool_message = [m for m in second_messages if m.get("role") == "tool"][-1]
        self.assertEqual(tool_message["tool_call_id"], "call_1")

    def test_missing_key_asks_for_settings(self):
        ai = self.make()
        ai.settings.tokenharbor_api_key = ""
        self.assertIn("Token Harbor API anahtarı", ai.ask("merhaba"))

    def test_invalid_key_message(self):
        ai = self.make()
        with patch("app.core.ai.SESSION.post", return_value=FakeResponse(401, {"error": "bad key"})):
            result = ai.ask("merhaba")
        self.assertIn("anahtar", result)

    def test_rate_limit_message(self):
        ai = self.make()
        with patch("app.core.ai.SESSION.post", return_value=FakeResponse(429, {})):
            result = ai.ask("merhaba")
        self.assertIn("sınır", result)


class OverloadTests(unittest.TestCase):
    """Ekran görüntüsündeki 503 'model yoğun' hatası."""

    OVERLOADED = {"error": {"message": "This model is currently experiencing high demand."}}

    def test_temporary_503_is_retried_on_same_model(self):
        ai = make_ai(model="gemini-3.8-flash")
        with patch("app.core.ai.SESSION.post", side_effect=[FakeResponse(503, self.OVERLOADED), answer("Geçti")]) as post, \
             patch.object(AI, "_wait") as wait:
            self.assertEqual(ai.ask("merhaba"), "Geçti")
        self.assertEqual(post.call_count, 2)
        self.assertTrue(all(c.args[0].endswith("/models/gemini-3.8-flash:generateContent") for c in post.call_args_list))
        wait.assert_called_once()
        self.assertEqual(ai.settings.model, "gemini-3.8-flash")

    def test_persistent_503_falls_back_to_other_model_without_changing_setting(self):
        ai = make_ai(model="gemini-3.8-flash")
        busy = FakeResponse(503, self.OVERLOADED)
        with patch("app.core.ai.SESSION.post", side_effect=[busy, busy, busy, answer("Yedek model")]) as post, \
             patch("app.core.ai.SESSION.get", return_value=model_list("gemini-3.8-flash", "gemini-3.7-flash")), \
             patch.object(AI, "_wait"):
            self.assertEqual(ai.ask("merhaba"), "Yedek model")
        self.assertTrue(post.call_args_list[3].args[0].endswith("/models/gemini-3.7-flash:generateContent"))
        self.assertEqual(ai.settings.model, "gemini-3.8-flash")  # geçici sorun, ayar değişmez

    def test_everything_busy_gives_clear_message_with_tried_models(self):
        ai = make_ai(model="gemini-3.8-flash")
        busy = FakeResponse(503, self.OVERLOADED)
        with patch("app.core.ai.SESSION.post", return_value=busy), \
             patch("app.core.ai.SESSION.get", return_value=model_list("gemini-3.8-flash", "gemini-3.7-flash")), \
             patch.object(AI, "_wait"):
            result = ai.ask("merhaba")
        self.assertTrue(result.startswith("❌ Gemini API hatası (503)"))
        self.assertIn("çok yoğun", result)
        self.assertIn("gemini-3.8-flash, gemini-3.7-flash", result)

    def test_quota_429_also_tries_another_model(self):
        ai = make_ai(model="gemini-3.8-flash")
        quota = FakeResponse(429, {"error": {"message": "quota"}})
        with patch("app.core.ai.SESSION.post", side_effect=[quota, answer("Başka model")]), \
             patch("app.core.ai.SESSION.get", return_value=model_list("gemini-3.8-flash", "gemini-3.5-flash-lite")):
            self.assertEqual(ai.ask("merhaba"), "Başka model")

    def test_cancel_stops_retrying(self):
        ai = make_ai(model="gemini-3.8-flash")
        def cancel_and_wait(seconds):
            ai.cancel()
        with patch("app.core.ai.SESSION.post", return_value=FakeResponse(503, self.OVERLOADED)) as post, \
             patch.object(AI, "_wait", side_effect=cancel_and_wait):
            self.assertEqual(ai.ask("merhaba"), "")
        self.assertEqual(post.call_count, 1)


class QuotaTests(unittest.TestCase):
    DAILY = {"error": {"message": (
        "You exceeded your current quota. Quota exceeded for metric: "
        "generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 20, "
        "model: gemini-3.8-flash, description: Generate content requests per day limit exceeded "
        "on Free Tier. Please retry in 5h55m14.741232261s."
    )}}

    def setUp(self):
        AI.reset_quota()
        self.addCleanup(AI.reset_quota)

    def test_daily_quota_returns_short_message_and_stops_network_calls(self):
        ai = make_ai(model="gemini-3.8-flash")
        quota = FakeResponse(429, self.DAILY)
        with patch("app.core.ai.SESSION.post", return_value=quota) as post, \
             patch("app.core.ai.SESSION.get", return_value=model_list("gemini-3.8-flash")):
            first = ai.ask("merhaba")
            second = ai.ask("tekrar dene")
        self.assertIn("günlük ücretsiz kotası doldu", first)
        self.assertIn("5 sa 55 dk", first)
        self.assertNotIn("Denenen modeller", first)
        self.assertEqual(first, second)
        self.assertEqual(post.call_count, 1)
        self.assertTrue(ai.quota_blocked)

    def test_retry_delay_is_parsed(self):
        self.assertEqual(AI._retry_seconds("Please retry in 5h55m14.741232261s."), 5 * 3600 + 55 * 60 + 14)
        self.assertEqual(AI._retry_seconds("retry in 30s"), 30)
        self.assertEqual(AI._retry_seconds("no wait"), 0)

    def test_per_minute_429_does_not_trigger_the_breaker(self):
        ai = make_ai(model="gemini-3.8-flash")
        quota = FakeResponse(429, {"error": {"message": "quota exceeded per minute"}})
        with patch("app.core.ai.SESSION.post", return_value=quota), \
             patch("app.core.ai.SESSION.get", return_value=model_list("gemini-3.8-flash")):
            ai.ask("merhaba")
        self.assertFalse(ai.quota_blocked)


class GreetingTests(unittest.TestCase):
    def setUp(self):
        AI.reset_quota()
        self.addCleanup(AI.reset_quota)

    def _block(self):
        AI._quota_until = time.monotonic() + 600
        AI._quota_message = "⏳ kota"
        AI._quota_models = ("gemini-1.5-flash",)
        AI._quota_key = "AQ.test-key-123"

    def test_greeting_still_answers_while_quota_blocked(self):
        self._block()
        ai = make_ai()
        with patch("app.core.ai.SESSION.post") as post:
            self.assertIn("İyiyim", ai.ask("Jarvis nasılsın"))
            self.assertIn("Merhaba", ai.ask("hey jarvis merhaba"))
        post.assert_not_called()
        self.assertEqual(ai.storage.messages[-2][0], "user")
        self.assertEqual(ai.storage.messages[-1][0], "assistant")

    def test_real_question_gets_quota_notice_not_a_canned_reply(self):
        self._block()
        ai = make_ai()
        with patch("app.core.ai.SESSION.post") as post:
            result = ai.ask("nasılsın diye soran arkadaşıma ne cevap vereyim")
        post.assert_not_called()
        self.assertIn("kota", result)


class StreamingTests(unittest.TestCase):
    """Cevabın kelime kelime, üretildikçe gelmesi (yavaşlık düzeltmesi)."""

    def test_real_streaming_delivers_chunks_as_they_arrive(self):
        ai = make_ai(model="gemini-3.8-flash")
        chunks = []
        stream = FakeStream(sse("Mer", "haba", " dünya"))
        with patch("app.core.ai.SESSION.post", return_value=stream) as post:
            result = ai.ask("merhaba", stream=True, on_chunk=chunks.append)
        self.assertEqual(chunks, ["Mer", "haba", " dünya"])
        self.assertEqual(result, "Merhaba dünya")
        self.assertIn(":streamGenerateContent?alt=sse", post.call_args.args[0])
        self.assertTrue(post.call_args.kwargs["stream"])
        self.assertTrue(stream.closed)
        self.assertEqual(ai.storage.messages[-1], ("assistant", "Merhaba dünya"))

    def test_thinking_level_low_for_gemini3_but_not_older_models(self):
        expected = {
            "gemini-3.8-flash": "low", "gemini-3.7-flash": "low", "gemini-3.1-pro": "low",
            "gemini-3.6-flash": "minimal", "gemini-3.5-flash": "minimal", "gemini-3-flash-preview": "minimal",
            "gemini-3.5-flash-lite": None, "gemini-3.1-flash-lite": None, "gemini-2.5-flash": None,
        }
        for model, level in expected.items():
            ai = make_ai(model=model)
            with patch("app.core.ai.SESSION.post", return_value=answer()) as post:
                ai.ask("merhaba")
            config = post.call_args.kwargs["json"]["generationConfig"]
            want = {"thinkingLevel": level} if level else None
            self.assertEqual(config.get("thinkingConfig"), want, model)

    def test_model_rejecting_thinking_level_is_retried_without_it(self):
        ai = make_ai(model="gemini-3.8-flash")
        reject = FakeResponse(400, {"error": {"message": "Thinking level LOW is not supported for this model."}})
        with patch("app.core.ai.SESSION.post", side_effect=[reject, answer("Tamam")]) as post:
            self.assertEqual(ai.ask("merhaba"), "Tamam")
        self.assertIn("thinkingConfig", post.call_args_list[0].kwargs["json"]["generationConfig"])
        self.assertNotIn("thinkingConfig", post.call_args_list[1].kwargs["json"]["generationConfig"])

    def test_stream_503_is_retried_then_streams(self):
        ai = make_ai(model="gemini-3.8-flash")
        chunks = []
        busy = FakeResponse(503, {"error": {"message": "high demand"}})
        with patch("app.core.ai.SESSION.post", side_effect=[busy, FakeStream(sse("Geç", "ti"))]), \
             patch.object(AI, "_wait"):
            result = ai.ask("merhaba", stream=True, on_chunk=chunks.append)
        self.assertEqual((result, chunks), ("Geçti", ["Geç", "ti"]))

    def test_stream_hides_thoughts_and_reports_blocked_prompt(self):
        ai = make_ai(model="gemini-3.8-flash")
        events = [{"candidates": [{"content": {"parts": [{"text": "düşünce", "thought": True}, {"text": "Cevap"}]}}]}]
        chunks = []
        with patch("app.core.ai.SESSION.post", return_value=FakeStream(events)):
            self.assertEqual(ai.ask("a", stream=True, on_chunk=chunks.append), "Cevap")
        self.assertEqual(chunks, ["Cevap"])
        blocked = FakeStream([{"promptFeedback": {"blockReason": "SAFETY"}}])
        with patch("app.core.ai.SESSION.post", return_value=blocked):
            result = ai.ask("b", stream=True, on_chunk=chunks.append)
        self.assertIn("SAFETY", result)
        self.assertNotIn(("assistant", result), ai.storage.messages)

    def test_stale_pooled_connection_is_retried_once_on_fresh_connection(self):
        ai = make_ai(model="gemini-3.8-flash")
        with patch("app.core.ai.SESSION.post", side_effect=[requests.exceptions.ConnectionError("aborted"), answer("Tamam")]) as post:
            self.assertEqual(ai.ask("merhaba"), "Tamam")
        self.assertEqual(post.call_count, 2)

    def test_connect_timeout_is_not_doubled(self):
        ai = make_ai(model="gemini-3.8-flash")
        with patch("app.core.ai.SESSION.post", side_effect=requests.exceptions.ConnectTimeout("t")) as post:
            ai.ask("merhaba")
        self.assertEqual(post.call_count, 1)

    def test_read_timeout_is_retried_once_without_losing_progress(self):
        ai = make_ai(model="gemini-3.8-flash")
        with patch(
            "app.core.ai.SESSION.post",
            side_effect=[requests.exceptions.ReadTimeout("slow"), answer("Tamam")],
        ) as post:
            self.assertEqual(ai.ask("merhaba"), "Tamam")
        self.assertEqual(post.call_count, 2)

    def test_old_screenshots_are_trimmed_so_payload_stays_small(self):
        import tempfile
        from pathlib import Path

        ai = make_ai(model="gemini-3.8-flash")
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index in range(4):
                path = Path(directory) / f"shot{index}.jpg"
                path.write_bytes(b"fake-jpeg" + bytes([index]))
                paths.append(path)
            step = [0]

            def handler(name, args):
                ai.add_screenshot(str(paths[step[0]]))
                ai.add_screenshot(str(paths[step[0] + 1]))
                step[0] += 2
                return "✅ Görüntü alındı"

            with patch("app.core.ai.SESSION.post", side_effect=[
                tool_call("capture_screen", {}),
                tool_call("capture_screen", {}),
                answer("Tamam"),
            ]) as post:
                self.assertEqual(ai.ask("ekranı yakala", tool_handler=handler), "Tamam")
            contents = post.call_args_list[2].kwargs["json"]["contents"]
            images = [part for message in contents for part in message.get("parts", []) if "inline_data" in part]
            self.assertEqual(len(images), 2)

    def test_timeout_after_a_tool_call_keeps_the_verified_result(self):
        ai = make_ai(model="gemini-3.8-flash")
        with patch("app.core.ai.SESSION.post", side_effect=[
            tool_call("save_file_dialog", {"path": "Jarvis_Evi.png"}),
            requests.exceptions.ReadTimeout("slow"),
            requests.exceptions.ReadTimeout("slow"),
        ]):
            result = ai.ask(
                "Jarvis_Evi.png olarak kaydet",
                tool_handler=lambda name, args: "✅ Dosya kaydedildi: C:\\Users\\cem134\\Desktop\\Jarvis_Evi.png",
            )
        self.assertTrue(result.startswith("⚠️"), result)
        self.assertIn("save_file_dialog", result)
        self.assertIn("Jarvis_Evi.png", result)

    def test_cancel_mid_stream_stops_and_closes(self):
        ai = make_ai(model="gemini-3.8-flash")
        stream = FakeStream(sse("bir", "iki", "üç"))
        chunks = []
        def on_chunk(text):
            chunks.append(text)
            ai.cancel()
        with patch("app.core.ai.SESSION.post", return_value=stream):
            self.assertEqual(ai.ask("merhaba", stream=True, on_chunk=on_chunk), "")
        self.assertEqual(chunks, ["bir"])
        self.assertTrue(stream.closed)


if __name__ == "__main__":
    unittest.main()
