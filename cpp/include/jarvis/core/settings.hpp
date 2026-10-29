#pragma once

#include <filesystem>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include <nlohmann/json.hpp>

namespace jarvis::core {

inline constexpr std::string_view kDefaultModel = "gemini-3.8-flash";
inline constexpr std::string_view kDefaultOllamaModel = "qwen2.5-coder:3b";
inline constexpr std::string_view kDefaultOllamaUrl = "http://localhost:11434";
inline constexpr std::string_view kDefaultTokenHarborUrl = "https://tokenharbor.ai/v1";
inline constexpr std::string_view kDefaultVoiceStyle = "jarvis";

// Ev konumu; Python'daki _DEFAULT_HOME karşılığı.
struct HomeLocation {
    std::string name{"Derince"};
    std::string region{"Kocaeli"};
    double lat{40.757};
    double lon{29.815};
};

// Yerel Jarvis ayarları; API anahtarı proje klasörüne yazılmaz.
// Python'daki app/core/settings.py Settings sınıfının birebir karşılığı.
class Settings {
public:
    Settings();
    explicit Settings(std::filesystem::path file_path);

    // --- model / sağlayıcı ---
    std::string model() const;
    std::string api_key() const;
    std::string provider() const;
    std::string provider_label() const;
    std::string active_model() const;
    std::string ollama_url() const;
    std::string ollama_model() const;
    std::string tokenharbor_url() const;
    std::string tokenharbor_model() const;
    std::string tokenharbor_api_key() const;

    std::filesystem::path data_dir() const;

    // --- tema ---
    std::string theme() const;
    void set_theme(std::string name);

    // --- erişim modu ---
    std::string access_mode() const;
    bool strict_after_web() const;
    void set_access(std::string mode, bool strict_after_web = true);

    // --- ekran bağlamı ---
    bool screen_context() const;
    void set_screen_context(bool enabled);

    // --- sesli yanıt ---
    bool voice_replies() const;
    void set_voice_replies(bool enabled);

    // --- eşlikçi ---
    bool companion_enabled() const;
    int companion_interval() const;
    std::string companion_quiet_start() const;
    std::string companion_quiet_end() const;
    bool companion_hydration() const;
    bool companion_speak() const;
    void set_companion(std::optional<bool> enabled = std::nullopt,
                       std::optional<int> interval = std::nullopt,
                       std::optional<std::string> quiet_start = std::nullopt,
                       std::optional<std::string> quiet_end = std::nullopt,
                       std::optional<bool> hydration = std::nullopt,
                       std::optional<bool> speak = std::nullopt);

    // --- ses / kamera ---
    std::string mic_device() const;
    // Konuşma tanıma anahtarı; boşsa kütüphanenin gömülü anahtarı kullanılır.
    std::string stt_api_key() const;
    std::string speaker_device() const;
    int camera_index() const;
    std::string voice_style() const;
    int voice_rate() const;
    int voice_volume() const;
    bool voice_headphones() const;
    void set_audio(std::string mic_device = "",
                   std::string speaker_device = "",
                   int camera_index = 0,
                   std::string voice_style = "jarvis",
                   int voice_rate = -3,
                   int voice_volume = 90,
                   bool headphones = true);

    // --- ajan sınırları ---
    int agent_step_limit() const;
    int agent_seconds_limit() const;
    int agent_loop_limit() const;
    void set_agent_limits(int step = 0, int seconds = 0, int loop = 0);

    // --- ek kök dizinler ---
    std::vector<std::string> extra_roots() const;
    void add_root(std::string path);
    void remove_root(std::string path);
    void set_roots(const std::vector<std::string>& roots);

    // --- yüzen HUD konumu ---
    std::optional<std::pair<int, int>> floating_hud_position() const;
    void save_floating_hud_position(int x, int y);
    void clear_floating_hud_position();

    // --- ev konumu ---
    bool home_is_set() const;
    HomeLocation home_location() const;
    HomeLocation set_home(double lat,
                          double lon,
                          std::string name = "Evim",
                          std::string region = "Kocaeli");

    // --- kayıt ---
    void save(std::string api_key,
              std::string model,
              std::string provider = "gemini",
              std::string ollama_url = std::string(kDefaultOllamaUrl),
              std::string ollama_model = std::string(kDefaultOllamaModel),
              std::string tokenharbor_api_key = "",
              std::string tokenharbor_model = "",
              std::string tokenharbor_url = std::string(kDefaultTokenHarborUrl));
    void set_model(std::string model);

private:
    std::filesystem::path path_;
    nlohmann::json data_;

    void load();
    void write() const;
    int limit(std::string_view key) const;
};

}  // namespace jarvis::core
