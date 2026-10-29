#include "jarvis/speech/stt.hpp"

#include "jarvis/core/util.hpp"
#include "jarvis/speech/voice.hpp"

#include <cctype>
#include <cstdio>
#include <sstream>
#include <vector>

namespace jarvis::speech {
namespace {

// speech_recognition kütüphanesinin gömülü genel anahtarı; kişisel/test kullanımına
// açık olduğundan ayar yoksa bu kullanılır. Dışarı asla yazdırılmaz.
constexpr const char* kEmbeddedKey = "AIzaSyBOti4mM-6x9WDnZIjIeyEU21OpBXqWBgw";

// urllib.parse.quote_plus: korunanlar harfler, rakamlar ve "_.-~"; boşluk '+'.
std::string url_encode(const std::string& value) {
    std::string out;
    out.reserve(value.size());
    for (unsigned char c : value) {
        const bool safe = std::isalnum(c) != 0 || c == '_' || c == '.' || c == '-' || c == '~';
        if (safe) {
            out.push_back(static_cast<char>(c));
        } else if (c == ' ') {
            out.push_back('+');
        } else {
            char buffer[4];
            std::snprintf(buffer, sizeof(buffer), "%%%02X", c);
            out.append(buffer);
        }
    }
    return out;
}

}  // namespace

std::string resolve_stt_key(const std::string& from_settings) {
    const std::string trimmed = util::trim(from_settings);
    return trimmed.empty() ? std::string(kEmbeddedKey) : trimmed;
}

std::string stt_url(const std::string& api_key, const std::string& language) {
    std::string query;
    query += "client=chromium";
    query += "&lang=" + url_encode(language);
    query += "&key=" + url_encode(api_key);
    query += "&pFilter=0";
    return std::string(kSttEndpoint) + "?" + query;
}

SttRequest build_stt_request(const std::string& api_key, const std::string& pcm16_mono,
                             int sample_rate, SttFormat format, const std::string& language) {
    SttRequest request;
    request.url = stt_url(resolve_stt_key(api_key), language);
    if (format == SttFormat::Flac) {
        request.content_type = "audio/x-flac; rate=" + std::to_string(sample_rate);
    } else {
        request.content_type = "audio/l16; rate=" + std::to_string(sample_rate) + "; channels=1";
    }
    request.body = pcm16_mono;
    return request;
}

std::optional<nlohmann::json> first_result(const std::string& response_text) {
    std::istringstream stream(response_text);
    std::string line;
    // Yanıt NDJSON: satırlar "\n" ile ayrılır; boş bloklar atlanır.
    while (std::getline(stream, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty()) continue;
        nlohmann::json parsed;
        try {
            parsed = nlohmann::json::parse(line);
        } catch (const nlohmann::json::exception&) {
            return std::nullopt;
        }
        if (!parsed.is_object() || !parsed.contains("result") || !parsed.at("result").is_array()) {
            return std::nullopt;
        }
        const nlohmann::json& results = parsed.at("result");
        if (results.empty()) continue;
        if (!results.front().is_object()) return std::nullopt;
        const auto it = results.front().find("alternative");
        if (it == results.front().end() || !it->is_array() || it->empty()) {
            // Python burada hemen UnknownValueError fırlatır; sonraki satıra geçmez.
            return std::nullopt;
        }
        return results.front();
    }
    return std::nullopt;
}

SttOutcome parse_stt_response(const std::string& response_text) {
    SttOutcome outcome;
    const std::optional<nlohmann::json> result = first_result(response_text);
    if (!result.has_value()) return outcome;
    // Python sırası: _normalize_wake(_pick_transcript(payload))
    const std::string text = normalize_wake(pick_transcript(*result));
    if (text.empty()) return outcome;
    outcome.kind = SttOutcome::Kind::Text;
    outcome.text = text;
    return outcome;
}

}  // namespace jarvis::speech
