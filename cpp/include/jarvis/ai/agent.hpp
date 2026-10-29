#pragma once

// Eylem planı veri modeli + deterministik Türkçe niyet ayrıştırıcısı.
// Python app/core/agent.py PlannedAction/ActionPlan/ActionPlanner karşılığı.
//
// Bu katran salt metin mantığıdır: işletim sistemine dokunmaz, yalnızca kullanıcı
// komutundan PlannedAction listesi üretir. Somut yürütme (ActionExecutor) arayüz
// arkasındadır ve worker onu enjekte eder.

#include <filesystem>
#include <optional>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

namespace jarvis::ai {

struct PlannedAction {
    std::string action_type;
    nlohmann::json arguments = nlohmann::json::object();
    std::string description;
    std::string risk = "routine";
};

struct ActionPlan {
    std::vector<PlannedAction> actions;
    std::string source = "deterministic";
    std::string reply;

    bool is_empty() const { return actions.empty(); }
    bool requires_confirmation() const {
        for (const auto& a : actions)
            if (a.risk != "routine") return true;
        return false;
    }
    std::string summary() const;
};

// Türkçe komutları belirlenebilir araç planlarına çevirir.
class ActionPlanner {
public:
    // desktop boşsa %USERPROFILE%\Desktop kullanılır.
    explicit ActionPlanner(std::filesystem::path desktop = {});

    std::optional<ActionPlan> plan(const std::string& prompt);

    const std::filesystem::path& desktop() const { return desktop_; }

private:
    std::filesystem::path desktop_;

    static std::string clean_name(const std::string& value);
    static std::string with_text_extension(const std::string& name);
    std::filesystem::path desktop_path(const std::string& name) const;
    static int count_of(const std::string& raw);

    std::optional<ActionPlan> plan_folder_and_document(const std::string& text);
    std::optional<ActionPlan> plan_notepad_text(const std::string& text);
};

}  // namespace jarvis::ai
