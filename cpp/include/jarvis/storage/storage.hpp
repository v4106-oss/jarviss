#pragma once

#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include <jarvis/core/history.hpp>

namespace jarvis::storage {

// SQLite tabanlı kalıcı depo; Python'daki app/core/storage.py Storage sınıfının
// modern C++ karşılığı. Ham işaretçi kullanılmaz: sqlite3* benzersiz akıllı
// işaretçi ile sarılır; satırlar nlohmann::json nesnesine dönüştürülür.
class Storage {
public:
    // Varsayılan olarak %LOCALAPPDATA%/Jarvis/data/jarvis.db kullanılır.
    explicit Storage(std::optional<std::string> db_path = std::nullopt);
    ~Storage();

    Storage(const Storage&) = delete;
    Storage& operator=(const Storage&) = delete;
    Storage(Storage&&) noexcept;
    Storage& operator=(Storage&&) noexcept;

    void close();

    // Kullanılan SQLite dosya yolu; TaskSessionManager dışa aktarma klasörünü
    // bundan türetir (Python storage.db_path karşılığı).
    std::string db_path() const;

    // --- iletiler ---
    void add_message(const std::string& role, const std::string& content,
                     const std::string& source = "chat",
                     const std::optional<nlohmann::json>& metadata = std::nullopt,
                     const std::string& created_at = "");
    std::vector<core::ChatMessage> get_recent_messages(int limit = 50);
    std::vector<core::ChatMessage> search_messages(const std::string& query, int limit = 100);
    std::vector<core::ChatMessage> get_conversation_history(int limit = 50, bool exclude_last = false);

    // --- ajan koşuları ---
    std::int64_t start_agent_run(const std::string& prompt);
    void log_agent_event(std::int64_t run_id, const std::string& event_type,
                         const std::optional<nlohmann::json>& payload = std::nullopt);
    void finish_agent_run(std::int64_t run_id, const std::string& status,
                          const std::optional<std::string>& summary = std::nullopt);
    std::vector<nlohmann::json> get_agent_runs(int limit = 20);
    std::vector<nlohmann::json> get_agent_events(std::int64_t run_id, int limit = 100);

    // --- görev oturumları ---
    std::int64_t start_task_session(const std::string& goal,
                                    std::optional<std::int64_t> run_id = std::nullopt);
    std::optional<nlohmann::json> get_task_session(std::int64_t session_id);
    std::optional<nlohmann::json> get_active_task_session();
    std::optional<nlohmann::json> get_latest_task_session();
    std::vector<nlohmann::json> list_task_sessions(int limit = 12);
    void set_task_session_state(std::int64_t session_id, const std::string& status,
                                const std::optional<std::string>& pause_reason = std::nullopt,
                                const std::optional<std::string>& summary = std::nullopt);
    void add_task_step(std::int64_t session_id, int seq, const std::string& tool,
                       const std::string& args_summary, const std::string& result,
                       const std::string& status, const std::optional<std::string>& screenshot = std::nullopt);
    std::vector<nlohmann::json> get_task_steps(std::int64_t session_id, int limit = 200);

    // --- anahtar-değer bellek ---
    void save_memory(const std::string& key, const std::string& value);
    std::optional<std::string> get_memory(const std::string& key);
    void delete_memory(const std::string& key);
    std::vector<nlohmann::json> get_all_memory();

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace jarvis::storage
