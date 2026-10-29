#include <jarvis/storage/storage.hpp>

#include <sqlite3.h>

#include <algorithm>
#include <cstdlib>
#include <filesystem>
#include <memory>
#include <stdexcept>
#include <utility>

#include <jarvis/core/util.hpp>

namespace jarvis::storage {

namespace {

struct DbDeleter {
    void operator()(sqlite3* db) const noexcept {
        if (db) sqlite3_close(db);
    }
};
using DbPtr = std::unique_ptr<sqlite3, DbDeleter>;

struct StmtDeleter {
    void operator()(sqlite3_stmt* stmt) const noexcept {
        if (stmt) sqlite3_finalize(stmt);
    }
};
using StmtPtr = std::unique_ptr<sqlite3_stmt, StmtDeleter>;

[[noreturn]] void throw_sql(sqlite3* db, const std::string& context) {
    const char* message = db ? sqlite3_errmsg(db) : "bilinmeyen hata";
    throw std::runtime_error(context + ": " + (message ? message : ""));
}

void exec(sqlite3* db, const std::string& sql) {
    char* err = nullptr;
    if (sqlite3_exec(db, sql.c_str(), nullptr, nullptr, &err) != SQLITE_OK) {
        std::string message = err ? err : "bilinmeyen hata";
        sqlite3_free(err);
        throw std::runtime_error(message);
    }
}

StmtPtr prepare(sqlite3* db, const std::string& sql) {
    sqlite3_stmt* stmt = nullptr;
    if (sqlite3_prepare_v2(db, sql.c_str(), -1, &stmt, nullptr) != SQLITE_OK) {
        throw_sql(db, "prepare");
    }
    return StmtPtr(stmt);
}

void bind_text(StmtPtr& stmt, int index, const std::optional<std::string>& value) {
    if (value.has_value()) {
        // Bozuk bayt kaydı sonradan JSON dökümünü patlatabilir; yazmadan önce temizle.
        const std::string clean = util::utf8_sanitize(*value);
        sqlite3_bind_text(stmt.get(), index, clean.c_str(), -1, SQLITE_TRANSIENT);
    } else {
        sqlite3_bind_null(stmt.get(), index);
    }
}

void bind_text(StmtPtr& stmt, int index, const std::string& value) {
    const std::string clean = util::utf8_sanitize(value);
    sqlite3_bind_text(stmt.get(), index, clean.c_str(), -1, SQLITE_TRANSIENT);
}

void bind_int64(StmtPtr& stmt, int index, std::int64_t value) {
    sqlite3_bind_int64(stmt.get(), index, value);
}

void bind_int(StmtPtr& stmt, int index, int value) {
    sqlite3_bind_int(stmt.get(), index, value);
}

// Bir adım ilerler; satır varsa true, sona gelindiyse false döner.
bool step_row(StmtPtr& stmt) {
    int rc = sqlite3_step(stmt.get());
    if (rc == SQLITE_ROW) return true;
    if (rc == SQLITE_DONE) return false;
    throw_sql(sqlite3_db_handle(stmt.get()), "step");
}

std::string text_column(sqlite3_stmt* stmt, int index) {
    const unsigned char* text = sqlite3_column_text(stmt, index);
    if (!text) return {};
    return std::string(reinterpret_cast<const char*>(text),
                       static_cast<size_t>(sqlite3_column_bytes(stmt, index)));
}

nlohmann::json column_value(sqlite3_stmt* stmt, int index) {
    switch (sqlite3_column_type(stmt, index)) {
        case SQLITE_INTEGER:
            return static_cast<std::int64_t>(sqlite3_column_int64(stmt, index));
        case SQLITE_FLOAT:
            return sqlite3_column_double(stmt, index);
        case SQLITE_TEXT:
            return text_column(stmt, index);
        case SQLITE_NULL:
        default:
            return nullptr;
    }
}

// Satırın tüm sütunlarını sütun adı -> değer olarak json nesnesine çevirir.
nlohmann::json row_to_json(sqlite3_stmt* stmt) {
    nlohmann::json obj = nlohmann::json::object();
    int count = sqlite3_column_count(stmt);
    for (int i = 0; i < count; ++i) {
        obj[sqlite3_column_name(stmt, i)] = column_value(stmt, i);
    }
    return obj;
}

// messages tablosu satırını ChatMessage için hazırlar; metadata metnini JSON'a çevirir.
// Sütun sırası: role, content, source, metadata, created_at
nlohmann::json message_row(sqlite3_stmt* stmt) {
    nlohmann::json obj;
    obj["role"] = text_column(stmt, 0);
    obj["content"] = text_column(stmt, 1);
    obj["source"] = text_column(stmt, 2);
    if (sqlite3_column_type(stmt, 3) == SQLITE_NULL) {
        obj["metadata"] = nullptr;
    } else {
        try {
            obj["metadata"] = nlohmann::json::parse(text_column(stmt, 3));
        } catch (const nlohmann::json::exception&) {
            obj["metadata"] = nullptr;
        }
    }
    obj["created_at"] = text_column(stmt, 4);
    return obj;
}

std::string env_or(const char* name, const std::string& fallback) {
    const char* value = std::getenv(name);
    return (value && *value) ? std::string(value) : fallback;
}

std::string default_db_path() {
    const std::string root = env_or("LOCALAPPDATA", env_or("USERPROFILE", "."));
    return (std::filesystem::path(root) / "Jarvis" / "data" / "jarvis.db").string();
}

}  // namespace

struct Storage::Impl {
    DbPtr db;
    std::filesystem::path path;
};

Storage::Storage(std::optional<std::string> db_path) : impl_(std::make_unique<Impl>()) {
    const std::string path = db_path.value_or(default_db_path());
    std::filesystem::path file(path);
    impl_->path = file;
    std::filesystem::create_directories(file.parent_path());

    sqlite3* raw = nullptr;
    if (sqlite3_open(file.string().c_str(), &raw) != SQLITE_OK) {
        const std::string message = raw ? sqlite3_errmsg(raw) : "veritabanı açılamadı";
        if (raw) sqlite3_close(raw);
        throw std::runtime_error(message);
    }
    impl_->db.reset(raw);

    exec(impl_->db.get(),
         "CREATE TABLE IF NOT EXISTS messages ("
         "id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL, "
         "content TEXT NOT NULL, source TEXT NOT NULL, metadata TEXT, "
         "created_at TEXT NOT NULL)");
    exec(impl_->db.get(),
         "CREATE TABLE IF NOT EXISTS memory ("
         "key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)");
    exec(impl_->db.get(),
         "CREATE TABLE IF NOT EXISTS agent_runs ("
         "id INTEGER PRIMARY KEY AUTOINCREMENT, prompt TEXT NOT NULL, "
         "status TEXT NOT NULL, summary TEXT, created_at TEXT NOT NULL, "
         "finished_at TEXT)");
    exec(impl_->db.get(),
         "CREATE TABLE IF NOT EXISTS agent_events ("
         "id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, "
         "event_type TEXT NOT NULL, payload TEXT, created_at TEXT NOT NULL, "
         "FOREIGN KEY(run_id) REFERENCES agent_runs(id))");
    exec(impl_->db.get(),
         "CREATE TABLE IF NOT EXISTS task_sessions ("
         "id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, goal TEXT NOT NULL, "
         "status TEXT NOT NULL, summary TEXT, pause_reason TEXT, "
         "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, finished_at TEXT)");
    exec(impl_->db.get(),
         "CREATE TABLE IF NOT EXISTS task_steps ("
         "id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL, "
         "seq INTEGER NOT NULL, tool TEXT NOT NULL, args_summary TEXT, result TEXT, "
         "status TEXT NOT NULL, screenshot TEXT, created_at TEXT NOT NULL, "
         "FOREIGN KEY(session_id) REFERENCES task_sessions(id))");

    // Eski veritabanlarında messages.metadata sütunu olmayabilir.
    bool has_metadata = false;
    {
        auto stmt = prepare(impl_->db.get(), "PRAGMA table_info(messages)");
        while (step_row(stmt)) {
            if (text_column(stmt.get(), 1) == "metadata") {
                has_metadata = true;
                break;
            }
        }
    }
    if (!has_metadata) {
        exec(impl_->db.get(), "ALTER TABLE messages ADD COLUMN metadata TEXT");
    }
}

Storage::~Storage() = default;
Storage::Storage(Storage&&) noexcept = default;
Storage& Storage::operator=(Storage&&) noexcept = default;

void Storage::close() { impl_->db.reset(); }

std::string Storage::db_path() const { return impl_->path.string(); }

// ---------------------------------------------------------------------------
// İletiler
// ---------------------------------------------------------------------------
void Storage::add_message(const std::string& role, const std::string& content,
                          const std::string& source,
                          const std::optional<nlohmann::json>& metadata,
                          const std::string& created_at) {
    auto stmt = prepare(impl_->db.get(),
                        "INSERT INTO messages (role, content, source, metadata, created_at) "
                        "VALUES (?, ?, ?, ?, ?)");
    bind_text(stmt, 1, role);
    bind_text(stmt, 2, content);
    bind_text(stmt, 3, source);
    if (metadata.has_value()) {
        bind_text(stmt, 4, metadata->dump());
    } else {
        sqlite3_bind_null(stmt.get(), 4);
    }
    bind_text(stmt, 5, created_at.empty() ? util::now_utc_iso() : created_at);
    if (sqlite3_step(stmt.get()) != SQLITE_DONE) {
        throw_sql(impl_->db.get(), "add_message");
    }
}

std::vector<core::ChatMessage> Storage::get_recent_messages(int limit) {
    auto stmt = prepare(impl_->db.get(),
                        "SELECT role, content, source, metadata, created_at "
                        "FROM messages ORDER BY id DESC LIMIT ?");
    bind_int(stmt, 1, limit);
    std::vector<core::ChatMessage> messages;
    while (step_row(stmt)) {
        messages.push_back(core::ChatMessage::from_row(message_row(stmt.get())));
    }
    std::reverse(messages.begin(), messages.end());
    return messages;
}

std::vector<core::ChatMessage> Storage::search_messages(const std::string& query, int limit) {
    std::string text = util::trim(query);
    if (text.empty()) {
        return get_recent_messages(limit);
    }
    // LIKE için \ % _ karakterlerini kaçışla.
    std::string escaped;
    for (char c : text) {
        if (c == '\\' || c == '%' || c == '_') escaped.push_back('\\');
        escaped.push_back(c);
    }
    const std::string pattern = "%" + escaped + "%";
    auto stmt = prepare(impl_->db.get(),
                        "SELECT role, content, source, metadata, created_at FROM messages "
                        "WHERE content LIKE ? ESCAPE '\\' OR role LIKE ? ESCAPE '\\' "
                        "ORDER BY id DESC LIMIT ?");
    bind_text(stmt, 1, pattern);
    bind_text(stmt, 2, pattern);
    bind_int(stmt, 3, limit < 1 ? 1 : limit);
    std::vector<core::ChatMessage> messages;
    while (step_row(stmt)) {
        messages.push_back(core::ChatMessage::from_row(message_row(stmt.get())));
    }
    std::reverse(messages.begin(), messages.end());
    return messages;
}

std::vector<core::ChatMessage> Storage::get_conversation_history(int limit, bool exclude_last) {
    auto messages = get_recent_messages(limit);
    if (exclude_last && !messages.empty()) {
        messages.pop_back();
    }
    return messages;
}

// ---------------------------------------------------------------------------
// Ajan koşuları
// ---------------------------------------------------------------------------
std::int64_t Storage::start_agent_run(const std::string& prompt) {
    auto stmt = prepare(impl_->db.get(),
                        "INSERT INTO agent_runs (prompt, status, created_at) VALUES (?, 'running', ?)");
    bind_text(stmt, 1, prompt);
    bind_text(stmt, 2, util::now_utc_iso());
    if (sqlite3_step(stmt.get()) != SQLITE_DONE) {
        throw_sql(impl_->db.get(), "start_agent_run");
    }
    return static_cast<std::int64_t>(sqlite3_last_insert_rowid(impl_->db.get()));
}

void Storage::log_agent_event(std::int64_t run_id, const std::string& event_type,
                              const std::optional<nlohmann::json>& payload) {
    auto stmt = prepare(impl_->db.get(),
                        "INSERT INTO agent_events (run_id, event_type, payload, created_at) "
                        "VALUES (?, ?, ?, ?)");
    bind_int64(stmt, 1, run_id);
    bind_text(stmt, 2, event_type);
    bind_text(stmt, 3, payload.has_value() ? std::optional<std::string>(payload->dump())
                                           : std::nullopt);
    bind_text(stmt, 4, util::now_utc_iso());
    if (sqlite3_step(stmt.get()) != SQLITE_DONE) {
        throw_sql(impl_->db.get(), "log_agent_event");
    }
}

void Storage::finish_agent_run(std::int64_t run_id, const std::string& status,
                               const std::optional<std::string>& summary) {
    auto stmt = prepare(impl_->db.get(),
                        "UPDATE agent_runs SET status = ?, summary = ?, finished_at = ? WHERE id = ?");
    bind_text(stmt, 1, status);
    bind_text(stmt, 2, summary);
    bind_text(stmt, 3, util::now_utc_iso());
    bind_int64(stmt, 4, run_id);
    if (sqlite3_step(stmt.get()) != SQLITE_DONE) {
        throw_sql(impl_->db.get(), "finish_agent_run");
    }
}

std::vector<nlohmann::json> Storage::get_agent_runs(int limit) {
    auto stmt = prepare(impl_->db.get(),
                        "SELECT id, prompt, status, summary, created_at, finished_at "
                        "FROM agent_runs ORDER BY id DESC LIMIT ?");
    bind_int(stmt, 1, limit < 1 ? 1 : limit);
    std::vector<nlohmann::json> rows;
    while (step_row(stmt)) {
        rows.push_back(row_to_json(stmt.get()));
    }
    return rows;
}

std::vector<nlohmann::json> Storage::get_agent_events(std::int64_t run_id, int limit) {
    auto stmt = prepare(impl_->db.get(),
                        "SELECT id, event_type, payload, created_at FROM agent_events "
                        "WHERE run_id = ? ORDER BY id ASC LIMIT ?");
    bind_int64(stmt, 1, run_id);
    bind_int(stmt, 2, limit < 1 ? 1 : limit);
    std::vector<nlohmann::json> rows;
    while (step_row(stmt)) {
        rows.push_back(row_to_json(stmt.get()));
    }
    return rows;
}

// ---------------------------------------------------------------------------
// Görev oturumları
// ---------------------------------------------------------------------------
std::int64_t Storage::start_task_session(const std::string& goal,
                                         std::optional<std::int64_t> run_id) {
    const std::string now = util::now_utc_iso();
    auto stmt = prepare(impl_->db.get(),
                        "INSERT INTO task_sessions (run_id, goal, status, created_at, updated_at) "
                        "VALUES (?, ?, 'running', ?, ?)");
    if (run_id.has_value()) {
        bind_int64(stmt, 1, *run_id);
    } else {
        sqlite3_bind_null(stmt.get(), 1);
    }
    bind_text(stmt, 2, goal);
    bind_text(stmt, 3, now);
    bind_text(stmt, 4, now);
    if (sqlite3_step(stmt.get()) != SQLITE_DONE) {
        throw_sql(impl_->db.get(), "start_task_session");
    }
    return static_cast<std::int64_t>(sqlite3_last_insert_rowid(impl_->db.get()));
}

std::optional<nlohmann::json> Storage::get_task_session(std::int64_t session_id) {
    auto stmt = prepare(impl_->db.get(), "SELECT * FROM task_sessions WHERE id = ?");
    bind_int64(stmt, 1, session_id);
    if (step_row(stmt)) {
        return row_to_json(stmt.get());
    }
    return std::nullopt;
}

std::optional<nlohmann::json> Storage::get_active_task_session() {
    auto stmt = prepare(impl_->db.get(),
                        "SELECT * FROM task_sessions WHERE status IN ('running', 'paused', 'takeover') "
                        "ORDER BY id DESC LIMIT 1");
    if (step_row(stmt)) {
        return row_to_json(stmt.get());
    }
    return std::nullopt;
}

std::optional<nlohmann::json> Storage::get_latest_task_session() {
    auto stmt = prepare(impl_->db.get(), "SELECT * FROM task_sessions ORDER BY id DESC LIMIT 1");
    if (step_row(stmt)) {
        return row_to_json(stmt.get());
    }
    return std::nullopt;
}

std::vector<nlohmann::json> Storage::list_task_sessions(int limit) {
    auto stmt = prepare(impl_->db.get(), "SELECT * FROM task_sessions ORDER BY id DESC LIMIT ?");
    bind_int(stmt, 1, limit < 1 ? 1 : limit);
    std::vector<nlohmann::json> rows;
    while (step_row(stmt)) {
        rows.push_back(row_to_json(stmt.get()));
    }
    return rows;
}

void Storage::set_task_session_state(std::int64_t session_id, const std::string& status,
                                     const std::optional<std::string>& pause_reason,
                                     const std::optional<std::string>& summary) {
    const std::string now = util::now_utc_iso();
    const bool finished = (status == "completed" || status == "cancelled" || status == "error");
    auto stmt = prepare(impl_->db.get(),
                        "UPDATE task_sessions SET status = ?, pause_reason = ?, "
                        "summary = COALESCE(?, summary), updated_at = ?, "
                        "finished_at = COALESCE(?, finished_at) WHERE id = ?");
    bind_text(stmt, 1, status);
    bind_text(stmt, 2, pause_reason);
    bind_text(stmt, 3, summary);
    bind_text(stmt, 4, now);
    bind_text(stmt, 5, finished ? std::optional<std::string>(now) : std::nullopt);
    bind_int64(stmt, 6, session_id);
    if (sqlite3_step(stmt.get()) != SQLITE_DONE) {
        throw_sql(impl_->db.get(), "set_task_session_state");
    }
}

void Storage::add_task_step(std::int64_t session_id, int seq, const std::string& tool,
                            const std::string& args_summary, const std::string& result,
                            const std::string& status,
                            const std::optional<std::string>& screenshot) {
    const std::string now = util::now_utc_iso();
    auto stmt = prepare(impl_->db.get(),
                        "INSERT INTO task_steps (session_id, seq, tool, args_summary, result, "
                        "status, screenshot, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)");
    bind_int64(stmt, 1, session_id);
    bind_int(stmt, 2, seq);
    bind_text(stmt, 3, tool);
    bind_text(stmt, 4, args_summary.size() > 2000 ? args_summary.substr(0, 2000) : args_summary);
    bind_text(stmt, 5, result.size() > 4000 ? result.substr(0, 4000) : result);
    bind_text(stmt, 6, status);
    bind_text(stmt, 7, screenshot);
    bind_text(stmt, 8, now);
    if (sqlite3_step(stmt.get()) != SQLITE_DONE) {
        throw_sql(impl_->db.get(), "add_task_step");
    }
    auto update = prepare(impl_->db.get(),
                          "UPDATE task_sessions SET updated_at = ? WHERE id = ?");
    bind_text(update, 1, now);
    bind_int64(update, 2, session_id);
    if (sqlite3_step(update.get()) != SQLITE_DONE) {
        throw_sql(impl_->db.get(), "add_task_step(update)");
    }
}

std::vector<nlohmann::json> Storage::get_task_steps(std::int64_t session_id, int limit) {
    auto stmt = prepare(impl_->db.get(),
                        "SELECT * FROM task_steps WHERE session_id = ? ORDER BY seq ASC, id ASC LIMIT ?");
    bind_int64(stmt, 1, session_id);
    bind_int(stmt, 2, limit < 1 ? 1 : limit);
    std::vector<nlohmann::json> rows;
    while (step_row(stmt)) {
        rows.push_back(row_to_json(stmt.get()));
    }
    return rows;
}

// ---------------------------------------------------------------------------
// Anahtar-değer bellek
// ---------------------------------------------------------------------------
void Storage::save_memory(const std::string& key, const std::string& value) {
    auto stmt = prepare(impl_->db.get(),
                        "INSERT INTO memory (key, value, updated_at) VALUES (?, ?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                        "updated_at = excluded.updated_at");
    bind_text(stmt, 1, key);
    bind_text(stmt, 2, value);
    bind_text(stmt, 3, util::now_utc_iso());
    if (sqlite3_step(stmt.get()) != SQLITE_DONE) {
        throw_sql(impl_->db.get(), "save_memory");
    }
}

std::optional<std::string> Storage::get_memory(const std::string& key) {
    auto stmt = prepare(impl_->db.get(), "SELECT value FROM memory WHERE key = ?");
    bind_text(stmt, 1, key);
    if (step_row(stmt)) {
        return text_column(stmt.get(), 0);
    }
    return std::nullopt;
}

void Storage::delete_memory(const std::string& key) {
    auto stmt = prepare(impl_->db.get(), "DELETE FROM memory WHERE key = ?");
    bind_text(stmt, 1, key);
    if (sqlite3_step(stmt.get()) != SQLITE_DONE) {
        throw_sql(impl_->db.get(), "delete_memory");
    }
}

std::vector<nlohmann::json> Storage::get_all_memory() {
    auto stmt = prepare(impl_->db.get(),
                        "SELECT key, value, updated_at FROM memory ORDER BY updated_at DESC");
    std::vector<nlohmann::json> rows;
    while (step_row(stmt)) {
        rows.push_back(row_to_json(stmt.get()));
    }
    return rows;
}

}  // namespace jarvis::storage
