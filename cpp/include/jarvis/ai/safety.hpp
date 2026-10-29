#pragma once

// Jarvis güvenlik çekirdeği — Python app/core/safety.py mantığının C++20 karşılığı.
//
// Yalnızca salt karar üreten, işletim sisteminden bağımsız bölüm port edilmiştir:
//   Risk, AccessMode, Decision, PermissionGate, PathPolicy, KillSwitch, AuditLog,
//   classify_action, PlanAssessment, SafetyServices.
//
// Dosya değiştirme/yedekleme (UndoStore/ChangeSet) somut yürütücünün işidir ve
// buraya alınmamıştır; worker bunu IActionExecutor arayüzü arkasından kullanır.

#include <atomic>
#include <filesystem>
#include <functional>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "jarvis/core/settings.hpp"

namespace jarvis::ai {

// --- risk seviyesi (Python Risk IntEnum) ---
enum class Risk : int {
    Read = 0,       // okuma / listeleme
    Create = 1,     // yeni şey oluşturma, uygulama açma
    Modify = 2,     // var olanı değiştirme, taşıma, geri alma
    Dangerous = 3,  // silme, komut çalıştırma
    Forbidden = 9,  // hiçbir modda yapılmaz
};

const char* risk_label(Risk risk);

// --- erişim modu sabitleri ---
namespace access_mode {
inline constexpr const char* kApproval = "approval";
inline constexpr const char* kStep = "step";
inline constexpr const char* kFull = "full";
std::string normalize(const std::string& value);
}  // namespace access_mode

// --- güvenlik hataları (SafetyError ailesi) ---
class SafetyError : public std::runtime_error {
public:
    explicit SafetyError(const std::string& message) : std::runtime_error(message) {}
};
class PathNotAllowed : public SafetyError {
public:
    explicit PathNotAllowed(const std::string& m) : SafetyError(m) {}
};
class ActionForbidden : public SafetyError {
public:
    explicit ActionForbidden(const std::string& m) : SafetyError(m) {}
};
class BackupError : public SafetyError {
public:
    explicit BackupError(const std::string& m) : SafetyError(m) {}
};

// --- yol yardımcıları ---
std::vector<std::filesystem::path> default_roots(
    const std::filesystem::path& home = {});
bool is_within(const std::filesystem::path& child, const std::filesystem::path& parent);

// PathPolicy: dosya yollarının güvenli olup olmadığını denetler.
class PathPolicy {
public:
    PathPolicy(const std::vector<std::filesystem::path>& roots,
               const std::vector<std::pair<std::filesystem::path, std::string>>& forbidden = {},
               const std::filesystem::path& base = {});

    // raw'ı doğrular ve güvenli mutlak yol olarak çözer; ihlalde PathNotAllowed.
    std::filesystem::path resolve(const std::string& raw) const;
    bool is_protected_root(const std::filesystem::path& path) const;
    bool is_executable(const std::filesystem::path& path) const;

    // Yasak bölgeler (kök, açıklama) — run_command komut metnini de burada tarar.
    const std::vector<std::pair<std::filesystem::path, std::string>>& forbidden() const {
        return forbidden_;
    }

    static bool secret_dir_or_file(const std::string& name_lower);

private:
    std::vector<std::filesystem::path> roots_;
    std::vector<std::pair<std::filesystem::path, std::string>> forbidden_;
    std::filesystem::path base_;
};

// --- izin kararı ---
struct Decision {
    std::string action;  // "allow" | "ask" | "deny"
    std::string reason;
};

// PermissionGate: risk + erişim modu -> allow/ask/deny.
class PermissionGate {
public:
    using ModeFn = std::function<std::string()>;
    using StrictFn = std::function<bool()>;

    PermissionGate() = default;
    PermissionGate(ModeFn mode, StrictFn strict_after_web);

    Decision decide(Risk risk, bool tainted = false) const;

private:
    ModeFn mode_;
    StrictFn strict_after_web_;
};

// --- kill switch (Durdur düğmesi) ---
class KillSwitch {
public:
    void request() { set_.store(true); }
    void clear() { set_.store(false); }
    bool is_set() const { return set_.load(); }

private:
    std::atomic<bool> set_{false};
};

// Python'daki modül geneli KILL_SWITCH tekil örneğinin karşılığı: yürütücü ile
// worker aynı anahtarı paylaşır.
KillSwitch& global_kill();

// --- audit günlüğü (satır satır JSON; içerik değil özet) ---
class AuditLog {
public:
    explicit AuditLog(const std::filesystem::path& path, long long max_bytes = 2000000);

    void record(const std::string& tool, const nlohmann::json& args = nlohmann::json(),
                const nlohmann::json& extra = nlohmann::json());
    std::vector<nlohmann::json> tail(int count = 20) const;

    const std::filesystem::path& path() const { return path_; }

private:
    std::filesystem::path path_;
    long long max_bytes_;
};

// --- plan değerlendirmesi ---
struct PlanAssessment {
    std::string decision;  // "allow" | "ask" | "deny"
    Risk risk = Risk::Read;
    std::vector<std::string> reasons;
};

// Eylem türü + argümanlardan risk türetir; kural çiğneyen eylemde SafetyError fırlatır.
Risk classify_action(const std::string& action_type, const nlohmann::json& arguments,
                     const PathPolicy& policy);

// --- servis paketi ---
class UndoStore;

struct SafetyServices {
    PathPolicy policy{std::vector<std::filesystem::path>{}};
    PermissionGate gate;
    AuditLog audit{std::filesystem::path{}};
    KillSwitch* kill = &global_kill();
    std::function<bool(const std::string&)> approval;
    core::Settings* settings = nullptr;
    // Yürütücü dosya değişikliklerini buraya kaydeder; nullptr → geri alma kapalı.
    std::shared_ptr<UndoStore> undo;
};

// Ayarlardan güvenlik servislerini kurar (Python build_services karşılığı).
SafetyServices build_services(core::Settings& settings, KillSwitch* shared_kill = nullptr);

}  // namespace jarvis::ai
