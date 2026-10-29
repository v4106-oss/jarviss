#pragma once

// Nöral ses sentezinin OS tarafı: önbellek anahtarı, edge-tts çağrısı ve SAPI yedeği.
// Python app/core/speech.py'deki synthesize/_sapi_script karşılığı; Qt kullanmaz.

#include <filesystem>
#include <string>
#include <vector>

namespace jarvis::speech {

inline constexpr int kSynthTimeoutMs = 10000;
inline constexpr int kCacheKeepFiles = 40;

// %LOCALAPPDATA%\Jarvis\cache\tts (yoksa kullanıcı evi altında).
std::filesystem::path cache_dir();

// voice|rate|pitch|text girdisinin ilk 24 hex karakterlik SHA-256 özeti.
std::string cache_key(const std::string& voice, const std::string& rate,
                      const std::string& pitch, const std::string& text);

std::filesystem::path cached_path(const std::string& key);

// Metni MP3 olarak üretir; önbellekte hazır dosya varsa ağa çıkmaz.
// Başarısız olursa std::runtime_error atar (çağıran SAPI yedeğine düşer).
std::filesystem::path synthesize(const std::string& text, const std::string& voice,
                                 const std::string& rate, const std::string& pitch);

// edge-tts'i çalıştıran Python yorumlayıcısı. Python sürümü edge_tts'i kendi
// yorumlayıcısında import ediyordu; burada ayrı süreç açıldığı için modülü
// gerçekten barındıran yorumlayıcı seçilir: JARVIS_TTS_PYTHON, sonra proje
// .venv'i, en son "python".
std::string tts_python();

// edge-tts süreç komutu: [python, -m, edge_tts, --text=..., --voice=...,
// --rate=..., --pitch=..., --write-media=...]. Değerler bayrakla birleşik
// verilir; "--pitch -10Hz" biçimini argparse reddediyor.
std::vector<std::wstring> edge_tts_command(const std::string& text, const std::string& voice,
                                           const std::string& rate, const std::string& pitch,
                                           const std::filesystem::path& out);

// En yeni kCacheKeepFiles dosya dışında önbelleği temizler.
void trim_cache(int keep = kCacheKeepFiles);

// PowerShell + System.Speech betiği (Türkçe ses varsa o seçilir).
std::string sapi_script(const std::string& text, int volume);

// Betiği -EncodedCommand için hazır PowerShell komut satırına çevirir.
std::vector<std::wstring> sapi_command(const std::string& text, int volume);

// Aynı komutu CreateProcessW'nun beklediği tek satıra birleştirir (tırnaklanmış).
std::wstring sapi_command_line(const std::string& text, int volume);

std::string to_base64(const std::string& bytes);

}  // namespace jarvis::speech
