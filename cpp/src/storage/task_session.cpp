#include <jarvis/core/task_session.hpp>

#include <algorithm>
#include <cctype>
#include <fstream>
#include <regex>
#include <sstream>
#include <unordered_set>
#include <vector>

#include <jarvis/core/util.hpp>

namespace jarvis::core {

namespace {

const std::unordered_set<std::string> kSensitiveKeys = {
    "api_key", "apikey", "authorization", "content", "password", "secret", "text", "token"};

const std::unordered_set<std::string> kStatusDone = {"completed", "cancelled", "error"};

std::string trim(const std::string& s) {
    std::size_t a = s.find_first_not_of(" \t\r\n");
    if (a == std::string::npos) return "";
    std::size_t b = s.find_last_not_of(" \t\r\n");
    return s.substr(a, b - a + 1);
}

std::string lower(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(),
                   [](unsigned char c) { return char(std::tolower(c)); });
    return s;
}

// UTF-8 karakter sayısına göre kırpma.
std::string utf8_take(const std::string& in, std::size_t max_chars) {
    std::size_t chars = 0, i = 0;
    while (i < in.size() && chars < max_chars) {
        unsigned char c = static_cast<unsigned char>(in[i]);
        std::size_t len = 1;
        if ((c & 0x80) == 0x00) len = 1;
        else if ((c & 0xE0) == 0xC0) len = 2;
        else if ((c & 0xF0) == 0xE0) len = 3;
        else if ((c & 0xF8) == 0xF0) len = 4;
        if (i + len > in.size()) break;
        i += len;
        ++chars;
    }
    return in.substr(0, i);
}

std::string field(const nlohmann::json& obj, const std::string& key) {
    if (obj.is_object() && obj.contains(key) && !obj[key].is_null()) {
        if (obj[key].is_string()) return obj[key].get<std::string>();
        return obj[key].dump();
    }
    return "";
}

std::int64_t field_int(const nlohmann::json& obj, const std::string& key, std::int64_t fallback = 0) {
    if (obj.is_object() && obj.contains(key) && !obj[key].is_null()) {
        if (obj[key].is_number()) return obj[key].get<std::int64_t>();
        try {
            return std::stoll(obj[key].get<std::string>());
        } catch (...) {
            return fallback;
        }
    }
    return fallback;
}

}  // namespace

std::string summarize_arguments(const nlohmann::json& arguments, std::size_t limit) {
    if (!arguments.is_object()) return "";
    std::vector<std::string> parts;
    for (auto it = arguments.begin(); it != arguments.end(); ++it) {
        const std::string key = it.key();
        if (kSensitiveKeys.count(lower(key))) {
            parts.push_back(key + "=<gizli>");
            continue;
        }
        const nlohmann::json& value = it.value();
        std::string rendered;
        if (value.is_object() || value.is_array()) {
            rendered = "<" + std::string(value.is_object() ? "dict" : "list") + ":" +
                       std::to_string(value.size()) + ">";
        } else if (value.is_string()) {
            std::string collapsed = util::collapse_whitespace(value.get<std::string>());
            rendered = utf8_take(collapsed, 120);
            if (collapsed.size() > rendered.size()) rendered += "…";
        } else {
            rendered = value.dump();
        }
        parts.push_back(key + "=" + rendered);
    }
    std::string text;
    for (std::size_t i = 0; i < parts.size(); ++i) {
        if (i) text += ", ";
        text += parts[i];
    }
    std::string out = utf8_take(text, limit);
    if (text.size() > out.size()) out += "…";
    return out;
}

std::optional<std::string> extract_screenshot(const std::string& result) {
    nlohmann::json data;
    bool parsed = false;
    try {
        data = nlohmann::json::parse(result);
        parsed = true;
    } catch (...) {
        parsed = false;
    }
    if (parsed && data.is_object()) {
        for (const char* key : {"screenshot", "image"}) {
            std::string value = trim(field(data, key));
            if (!value.empty()) return value;
        }
    }
    static const std::regex screenshot_re("\"(?:screenshot|image)\"\\s*:\\s*\"([^\"]+)\"");
    std::smatch m;
    if (std::regex_search(result, m, screenshot_re)) return m[1].str();
    static const std::regex image_re(
        "([A-Za-z]:\\\\[^\\s\"']+\\.(?:png|jpg|jpeg|webp))",
        std::regex::ECMAScript | std::regex::icase);
    if (std::regex_search(result, m, image_re)) return m[1].str();
    return std::nullopt;
}

