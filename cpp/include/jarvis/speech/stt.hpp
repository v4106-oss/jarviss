#pragma once

// Google konuşma tanıma (speech-api/v2) istek kurma ve NDJSON yanıt ayrıştırma.
// Python .venv/Lib/site-packages/speech_recognition/recognizers/google.py karşılığı.
//
// Bu modül ağa çıkmaz ve Qt kullanmaz: yalnızca URL/gövde kurar ve metin yanıtı
// ayrıştırır. Böylece çevirimdışı test edilebilir; HTTP çağrısı arayüz katmanındır.

#include <nlohmann/json.hpp>

#include <optional>
#include <string>

namespace jarvis::speech {

// Uç nokta Python kütüphanesindeki ENDPOINT ile aynı.
inline constexpr const char* kSttEndpoint = "http://www.google.com/speech-api/v2/recognize";

// Konuşma tanılamada kullanıcının göreceği metinler (Python'daki karşılıklarıyla birebir).
inline constexpr const char* kSttNotUnderstood =
    "Söylenenler anlaşılamadı. Daha net konuşup tekrar deneyin.";
inline constexpr const char* kSttRequestFailed =
    "Ses tanıma servisine ulaşılamadı. İnternet bağlantısını kontrol edin.";

// Gövde kodlama biçimi. Uç nokta FLAC ister; ham 16-bit PCM denemesi başarılı
// olursa yeni bağımlılık gerekmez. FLAC'a geçiş yalnızca içerik türünü değiştirir.
enum class SttFormat { L16Pcm, Flac };

struct SttRequest {
    std::string url;
    std::string content_type;
    std::string body;  // ikili (binary) veri; UTF-8 değildir
};

// Ayar anahtarı boşsa kütüphanenin gömülü genel anahtarı kullanılır. Anahtar
// asla loglanmaz ve hata mesajlarına konmaz.
std::string resolve_stt_key(const std::string& from_settings);

// client=chromium&lang=<dil>&key=<anahtar>&pFilter=0 (urlencode/quote_plus biçimi).
std::string stt_url(const std::string& api_key, const std::string& language = "tr-TR");

// PCM16 mono gövdesinden istek üretir; rate 8 kHz altında uç nokta reddeder.
SttRequest build_stt_request(const std::string& api_key, const std::string& pcm16_mono,
                             int sample_rate, SttFormat format = SttFormat::L16Pcm,
                             const std::string& language = "tr-TR");

// NDJSON yanıtını Python convert_to_result gibi çevirir: boş satırlar atlanır,
// ilk dolu result dizisinin 0. öğesi döner. Hiç yoksa nullopt.
std::optional<nlohmann::json> first_result(const std::string& response_text);

// Yanıtı Python sırasıyla metne çevirir: _normalize_wake(_pick_transcript(payload)).
// Anlaşılamayan/boş yanıtlar için kind NotUnderstood, ağ/HTTP hatası için RequestFailed.
struct SttOutcome {
    enum class Kind { Text, NotUnderstood, RequestFailed };

    Kind kind = Kind::NotUnderstood;
    std::string text;  // yalnız Kind::Text için dolu

    bool ok() const { return kind == Kind::Text && !text.empty(); }
};

SttOutcome parse_stt_response(const std::string& response_text);

}  // namespace jarvis::speech
