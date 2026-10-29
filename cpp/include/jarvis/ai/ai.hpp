#pragma once

#include <array>
#include <atomic>
#include <cstdint>
#include <functional>
#include <map>
#include <memory>
#include <optional>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "jarvis/core/settings.hpp"
#include "jarvis/core/skill_library.hpp"
#include "jarvis/net/http.hpp"
#include "jarvis/storage/storage.hpp"

namespace jarvis::ai {

// Sabitler: Python app/core/ai.py başındaki zaman aşımı/tekrar eşiklerinin
// birebir karşılığı.
inline constexpr long kGenerateTimeoutMs = 120000;
inline constexpr long kOllamaTimeoutMs = 300000;
inline constexpr long kTokenHarborTimeoutMs = 180000;
inline constexpr long kListTimeoutMs = 30000;
inline constexpr int kMaxInlineImages = 2;
inline constexpr int kMaxFallbackModels = 3;
inline constexpr int kMaxActionNudges = 2;
inline constexpr int kMaxFailureReflections = 3;
inline constexpr int kStagnantWarningAt = 3;

// Gemini model kimliğini sohbet için puanlar; uygun değilse std::nullopt.
std::optional<std::array<int, 4>> model_rank(const std::string& model_id);

// Modelin desteklediği en hızlı düşünme seviyesi ("low"/"minimal"); yoksa nullopt.
std::optional<std::string> thinking_level_for(const std::string& model);

std::vector<std::string> ranked_models(const std::vector<std::string>& model_ids,
                                       const std::vector<std::string>& exclude = {});
std::optional<std::string> best_model(const std::vector<std::string>& model_ids,
                                      const std::string& exclude = "");

// Türkçe metni ASCII'ye katlayıp küçültür (ğ->g, ş->s vb.) — regex eşleştirmesi için.
std::string fold_tr(const std::string& text);

// Kullanıcı bir işlem mi istiyor, yoksa bilgi mi soruyor (Python regex karşılığı).
bool requires_action(const std::string& prompt);
bool is_handoff(const std::string& text);
bool should_reflect(const std::string& result);
bool gave_up(const std::string& text);

// Bir araç çağrısını çalıştıran geri çağırma: (araç adı, argüman JSON) -> sonuç metni.
using ToolHandler = std::function<std::string(const std::string&, const nlohmann::json&)>;
using ChunkHandler = std::function<void(const std::string&)>;

// Gemini / Ollama / Token Harbor arkasındaki sohbeyürütme motoru.
// Python AI sınıfının C++ karşılığı; anahtar yalnızca Settings'ten okunur,
// asla loglanmaz veya diske yazılmaz.
class AI {
public:
    AI(core::Settings& settings, storage::Storage& storage, core::SkillLibrary& skills);

    // Worker ve arayüz katmanının paylaştığı bağımlılığa canlı erişim.
    core::Settings& settings() { return settings_; }
    core::SkillLibrary& skills() { return skills_; }
    storage::Storage& storage() { return storage_; }

    void cancel();
    void add_screenshot(const std::string& path);

    bool quota_blocked();
    static void reset_quota();

    std::vector<std::string> list_models();

    std::string ask(const std::string& prompt,
                    const std::string& system_prompt = "",
                    bool stream = false,
                    ChunkHandler on_chunk = nullptr,
                    const std::vector<std::string>& attachments = {},
                    ToolHandler tool_handler = nullptr,
                    bool record_history = true,
                    const std::vector<std::string>& image_paths = {});

    // Test edilebilirlik için: sistem istemi ve içerik derleme yardımcıları.
    std::string build_system_prompt();
    std::string build_contents(const std::string& prompt,
                               const std::vector<std::string>& attachments);

    static const nlohmann::json& tool_declarations();

private:
    struct QuotaState;

    std::string memory_command(const std::string& prompt);
    std::string greeting_reply(const std::string& prompt);
    std::string failure_reflection(const std::string& last_failure);
    std::string quota_text(const std::string& detail = "");
    std::string http_error_text(const net::HttpResponse& response, const std::string& model,
                               const std::vector<std::string>& available,
                               const std::vector<std::string>& tried);

    std::string ask_gemini(const std::string& prompt, const std::string& system_prompt,
                           bool stream, const ChunkHandler& on_chunk,
                           const std::vector<std::string>& attachments,
                           const ToolHandler& tool_handler, bool record_history,
                           const std::vector<std::string>& image_paths);
    std::string ask_ollama(const std::string& prompt, const std::string& system_prompt,
                           const std::vector<std::string>& attachments,
                           const ToolHandler& tool_handler, bool record_history,
                           const std::vector<std::string>& image_paths);
    std::string ask_tokenharbor(const std::string& prompt, const std::string& system_prompt,
                                const std::vector<std::string>& attachments,
                                const ToolHandler& tool_handler, bool record_history,
                                const std::vector<std::string>& image_paths);

    // Google API yardımcılarını taşıyan ağ istemcisi (allow_error_status=true).
    net::HttpClient& gemini_client();
    net::HttpClient& ollama_client();
    net::HttpClient& harbor_client();

    // Tek Gemini POST'u; 400+"thinking" hatasında ayarsız tekrar dener.
    net::HttpResponse post_once(const std::string& api_key, const std::string& model,
                                 const nlohmann::json& payload, bool stream);

    // Yoğunluk/kota/eksik model durumunda yedek modellere geçer.
    struct SendResult {
        net::HttpResponse response;
        std::string model;
        std::vector<std::string> available;
        std::vector<std::string> tried;
        bool ok() const { return response.status_code >= 200 && response.status_code < 300; }
    };
    SendResult send(const std::string& api_key, const std::string& current_model,
                    const nlohmann::json& payload, bool stream);

    std::vector<std::string> take_screenshots();
    std::vector<std::string> tokenharbor_models();
    void set_model_silently(const std::string& model);

    int agent_step_limit();
    int agent_seconds_limit();
    int agent_loop_limit();

    core::Settings& settings_;
    storage::Storage& storage_;
    core::SkillLibrary& skills_;

    // İstemciler yerinde durur: adres değişse de nesne kimliği değişmez, yoksa
    // döngü içinde saklanan `client` referansı sarkık hale gelir.
    net::HttpClient gemini_;
    net::HttpClient ollama_;
    net::HttpClient harbor_;
    std::vector<std::string> screenshots_;
    std::string tokenharbor_model_;
    bool cancelled_ = false;
};

}  // namespace jarvis::ai