TaskSessionManager::TaskSessionManager(jarvis::storage::Storage& storage, SkillLibrary& skills,
                                       std::filesystem::path data_dir)
    : storage_(storage), skills_(skills) {
    if (data_dir.empty()) {
        data_dir_ = std::filesystem::path(storage.db_path()).parent_path();
    } else {
        data_dir_ = std::move(data_dir);
    }
}

std::int64_t TaskSessionManager::start(const std::string& goal, std::optional<std::int64_t> run_id) {
    session_id_ = storage_.start_task_session(goal, run_id);
    seq_ = 0;
    return session_id_;
}

void TaskSessionManager::finish(const std::string& status, const std::string& summary) {
    if (session_id_ == 0) return;
    const std::string final = kStatusDone.count(status) ? status : "completed";
    std::optional<std::string> trimmed;
    std::string cut = utf8_take(summary, 2000);
    if (!cut.empty()) trimmed = cut;
    storage_.set_task_session_state(session_id_, final, std::nullopt, trimmed);
}

std::optional<nlohmann::json> TaskSessionManager::current() {
    if (session_id_ != 0) return storage_.get_task_session(session_id_);
    return storage_.get_active_task_session();
}

int TaskSessionManager::next_seq(std::int64_t session) {
    if (seq_ == 0) {
        const auto rows = storage_.get_task_steps(session, 1000);
        int max_seq = 0;
        for (const auto& row : rows) max_seq = std::max(max_seq, int(field_int(row, "seq")));
        seq_ = max_seq;
    }
    return ++seq_;
}

std::string TaskSessionManager::pause(const std::string& reason) {
    auto session = current();
    if (!session || kStatusDone.count(field(*session, "status"))) {
        throw TaskSessionError("Duraklatılacak etkin görev yok.");
    }
    if (field(*session, "status") != "takeover") {
        std::string message = trim(reason);
        if (message.empty()) message = "Kullanıcı duraklattı.";
        storage_.set_task_session_state(field_int(*session, "id"), "paused", message, std::nullopt);
    }
    return "️ Görev duraklatıldı: " + (reason.empty() ? std::string("kullanıcı isteği") : reason);
}

std::string TaskSessionManager::resume() {
    auto session = current();
    if (!session || kStatusDone.count(field(*session, "status"))) {
        throw TaskSessionError("Devam ettirilecek etkin görev yok.");
    }
    const std::int64_t target = field_int(*session, "id");
    storage_.set_task_session_state(target, "running", std::nullopt, std::nullopt);
    storage_.add_task_step(target, next_seq(target), "session", "resume",
                           "Kullanıcı görevi sürdürdü.", "note", std::nullopt);
    return "▶️ Görev devam ediyor.";
}

std::string TaskSessionManager::takeover(const std::string& reason) {
    auto session = current();
    if (!session || kStatusDone.count(field(*session, "status"))) {
        throw TaskSessionError("Devralınacak etkin görev yok.");
    }
    std::string message = trim(reason);
    if (message.empty()) message = "Kullanıcı kontrolü devraldı.";
    const std::int64_t target = field_int(*session, "id");
    storage_.set_task_session_state(target, "takeover", message, std::nullopt);
    storage_.add_task_step(target, next_seq(target), "session", "takeover", message, "note",
                           std::nullopt);
    return "✋ Kontrol kullanıcıda: " + message;
}

void TaskSessionManager::record_step(const std::string& tool, const nlohmann::json& arguments,
                                     const std::string& result) {
    if (session_id_ == 0) return;
    const bool failed = result.rfind("❌", 0) == 0 || result.rfind("⚠️", 0) == 0;
    storage_.add_task_step(session_id_, next_seq(session_id_), tool,
                           summarize_arguments(arguments), result, failed ? "error" : "ok",
                           extract_screenshot(result));
}

