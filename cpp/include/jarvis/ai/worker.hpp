#pragma once

// AIWorker — Python app/core/worker.py orkestrasyon mantığının C++20 karşılığı.
//
// Qt'ye bağımlı değildir: sinyal yerine WorkerCallbacks geri çağırmaları kullanır,
// böylece başsız (headless) test edilebilir. Somut işletim sistemi eylemleri
// IActionExecutor arayüzü arkasındadır; UI/entegrasyon katmanı onu uygular.
//
// Sorumluluklar: hızlı yerel yol seçimi, duraklat/devam/devral ve onay geçitleri,
// araç çağrılarını güvenlik + oturum kaydıyla yürütmek, AI.ask'ı araç işleyicisiyle
// beslemek ve koşu başlangıç/bitiş kaydını Storage'a yazmak.

#include <atomic>
#include <condition_variable>
#include <functional>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "jarvis/ai/action_executor.hpp"
#include "jarvis/ai/agent.hpp"
#include "jarvis/ai/ai.hpp"
#include "jarvis/ai/safety.hpp"
#include "jarvis/core/skill_library.hpp"
#include "jarvis/core/task_session.hpp"
#include "jarvis/storage/storage.hpp"

namespace jarvis::ai {

// QObject sinyallerinin karşılığı olan geri çağırmalar.
struct WorkerCallbacks {
    std::function<void()> started;
    std::function<void()> finished;
    std::function<void()> resumed;
    std::function<void(const std::string&)> response;
    std::function<void(const std::string&)> chunk;
    std::function<void(const std::string&)> error;
    std::function<void(const std::string&)> progress;
    std::function<void(const std::string&)> approval_requested;
    std::function<void(const std::string&)> paused;
    std::function<void(const std::string&)> takeover_requested;
    std::function<void(const std::string&)> browser_view;
    std::function<void(const nlohmann::json&)> map_view;
    std::function<void(const nlohmann::json&)> book_view;
};

class AIWorker {
public:
    AIWorker(AI& ai, storage::Storage& storage, core::SkillLibrary& skills, SafetyServices& services,
             IActionExecutor& executor);

    // girdi kurucuları (set_prompt/set_plan/... Python karşılıkları)
    void set_prompt(const std::string& prompt);
    // std::nullopt: plan override kaldırılır, yerel ayrıştırıcı devreye girer.
    void set_plan(std::optional<ActionPlan> plan);
    void set_attachments(const std::vector<std::string>& attachments);
    void set_camera_context(const std::string& image_path, const std::string& scene_summary = "");
    void set_screen_context(const std::string& image_path);
    void set_speak_response(bool enabled);

    void cancel();
    bool is_paused() const;
    bool request_running() const { return running_.load(); }
    void pause(const std::string& reason = "");
    void resume();
    void takeover(const std::string& reason = "");
    void set_approval(bool approved);

    void set_callbacks(WorkerCallbacks cb) { cb_ = std::move(cb); }

    // Araç çağrısı giriş noktası: AI.ask araç döngüsünün ToolHandler'ı ve arayüzün
    // tek araç çalıştırma yolu. Güvenlik geçidi + oturum kaydı burada yürür.
    std::string execute_tool(const std::string& name, const nlohmann::json& arguments);

    // Ana yürütme — bir iş parçacığından çağrilir.
    void run();

private:
    std::string task_session_tool(const nlohmann::json& arguments);
    std::optional<ActionPlan> local_plan();
    void start_run();
    void finish_run(const std::string& status, const std::string& summary);

    bool wait_if_paused();
    bool wait_for_approval(const std::string& description);

    AI& ai_;
    storage::Storage& storage_;
    core::SkillLibrary& skills_;
    SafetyServices& services_;
    IActionExecutor& executor_;
    core::TaskSessionManager session_;
    ActionPlanner planner_;

    WorkerCallbacks cb_;

    std::string prompt_;
    std::optional<ActionPlan> plan_override_;
    std::vector<std::string> attachments_;
    std::string camera_image_;
    std::string screen_image_;
    std::string scene_summary_;
    bool speak_response_ = false;
    bool tainted_ = false;
    std::optional<std::int64_t> run_id_;

    std::atomic<bool> running_{false};

    // resume/uykuya geçiş geçidi
    mutable std::mutex gate_mx_;
    std::condition_variable gate_cv_;
    bool resumed_ = true;
    std::string pause_reason_;

    // onay geçidi
    mutable std::mutex appr_mx_;
    std::condition_variable appr_cv_;
    bool approval_ready_ = false;
    bool approved_ = false;

    static constexpr int kFastMaxWords = 12;
};

}  // namespace jarvis::ai
