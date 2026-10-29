#pragma once

// IActionExecutor — Python app/core/worker.py'deki araç yürütücü arayüzü.
// Somut işletim sistemi eylemlerini ağ/servis katmanından ayırır: worker bu
// arayüzü çağırır, uygulamayı yürüten sınıf (DesktopExecutor) Win32'ye bağlar.

#include <functional>
#include <optional>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "jarvis/ai/agent.hpp"
#include "jarvis/ai/safety.hpp"

namespace jarvis::ai {

class IActionExecutor {
public:
    virtual ~IActionExecutor() = default;
    virtual PlanAssessment assess(const ActionPlan& plan, bool tainted) = 0;
    virtual std::string execute(const PlannedAction& action) = 0;
    virtual std::vector<std::string> execute_plan(
        const ActionPlan& plan, const std::function<void(const std::string&)>& on_progress) = 0;
    // apply_self_changes onay metni için; varsayılan boş.
    virtual std::string diff_self_workspace(const nlohmann::json& /*arguments*/) { return ""; }
    // Ekran yakalama araçlarının görüntüyü modelin sonraki adımına vermesi için.
    // Arayüz std::function bilir, somut sınıfa (AIWorker/Qt) bağımlılığı yoktur.
    virtual void set_screenshot_sink(std::function<void(const std::string&)> /*sink*/) {}
};

}  // namespace jarvis::ai