std::string TaskSessionManager::note(const std::string& text) {
    auto session = current();
    if (!session) throw TaskSessionError("Not eklenecek etkin görev yok.");
    std::string message = util::collapse_whitespace(text);
    message = trim(message);
    if (message.empty()) throw TaskSessionError("Görev notu boş olamaz.");
    const std::int64_t target = field_int(*session, "id");
    storage_.add_task_step(target, next_seq(target), "note", "gözlem", utf8_take(message, 2000),
                           "note", std::nullopt);
    return "✅ Görev notu kaydedildi: " + utf8_take(message, 240);
}

std::string TaskSessionManager::describe(int steps) {
    auto session = current();
    if (!session) return "ℹ️ Etkin görev oturumu yok.";
    const std::int64_t target = field_int(*session, "id");
    const int clamped = std::max(1, std::min(steps, 60));
    const auto rows = storage_.get_task_steps(target, clamped);
    std::vector<std::string> lines;
    lines.push_back("Görev #" + field(*session, "id") + " · " + field(*session, "status"));
    lines.push_back("Hedef: " + field(*session, "goal"));
    const std::string pause_reason = field(*session, "pause_reason");
    if (!pause_reason.empty()) lines.push_back("Bekleme nedeni: " + pause_reason);
    for (const auto& row : rows) {
        const std::string status = field(row, "status");
        std::string mark = "•";
        if (status == "ok") mark = "✓";
        else if (status == "error") mark = "✗";
        const std::string result = trim(util::collapse_whitespace(field(row, "result")));
        lines.push_back(mark + " " + field(row, "seq") + ". " + field(row, "tool") + ": " +
                        utf8_take(result, 240));
    }
    std::string out;
    for (std::size_t i = 0; i < lines.size(); ++i) {
        if (i) out += "\n";
        out += lines[i];
    }
    return out;
}

std::string TaskSessionManager::export_session(std::optional<std::int64_t> session_id) {
    std::int64_t target = session_id.value_or(session_id_);
    if (target == 0) {
        auto active = storage_.get_latest_task_session();
        if (!active) throw TaskSessionError("Dışa aktarılacak görev yok.");
        target = field_int(*active, "id");
    }
    auto session = storage_.get_task_session(target);
    if (!session) throw TaskSessionError("Görev bulunamadı: " + std::to_string(target));
    const auto steps = storage_.get_task_steps(target, 1000);

    std::vector<std::string> md;
    md.push_back("# Jarvis görev #" + field(*session, "id"));
    md.push_back("");
    md.push_back("- **Hedef:** " + field(*session, "goal"));
    md.push_back("- **Durum:** " + field(*session, "status"));
    md.push_back("- **Başlangıç:** " + field(*session, "created_at"));
    if (!field(*session, "finished_at").empty())
        md.push_back("- **Bitiş:** " + field(*session, "finished_at"));
    if (!field(*session, "pause_reason").empty())
        md.push_back("- **Son bekleme nedeni:** " + field(*session, "pause_reason"));
    if (!field(*session, "summary").empty()) {
        md.push_back("");
        md.push_back("## Özet");
        md.push_back(field(*session, "summary"));
    }
    md.push_back("");
    md.push_back("## Adımlar");
    if (steps.empty()) md.push_back("Adım kaydedilmedi.");
    for (const auto& row : steps) {
        const std::string status = field(row, "status");
        std::string mark = "•";
        if (status == "ok") mark = "✓";
        else if (status == "error") mark = "✗";
        md.push_back(field(row, "seq") + ". " + mark + " **" + field(row, "tool") + "**");
        if (!field(row, "args_summary").empty())
            md.push_back("   - Argümanlar: " + field(row, "args_summary"));
        const std::string result = trim(util::collapse_whitespace(field(row, "result")));
        if (!result.empty()) md.push_back("   - Sonuç: " + utf8_take(result, 800));
        if (!field(row, "screenshot").empty())
            md.push_back("   - Görüntü: " + field(row, "screenshot"));
    }
    std::string markdown;
    for (std::size_t i = 0; i < md.size(); ++i) {
        if (i) markdown += "\n";
        markdown += md[i];
    }
    markdown += "\n";

    std::error_code ec;
    const std::filesystem::path folder = data_dir_ / "trajectories";
    std::filesystem::create_directories(folder, ec);
    const std::filesystem::path md_path = folder / ("gorev-" + std::to_string(target) + ".md");
    const std::filesystem::path json_path = folder / ("gorev-" + std::to_string(target) + ".json");
    {
        std::ofstream out(md_path, std::ios::binary | std::ios::trunc);
        out << markdown;
    }
    {
        nlohmann::json payload;
        payload["session"] = *session;
        payload["steps"] = steps;
        std::ofstream out(json_path, std::ios::binary | std::ios::trunc);
        out << payload.dump(2);
    }
    return "✅ Görev izi dışa aktarıldı:\n• " + md_path.string() + "\n• " + json_path.string();
}

