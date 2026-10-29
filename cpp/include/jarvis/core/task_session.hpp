#pragma once

// Görev oturumu, adım izi ve insan devralma yönetimi.
// Python app/core/task_session.py dosyasının C++20 karşılığı.
//
// Bir ajan çalışması boyunca planlanan/uygulanan her araç adımı, notlar ve
// duraklatma/devralma durumu SQLite'ta tutulur. Oturum kapanınca iz Markdown ve
// JSON olarak dışa aktarılabilir; başarılı bir izden yerel yetenek üretilebilir.
//
// İzler yalnızca araç adı, kısa argüman özeti ve sonuç metnini saklar. Dosya
// içerikleri, parolalar ve API anahtarları özetlenmez.

#include <filesystem>
#include <optional>
#include <stdexcept>
#include <string>

#include <nlohmann/json.hpp>

#include <jarvis/core/skill_library.hpp>
#include <jarvis/storage/storage.hpp>

namespace jarvis::core {

class TaskSessionError : public std::runtime_error {
public:
    explicit TaskSessionError(const std::string& message) : std::runtime_error(message) {}
};

// Argümanları sır ve içerik sızdırmadan tek satırda özetler.
std::string summarize_arguments(const nlohmann::json& arguments, std::size_t limit = 220);

// Araç sonucundan (varsa) ekran görüntüsü yolunu çıkarır.
std::optional<std::string> extract_screenshot(const std::string& result);

class TaskSessionManager {
public:
    // data_dir boşsa storage.db_path'in bulunduğu dizin kullanılır.
    TaskSessionManager(jarvis::storage::Storage& storage, SkillLibrary& skills,
                       std::filesystem::path data_dir = {});

    std::int64_t session_id() const { return session_id_; }
    // Python'daki `session.session_id = None` karşılığı; 0 = oturum yok.
    void set_session_id(std::int64_t id) { session_id_ = id; }

    std::int64_t start(const std::string& goal, std::optional<std::int64_t> run_id = std::nullopt);
    void finish(const std::string& status, const std::string& summary = "");
    std::optional<nlohmann::json> current();

    std::string pause(const std::string& reason = "");
    std::string resume();
    std::string takeover(const std::string& reason = "");

    void record_step(const std::string& tool, const nlohmann::json& arguments,
                     const std::string& result);
    std::string note(const std::string& text);
    std::string describe(int steps = 12);
    std::string export_session(std::optional<std::int64_t> session_id = std::nullopt);
    std::string build_skill(const std::string& name, const std::string& description,
                            std::optional<std::int64_t> session_id = std::nullopt,
                            bool overwrite = false);

private:
    jarvis::storage::Storage& storage_;
    SkillLibrary& skills_;
    std::filesystem::path data_dir_;
    std::int64_t session_id_ = 0;  // 0 = yok
    int seq_ = 0;

    int next_seq(std::int64_t session);
};

}  // namespace jarvis::core
