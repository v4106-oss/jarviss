#pragma once

// Mikrofon/STT karar mantığı: Python app/core/voice.py'nin salt kısmı.
// Aygıt okuma ve ağ çağrısı burada değil; yalnızca metin/seviye/sıralama kararları.

#include <nlohmann/json.hpp>

#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace jarvis::speech {

inline constexpr double kLevelFloor = 60.0;
inline constexpr double kLevelCeil = 5000.0;

// RMS girişini arayüz göstergesi için 0.0–1.0 aralığına-eğrisel çevirir.
double level_from_rms(double rms);

// Fold edilmiş kelimeler (salt [a-z0-9]+ parçaları).
std::vector<std::string> words_of(const std::string& text);

// Jarvis konuşurken tek kelimelik gürültü/yankı söz kesme sayılmaz.
bool should_accept_barge(const std::string& text);

// "carvis/hejiyan/carwıs" gibi bozuk yazımlarını tek ada (Jarvis) çevirir.
std::string normalize_wake(const std::string& text);

// show_all yanıtından uyanma kelimesini içeren en iyi alternatifi seçer.
std::string pick_transcript(const nlohmann::json& payload);

// PortAudio host API tercih sırası: wasapi < directsound < mme < wdm-ks.
int host_rank(const std::string& host_name);

// Kayıtlı aygıt adını indekse çevirir; boş ad => sistem varsayılanı (nullopt).
std::optional<int> resolve_microphone_index(
    const std::vector<std::pair<int, std::string>>& devices, const std::string& wanted);

// (host önceliği, indeks, ad) sıralamasına göre tekrar eden adları eler.
std::vector<std::pair<int, std::string>> order_microphones(
    const std::vector<std::pair<int, std::string>>& devices,
    const std::vector<std::string>& host_of_each);

}  // namespace jarvis::speech