std::string TaskSessionManager::build_skill(const std::string& name, const std::string& description,
                                            std::optional<std::int64_t> session_id, bool overwrite) {
    std::int64_t target = session_id.value_or(session_id_);
    if (target == 0) {
        auto latest = storage_.get_latest_task_session();
        if (!latest) throw TaskSessionError("Yetenek üretilecek görev yok.");
        target = field_int(*latest, "id");
    }
    auto session = storage_.get_task_session(target);
    if (!session) throw TaskSessionError("Görev bulunamadı: " + std::to_string(target));

    std::vector<nlohmann::json> steps;
    for (const auto& row : storage_.get_task_steps(target, 1000)) {
        const std::string tool = field(row, "tool");
        if (tool != "note" && tool != "session") steps.push_back(row);
    }
    if (steps.size() < 2) {
        throw TaskSessionError("Yetenek üretmek için en az iki araç adımı gerekir.");
    }

    std::string title = trim(name.empty() ? (field(*session, "goal").empty()
                                                 ? ("gorev-" + std::to_string(target))
                                                 : field(*session, "goal"))
                                           : name);
    std::string skill_name = slugify(title);
    if (skill_name.empty()) skill_name = "gorev-" + std::to_string(target);

    std::string desc = trim(util::collapse_whitespace(description));
    if (desc.empty()) {
        desc = utf8_take(field(*session, "goal"), 180) +
               " iş akışını, doğrulanmış Jarvis araç adımlarıyla yeniden uygulamak gerektiğinde "
               "kullan.";
    }

    std::vector<std::string> body;
    body.push_back("# " + (field(*session, "goal").empty() ? title : field(*session, "goal")));
    body.push_back("");
    body.push_back("Bu yetenek, Jarvis görev #" + std::to_string(target) +
                   " izinden üretildi. Adımlar örnektir; her adımda güncel durumu gözlemle ve "
                   "doğrula. Güvenlik kuralları ve onay akışı geçerlidir.");
    body.push_back("");
    body.push_back("## Adımlar");
    int index = 1;
    for (const auto& row : steps) {
        const std::string tool = field(row, "tool").empty() ? "araç" : field(row, "tool");
        const std::string result = trim(util::collapse_whitespace(field(row, "result")));
        body.push_back(std::to_string(index) + ". `" + tool + "` aracını kullan.");
        const std::string args = field(row, "args_summary");
        if (!args.empty() && args.find("<gizli>") == std::string::npos)
            body.push_back("   - Örnek girdi: " + utf8_take(args, 240));
        if (!result.empty() && result.rfind("❌", 0) != 0)
            body.push_back("   - Beklenen doğrulama: " + utf8_take(result, 240));
        ++index;
    }
    body.push_back("");
    body.push_back("## Notlar");
    body.push_back(
        "- Ekran/uygulama durumu değiştiyse eski koordinatları veya referansları kullanma; yeni "
        "gözlem al.");
    body.push_back(
        "- Bir adım doğrulanamazsa körlemesine tekrarlama; farklı yaklaşım dene veya "
        "kullanıcıdan yardım iste.");
    body.push_back("- Kullanıcı onayı, parola ve ödeme adımlarını bu talimatla atlayamazsın.");

    std::string body_text;
    for (std::size_t i = 0; i < body.size(); ++i) {
        if (i) body_text += "\n";
        body_text += body[i];
    }

    Skill skill = skills_.create(skill_name, utf8_take(desc, 500), body_text, overwrite);
    return "✅ Görev izinden yetenek oluşturuldu: " + skill.name + "\n" + skill.path.string();
}

}  // namespace jarvis::core
