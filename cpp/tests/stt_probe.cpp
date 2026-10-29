// C++ STT hattının canlı ucu: saf istek yükleyici ve ayrıştırıcı gerçek uç noktaya
// gönderilir. Bu bir birim test değildir (transkript doğrulaması mikrofon ister);
// yalnızca durum kodu ve çözümleme türü yazdırılır. API anahtarı hiçbir satırda
// görünmez.
#include <cmath>
#include <cstdio>
#include <string>

#include <jarvis/core/settings.hpp>
#include <jarvis/net/http.hpp>
#include <jarvis/speech/stt.hpp>

namespace {

constexpr int kRate = 16000;

// Bir saniyelik 16 kHz mono ton: konuşma değil, biçim/geçerlilik denemesi.
std::string tone_pcm() {
    std::string pcm;
    pcm.reserve(static_cast<std::size_t>(kRate) * 2);
    for (int i = 0; i < kRate; ++i) {
        const double value = 12000.0 * std::sin(2.0 * 3.14159265358979 * 220.0 *
                                                static_cast<double>(i) / kRate);
        const int sample = static_cast<int>(value);
        pcm.push_back(static_cast<char>(sample & 0xFF));
        pcm.push_back(static_cast<char>((sample >> 8) & 0xFF));
    }
    return pcm;
}

const char* kind_name(jarvis::speech::SttOutcome::Kind kind) {
    switch (kind) {
    case jarvis::speech::SttOutcome::Kind::Text:
        return "metin";
    case jarvis::speech::SttOutcome::Kind::RequestFailed:
        return "istek-hatası";
    case jarvis::speech::SttOutcome::Kind::NotUnderstood:
        return "anlaşılamadı";
    }
    return "?";
}

bool attempt(const jarvis::core::Settings& settings, const std::string& pcm,
             jarvis::speech::SttFormat format) {
    const jarvis::speech::SttRequest request = jarvis::speech::build_stt_request(
        settings.stt_api_key(), pcm, kRate, format);

    jarvis::net::HttpClient client("http://www.google.com");
    client.set_timeout_ms(8000);
    client.set_allow_error_status(true);
    const jarvis::net::HttpResult result =
        client.post_bytes(request.url, request.body, request.content_type);
    if (!result.has_value()) {
        std::printf(" Durum: ağ hatası (%s)\n", result.error().message.c_str());
        return false;
    }
    const jarvis::speech::SttOutcome outcome =
        jarvis::speech::parse_stt_response(result.value().text);
    std::printf(" Durum HTTP %ld, gövde %zu bayt, başlık %s\n", result.value().status_code,
                request.body.size(), request.content_type.c_str());
    std::printf(" Çözümleme: %s | uç yanıtı: %.80s\n", kind_name(outcome.kind),
                result.value().text.c_str());
    if (result.value().status_code >= 400) {
        std::printf(" BAŞARISIZ: uç nokta bu gövde biçimini reddetti.\n");
        return false;
    }
    return true;
}

}  // namespace

int main() {
    const jarvis::core::Settings settings;
    const std::string pcm = tone_pcm();
    std::printf("STT hattı canlı denemesi (ton, %zu bayt PCM)\n", pcm.size());

    bool ok = attempt(settings, pcm, jarvis::speech::SttFormat::L16Pcm);
    if (!ok) {
        std::printf("l16/PCM reddedildi; FLAC deneniyor.\n");
        ok = attempt(settings, pcm, jarvis::speech::SttFormat::Flac);
    }
    std::printf(ok ? "STT denemesi: BAŞARILI\n" : "STT denemesi: BAŞARISIZ\n");
    return ok ? 0 : 1;
}
