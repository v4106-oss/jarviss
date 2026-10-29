#pragma once

// Seslendirme metninin hazırlanması: Python app/core/speech.py'nin salt mantık kısmı.
// Qt/OS gerektirmez; cümleleme, kısaltma ve tempo kararları burada test edilir.

#include <optional>
#include <string>
#include <vector>

namespace jarvis::speech {

inline constexpr int kSynthAttempts = 3;
inline constexpr int kMaxSpeechChars = 3000;
inline constexpr int kMaxChunkChars = 220;
inline constexpr int kFirstChunkChars = 90;
inline constexpr int kVoiceReplyChars = 110;
inline constexpr int kDefaultVoiceRate = -3;

struct VoiceStyle {
    std::string style;
    std::string voice;   // edge-tts sinir sesi adı
    std::string pitch;   // "+0Hz" / "-10Hz"
    std::string label;   // arayüzde görünen ad
};

// Bilinmeyen stil varsayılana ("jarvis") döner.
const VoiceStyle& style_parts(const std::string& style);
std::string style_label(const std::string& style);
int style_rate_offset(const std::string& style);

// Kullanıcının kayıtlı temposu + karakterin kendi ofseti: "-3%" biçiminde metin.
std::string rate_text(int voice_rate, const std::string& style);

// Markdown/kod/bağlantı ayıklar; yalnızca seslendirilebilir düz metin bırakır.
std::string clean_for_speech(const std::string& text);

// Yanıtı cümle cümle parçalara böler (her biri en fazla max_chars).
std::vector<std::string> split_for_speech(const std::string& text,
                                          int max_chars = kMaxChunkChars);

// İlk parça kısa tutulur ki ses mümkün olan en erken anda başlasın.
std::vector<std::string> plan_chunks(const std::string& text,
                                     int first_chars = kFirstChunkChars,
                                     int max_chars = kMaxChunkChars);

// Sesli sohbette yalnızca giriş cümleleri söylenir; ayrıntı yazılı yanıtta kalır.
std::string concise_for_voice(const std::string& text, int max_chars = kVoiceReplyChars);

// Açık "okuhaber/anlat" isteklerinde sınır yok (nullopt), yoksa kısa yanıt limiti.
std::optional<int> spoken_limit_for_prompt(const std::string& prompt,
                                           int concise = kVoiceReplyChars);

// Türkçe metni aksansız küçük harfe indirger (speech.py'deki _fold).
std::string fold_tr(const std::string& text);

}  // namespace jarvis::speech
