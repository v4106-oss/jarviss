#include "jarvis/ai/safety.hpp"
#include "jarvis/ai/undo.hpp"

#include <algorithm>
#include <cctype>
#include <cstdlib>
#include <cstdio>
#include <ctime>
#include <fstream>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <unordered_set>

#include <regex>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <shlobj.h>
#endif

namespace fs = std::filesystem;

namespace jarvis::ai {

namespace {

std::string ascii_lower(std::string s) {
    for (char& c : s) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return s;
}

std::string trim(const std::string& s) {
    std::size_t a = 0, b = s.size();
    while (a < b && std::isspace(static_cast<unsigned char>(s[a]))) ++a;
    while (b > a && std::isspace(static_cast<unsigned char>(s[b - 1]))) --b;
    return s.substr(a, b - a);
}

// MSVC'nin std::filesystem'i C++20'de u8string() ile char8_t döndürür;
// projenin her yerinde UTF-8 std::string kullanıldığı için burada daraltılır.
std::string from_u8(std::u8string src) {
    std::string out;
    out.reserve(src.size());
    for (char8_t c : src) out.push_back(static_cast<char>(c));
    return out;
}

std::string u8s(const fs::path& p) { return from_u8(p.u8string()); }

std::string u8s_generic(const fs::path& p) { return from_u8(p.generic_u8string()); }

// os.path.normcase karşılığı: küçük harf + bölücüleri Windows ayracına çevir.
std::string normcase(const fs::path& p) {
    std::string s = u8s_generic(p);
    std::replace(s.begin(), s.end(), '/', '\\');
    return ascii_lower(s);
}

std::string strip_trailing_sep(std::string s) {
    while (s.size() > 1 && (s.back() == '\\' || s.back() == '/')) s.pop_back();
    return s;
}

// os.path.realpath benzeri: absolutlaştır + normalize et; symlink varsa çöz.
fs::path real_path(const fs::path& candidate) {
    fs::path abs = candidate;
    if (!abs.is_absolute()) {
        std::error_code ec;
        abs = fs::current_path(ec) / abs;
    }
    abs = abs.lexically_normal();
    std::error_code ec;
    fs::path canon = fs::canonical(abs, ec);
    if (!ec) return canon;
    return abs;
}

// UTF-8 metinden fs::path üretir: MSVC dar dizeyi yerel kod sayfası sanır,
// Türkçe klasör adları ancak geniş karakterle doğru taşınır.
fs::path path_from_utf8(const std::string& utf8_text) {
#ifdef _WIN32
    if (utf8_text.empty()) return {};
    int n = MultiByteToWideChar(CP_UTF8, 0, utf8_text.data(), static_cast<int>(utf8_text.size()),
                                nullptr, 0);
    std::wstring w(n, 0);
    MultiByteToWideChar(CP_UTF8, 0, utf8_text.data(), static_cast<int>(utf8_text.size()), w.data(), n);
    return fs::path(w);
#else
    return fs::path(utf8_text);
#endif
}

fs::path user_home();

fs::path expand_user(const std::string& text) {
    fs::path p = path_from_utf8(text);
    if (!text.empty() && text[0] == '~') {
        std::string home;
#ifdef _WIN32
        if (const char* up = std::getenv("USERPROFILE")) home = up;
#endif
        if (home.empty()) {
            std::error_code ec;
            home = u8s(user_home());
        }
        if (!home.empty()) {
            if (text.size() == 1) return fs::path(home);
            std::string rest = text.substr(1);
            if (!rest.empty() && (rest[0] == '/' || rest[0] == '\\')) rest.erase(0, 1);
            return fs::path(home) / path_from_utf8(rest);
        }
    }
    return p;
}

bool glob_match(const std::string& name, const std::string& pattern) {
    // Yalnızca '*' wildcard desteklenir (SECRET_PATTERNS için yeterli).
    std::size_t n = 0, p = 0, star = std::string::npos, mark = 0;
    while (n < name.size()) {
        if (p < pattern.size() && (pattern[p] == name[n])) { ++n; ++p; }
        else if (p < pattern.size() && pattern[p] == '*') { star = p++; mark = n; }
        else if (star != std::string::npos) { p = star + 1; n = ++mark; }
        else return false;
    }
    while (p < pattern.size() && pattern[p] == '*') ++p;
    return p == pattern.size();
}

// json argümanlarına güvenli erişim.
std::string arg_str(const nlohmann::json& args, const std::string& key) {
    if (!args.is_object() || !args.contains(key) || args[key].is_null()) return "";
    const nlohmann::json& v = args[key];
    if (v.is_string()) return v.get<std::string>();
    if (v.is_number_integer()) return std::to_string(v.get<long long>());
    if (v.is_number_float()) { std::ostringstream os; os << v.get<double>(); return os.str(); }
    if (v.is_boolean()) return v.get<bool>() ? "true" : "false";
    return v.dump();
}

bool arg_present(const nlohmann::json& args, const std::string& key) {
    return args.is_object() && args.contains(key) && !args[key].is_null();
}

bool arg_true(const nlohmann::json& args, const std::string& key) {
    if (!arg_present(args, key)) return false;
    const nlohmann::json& v = args[key];
    if (v.is_boolean()) return v.get<bool>();
    if (v.is_number()) return v.get<double>() != 0;
    if (v.is_string()) { std::string s = ascii_lower(trim(v.get<std::string>())); return s == "true" || s == "1" || s == "yes"; }
    return false;
}

fs::path user_home() {
#ifdef _WIN32
    if (const char* up = std::getenv("USERPROFILE")) return fs::path(up);
    const char* drive = std::getenv("HOMEDRIVE");
    const char* leaf = std::getenv("HOMEPATH");
    if (drive && leaf) return fs::path(std::string(drive) + leaf);
#else
    if (const char* home = std::getenv("HOME")) return fs::path(home);
#endif
    return {};
}

#ifdef _WIN32
fs::path known_folder(DWORD data1, WORD data2, WORD data3, const BYTE data4[8]) {
    GUID id{data1, data2, data3, {data4[0], data4[1], data4[2], data4[3], data4[4], data4[5], data4[6], data4[7]}};
    PWSTR path = nullptr;
    if (SUCCEEDED(SHGetKnownFolderPath(id, 0, nullptr, &path)) && path) {
        fs::path result(path);
        CoTaskMemFree(path);
        return result;
    }
    if (path) CoTaskMemFree(path);
    return {};
}
#endif

std::string url_host(const std::string& raw) {
    std::string url = raw;
    auto scheme = url.find("://");
    std::string rest = (scheme == std::string::npos) ? url : url.substr(scheme + 3);
    // authority = host[:port] (userinfo remove)
    std::size_t end = rest.find_first_of("/?#");
    std::string authority = rest.substr(0, end);
    std::size_t at = authority.find_last_of('@');
    if (at != std::string::npos) authority = authority.substr(at + 1);
    std::size_t colon = authority.find_last_of(':');
    if (colon != std::string::npos && authority.find(']') == std::string::npos)
        authority = authority.substr(0, colon);
    return ascii_lower(authority);
}

}  // namespace

KillSwitch& global_kill() {
    static KillSwitch kill;
    return kill;
}

const char* risk_label(Risk risk) {
    switch (risk) {
        case Risk::Read: return "okuma";
        case Risk::Create: return "oluşturma";
        case Risk::Modify: return "değiştirme";
        case Risk::Dangerous: return "riskli işlem";
        case Risk::Forbidden: return "yasak";
    }
    return "?";
}

namespace access_mode {
std::string normalize(const std::string& value) {
    if (value == kApproval || value == kStep || value == kFull) return value;
    return kApproval;
}
}  // namespace access_mode

std::vector<fs::path> default_roots(const fs::path& home_in) {
    fs::path home = home_in.empty() ? user_home() : home_in;
    std::vector<fs::path> roots;
    auto add = [&](const fs::path& p) {
        if (p.empty()) return;
        for (const auto& r : roots)
            if (normcase(r) == normcase(p)) return;
        roots.push_back(p);
    };
#ifdef _WIN32
    const BYTE desktop[8] = {0xB0, 0x29, 0x7F, 0xE9, 0x9A, 0x87, 0xC6, 0x41};
    const BYTE documents[8] = {0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7};
    add(known_folder(0xB4BFCC3A, 0xDB2C, 0x424C, desktop));
    add(home / "Desktop");
    add(known_folder(0xFDD39AD0, 0x238F, 0x46AF, documents));
    add(home / "Documents");
#else
    add(home / "Desktop");
    add(home / "Documents");
#endif
    return roots;
}

bool is_within(const fs::path& child, const fs::path& parent) {
    std::string c = strip_trailing_sep(normcase(child));
    std::string p = strip_trailing_sep(normcase(parent));
    if (c == p) return true;
    if (c.size() > p.size() && c.compare(0, p.size(), p) == 0) {
        return c[p.size()] == '\\' || c[p.size()] == '/';
    }
    return false;
}

// --- PathPolicy ---
namespace {
const std::set<std::string> kSecretDirs = {".ssh", ".aws", ".gnupg", ".kube", ".azure"};
const std::vector<std::string> kSecretPatterns = {
    ".env", ".env.*", "*.pem", "*.key", "*.pfx", "*.p12", "*.ppk", "*.kdbx",
    "id_rsa*", "id_ed25519*", "id_ecdsa*", "id_dsa*"};
const std::vector<std::string> kExecutableExt = {
    ".exe", ".bat", ".cmd", ".com", ".scr", ".msi", ".msp", ".ps1", ".psm1", ".vbs", ".vbe",
    ".js", ".jse", ".wsf", ".wsh", ".hta", ".lnk", ".reg", ".jar", ".cpl", ".dll", ".sys",
    ".pif", ".url", ".appref-ms", ".py", ".pyw"};

bool is_reserved_name(const std::string& part) {
    static const std::set<std::string> reserved = [] {
        std::set<std::string> r = {"CON", "PRN", "AUX", "NUL"};
        for (int i = 1; i < 10; ++i) { r.insert("COM" + std::to_string(i)); r.insert("LPT" + std::to_string(i)); }
        return r;
    }();
    std::string head = part.substr(0, part.find('.'));
    for (char& c : head) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
    return reserved.count(head) > 0;
}

std::vector<std::string> path_components(const fs::path& p) {
    // anchor (root_name + root_directory) hariç bileşenler.
    std::vector<std::string> out;
    std::string root_name = u8s(p.root_name());
    std::string root_dir = u8s(p.root_directory());
    for (const auto& c : p) {
        std::string s = u8s(c);
        if (!root_name.empty() && s == root_name) continue;
        if (!root_dir.empty() && s == root_dir) continue;
        out.push_back(s);
    }
    return out;
}
}  // namespace

PathPolicy::PathPolicy(const std::vector<fs::path>& roots,
                       const std::vector<std::pair<fs::path, std::string>>& forbidden,
                       const fs::path& base) {
    for (const auto& r : roots) roots_.push_back(real_path(r));
    for (const auto& f : forbidden) forbidden_.emplace_back(real_path(f.first), f.second);
    if (!base.empty())
        base_ = real_path(base);
    else if (!roots_.empty())
        base_ = roots_.front();
    else
        base_ = user_home();
}

fs::path PathPolicy::resolve(const std::string& raw) const {
    std::string text = raw;
    if (trim(text).empty() || text.find('\0') != std::string::npos)
        throw PathNotAllowed("Geçersiz veya boş dosya yolu.");
    if (text.rfind("\\\\", 0) == 0 || text.rfind("//", 0) == 0)
        throw PathNotAllowed("Ağ yolları (\\\\sunucu\\paylaşım) kullanılamaz.");
    std::string remainder = (text.size() > 1 && text[1] == ':') ? text.substr(2) : text;
    if (remainder.find(':') != std::string::npos)
        throw PathNotAllowed("Dosya adında ':' kullanılamaz (alternatif veri akışı engellendi).");

    fs::path candidate = expand_user(text);
    for (const std::string& part : path_components(candidate)) {
        if (part != "." && part != ".." &&
            (part.back() == '.' || part.back() == ' '))
            throw PathNotAllowed("Sonu nokta veya boşlukla biten ad kullanılamaz: " + part);
        if (is_reserved_name(part))
            throw PathNotAllowed("Windows'ta ayrılmış ad kullanılamaz: " + part);
    }

    if (!candidate.is_absolute()) candidate = base_ / candidate;
    fs::path real = real_path(candidate);

    for (const auto& zone : forbidden_)
        if (is_within(real, zone.first))
            throw PathNotAllowed("Bu konum korunuyor (" + zone.second + "): " + u8s(real));
    bool in_root = false;
    for (const auto& r : roots_)
        if (is_within(real, r)) { in_root = true; break; }
    if (!in_root) {
        std::string names;
        for (size_t i = 0; i < roots_.size(); ++i) {
            if (i) names += ", ";
            names += u8s(roots_[i]);
        }
        throw PathNotAllowed("Güvenlik nedeniyle yalnızca izinli klasörler kullanılabilir (" + names + ").");
    }
    for (const std::string& part : path_components(real))
        if (kSecretDirs.count(ascii_lower(part)))
            throw PathNotAllowed("Anahtar/kimlik klasörlerine erişim yasak: " + u8s(real));
    std::string name_lower = ascii_lower(u8s(real.filename()));
    for (const auto& pat : kSecretPatterns)
        if (glob_match(name_lower, pat))
            throw PathNotAllowed("Anahtar veya gizli bilgi dosyalarına erişim yasak: " + u8s(real.filename()));
    return real;
}

bool PathPolicy::is_protected_root(const fs::path& path) const {
    if (path.parent_path() == path) return true;
    for (const auto& r : roots_)
        if (normcase(path) == normcase(r)) return true;
    return false;
}

bool PathPolicy::is_executable(const fs::path& path) const {
    std::string ext = ascii_lower(u8s(path.extension()));
    return std::find(kExecutableExt.begin(), kExecutableExt.end(), ext) != kExecutableExt.end();
}

bool PathPolicy::secret_dir_or_file(const std::string& name_lower) {
    if (kSecretDirs.count(name_lower)) return true;
    for (const auto& pat : kSecretPatterns)
        if (glob_match(name_lower, pat)) return true;
    return false;
}

// --- PermissionGate ---
PermissionGate::PermissionGate(ModeFn mode, StrictFn strict_after_web)
    : mode_(std::move(mode)), strict_after_web_(std::move(strict_after_web)) {}

Decision PermissionGate::decide(Risk risk, bool tainted) const {
    if (static_cast<int>(risk) >= static_cast<int>(Risk::Forbidden))
        return {"deny", "Bu işlem yasak listesinde."};
    std::string mode = access_mode::normalize(mode_ ? mode_() : std::string(access_mode::kApproval));
    bool strict = strict_after_web_ ? strict_after_web_() : true;
    const std::string web_note =
        "İnternetten okunan içerikten sonra gelen işlem için onay gerekli.";
    if (mode == access_mode::kFull) {
        if (static_cast<int>(risk) >= static_cast<int>(Risk::Modify) && tainted && strict)
            return {"ask", web_note};
        return {"allow", ""};
    }
    if (risk == Risk::Read) return {"allow", ""};
    if (mode == access_mode::kStep)
        return {"ask", "Adım adım modu: her oluşturma/değiştirme/riskli işlem için onay gerekli."};
    if (tainted) return {"ask", web_note};
    if (risk == Risk::Create) return {"allow", ""};
    if (risk == Risk::Modify) return {"ask", "Var olan dosya veya klasör değişecek."};
    return {"ask", "Riskli işlem (silme / komut)."};
}

// --- AuditLog ---
namespace {
const std::vector<std::string> kSensitiveKeys = {"key", "token", "password", "secret", "authorization"};

bool key_sensitive(const std::string& key) {
    std::string l = ascii_lower(key);
    for (const auto& w : kSensitiveKeys)
        if (l.find(w) != std::string::npos) return true;
    return false;
}

std::string now_iso() {
    // Yerel saat, saniye hassasiyetinde ISO-8601.
    std::time_t t = std::time(nullptr);
    std::tm tm{};
#ifdef _WIN32
    localtime_s(&tm, &t);
#else
    localtime_r(&t, &tm);
#endif
    char buf[32];
    std::strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%S", &tm);
    return buf;
}

nlohmann::json clean_value(const nlohmann::json& value, const std::string& key) {
    if (key_sensitive(key)) return "***";
    if (key == "content") {
        nlohmann::json o;
        o["chars"] = value.is_null() ? 0 : static_cast<int>(value.is_string() ? value.get<std::string>().size() : value.dump().size());
        return o;
    }
    if (value.is_object()) {
        nlohmann::json out = nlohmann::json::object();
        for (auto it = value.begin(); it != value.end(); ++it)
            out[it.key()] = clean_value(it.value(), it.key());
        return out;
    }
    if (value.is_array()) {
        nlohmann::json out = nlohmann::json::array();
        int taken = 0;
        for (const auto& v : value) {
            if (taken++ >= 50) break;
            out.push_back(clean_value(v, ""));
        }
        return out;
    }
    if (value.is_string()) {
        std::string s = value.get<std::string>();
        if (s.size() <= 300) return s;
        // utf8_truncate yerine byte-300 + karakter yerine üç nokta (günlük özeti).
        return s.substr(0, 300) + "…";
    }
    return value;
}
}  // namespace

AuditLog::AuditLog(const fs::path& path, long long max_bytes) : path_(path), max_bytes_(max_bytes) {}

void AuditLog::record(const std::string& tool, const nlohmann::json& args, const nlohmann::json& extra) {
    try {
        nlohmann::json entry;
        entry["ts"] = now_iso();
        entry["tool"] = tool;
        if (!args.is_null() && !args.empty()) entry["args"] = clean_value(args, "");
        if (extra.is_object())
            for (auto it = extra.begin(); it != extra.end(); ++it)
                entry[it.key()] = clean_value(it.value(), it.key());

        std::error_code ec;
        fs::create_directories(path_.parent_path(), ec);
        if (fs::exists(path_, ec) && static_cast<long long>(fs::file_size(path_, ec)) > max_bytes_) {
            fs::path rotated = path_;
            rotated.replace_extension(".1.jsonl");
            fs::rename(path_, rotated, ec);
        }
        std::ofstream out(path_, std::ios::app | std::ios::binary);
        if (out) out << entry.dump() << "\n";
    } catch (...) {
        // günlük hatası asıl işlemi bozmamalı
    }
}

std::vector<nlohmann::json> AuditLog::tail(int count) const {
    std::vector<nlohmann::json> out;
    std::ifstream in(path_, std::ios::binary);
    if (!in) return out;
    std::vector<std::string> lines;
    std::string line;
    while (std::getline(in, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (!line.empty()) lines.push_back(line);
    }
    int start = std::max<int>(0, static_cast<int>(lines.size()) - count);
    for (int i = start; i < static_cast<int>(lines.size()); ++i) {
        try { out.push_back(nlohmann::json::parse(lines[i])); } catch (...) { continue; }
    }
    return out;
}

// --- classify_action ---
Risk classify_action(const std::string& action_type, const nlohmann::json& arguments,
                     const PathPolicy& policy) {
    static const std::map<std::string, std::vector<std::string>> kPathKeys = {
        {"create_folder", {"path"}}, {"write_text_file", {"path"}}, {"append_text_file", {"path"}},
        {"copy_path", {"source", "destination"}}, {"move_path", {"source", "destination"}},
        {"delete_path", {"path"}}, {"open_path", {"path"}}, {"inspect_image", {"path"}},
        {"save_file_dialog", {"path"}}, {"list_project_files", {"path"}}, {"read_project_file", {"path"}},
        {"search_project", {"path"}}, {"preview_edit", {"path"}}, {"replace_exact", {"path"}},
        {"download_file", {"path"}}, {"run_tests", {"working_directory"}},
        {"prepare_git_branch", {"working_directory"}}, {"diff_self_workspace", {"path"}},
        {"apply_self_changes", {"path"}}};

    std::map<std::string, fs::path> paths;
    auto pk = kPathKeys.find(action_type);
    if (pk != kPathKeys.end()) {
        for (const auto& key : pk->second) {
            if (!arg_present(arguments, key))
                throw ActionForbidden("'" + action_type + "' için '" + key + "' bilgisi eksik.");
            paths[key] = policy.resolve(arg_str(arguments, key));
        }
    }

    std::string a;  // action alt-değeri (birden fazla dalda kullanılır)

    if (action_type == "create_folder") return Risk::Create;
    if (action_type == "write_text_file")
        return (fs::exists(paths["path"]) && arg_true(arguments, "overwrite")) ? Risk::Modify : Risk::Create;
    if (action_type == "append_text_file")
        return fs::exists(paths["path"]) ? Risk::Modify : Risk::Create;
    if (action_type == "save_file_dialog")
        return fs::exists(paths["path"]) ? Risk::Modify : Risk::Create;
    if (action_type == "copy_path") return Risk::Create;
    if (action_type == "move_path") {
        if (policy.is_protected_root(paths["source"]))
            throw ActionForbidden("İzinli kök klasör taşınamaz: " + u8s(paths["source"]));
        return Risk::Modify;
    }
    if (action_type == "delete_path") {
        if (policy.is_protected_root(paths["path"]))
            throw ActionForbidden("İzinli kök klasör silinemez: " + u8s(paths["path"]));
        return Risk::Dangerous;
    }
    if (action_type == "open_path") {
        if (policy.is_executable(paths["path"]))
            throw ActionForbidden("Çalıştırılabilir veya betik dosyaları bu araçla açılamaz (" +
                                  u8s(paths["path"].extension()) + ").");
        return Risk::Create;
    }
    if (action_type == "browser_open") {
        std::string url = trim(arg_str(arguments, "url"));
        // ECMAScript regex '(?i)' kabul etmez — MSVC bunu başta duran bir tekrar sayar ve regex_error atar.
        static const std::regex url_re("^https?://\\S+$", std::regex_constants::icase);
        if (!std::regex_match(url, url_re))
            throw ActionForbidden("browser_open yalnızca http/https adresi kabul eder.");
        return Risk::Create;
    }
    if (action_type == "youtube") {
        a = ascii_lower(trim(arg_str(arguments, "action")));
        if (a.empty()) a = "open";
        if (a == "latest" || a == "highlights") {
            std::string channel = trim(arg_str(arguments, "channel"));
            if (channel.empty()) channel = trim(arg_str(arguments, "query"));
            if (channel.empty()) channel = trim(arg_str(arguments, "url"));
            if (channel.empty()) throw ActionForbidden("YouTube akışı için channel veya query gerekli.");
            if (channel.size() > 300) throw ActionForbidden("YouTube kanal bilgisi çok uzun.");
            if (channel.find("://") != std::string::npos) {
                static const std::set<std::string> yt = {"youtube.com", "www.youtube.com", "m.youtube.com",
                                                          "music.youtube.com", "youtu.be", "www.youtu.be"};
                if (!yt.count(url_host(channel)))
                    throw ActionForbidden("YouTube kanalı için yalnızca youtube.com bağlantısı kullanılabilir.");
            }
            return Risk::Read;
        }
        if (a != "open") throw ActionForbidden("YouTube için action: open, latest veya highlights olmalı.");
        std::string url = trim(arg_str(arguments, "url"));
        std::string query = trim(arg_str(arguments, "query"));
        if (url.empty() && query.empty()) throw ActionForbidden("YouTube için query veya url gerekli.");
        if (!url.empty()) {
            std::string probe = url.find("://") != std::string::npos ? url : "https://" + url;
            auto scheme = probe.find("://");
            std::string sh = ascii_lower(probe.substr(0, scheme));
            static const std::set<std::string> yt = {"youtube.com", "www.youtube.com", "m.youtube.com",
                                                      "music.youtube.com", "youtu.be", "www.youtu.be"};
            if ((sh != "http" && sh != "https") || !yt.count(url_host(probe)))
                throw ActionForbidden("youtube yalnızca youtube.com veya youtu.be bağlantısı kabul eder.");
        }
        return Risk::Create;
    }
    if (action_type == "open_application" || action_type == "open_web_page" || action_type == "search_web" ||
        action_type == "create_project" || action_type == "prepare_self_workspace")
        return Risk::Create;
    if (action_type == "screen_wait") {
        if (trim(arg_str(arguments, "text")).empty() && trim(arg_str(arguments, "window_title")).empty())
            throw ActionForbidden("screen_wait için text veya window_title gerekli.");
        if (arg_str(arguments, "text").size() > 200)
            throw ActionForbidden("screen_wait metni en fazla 200 karakter olabilir.");
        return Risk::Read;
    }
    {
        static const std::set<std::string> read_actions = {
            "inspect_ui", "list_project_files", "read_project_file", "search_project", "preview_edit",
            "diff_self_workspace", "capture_screen", "inspect_image", "screen_map", "check_capabilities",
            "read_page", "read_rss", "browser_read", "news", "markets", "wiki", "weather", "github", "photo"};
        if (read_actions.count(action_type)) return Risk::Read;
    }
    if (action_type == "memory") {
        a = ascii_lower(trim(arg_str(arguments, "action")));
        if (a.empty()) a = "list";
        if (a != "list" && a != "add" && a != "delete")
            throw ActionForbidden("memory için action: list, add veya delete olmalı.");
        if (a == "list") return Risk::Read;
        return a == "add" ? Risk::Create : Risk::Modify;
    }
    if (action_type == "skill") {
        a = ascii_lower(trim(arg_str(arguments, "action")));
        if (a.empty()) a = "list";
        if (a != "list" && a != "read" && a != "create")
            throw ActionForbidden("skill için action: list, read veya create olmalı.");
        if (a == "list" || a == "read") return Risk::Read;
        return Risk::Modify;
    }
    if (action_type == "replace_exact" || action_type == "prepare_git_branch") return Risk::Modify;
    if (action_type == "download_file")
        return (fs::exists(paths["path"]) && arg_true(arguments, "overwrite")) ? Risk::Modify : Risk::Create;
    if (action_type == "steam_workshop") {
        a = ascii_lower(trim(arg_str(arguments, "action")));
        if (a != "search" && a != "open" && a != "status")
            throw ActionForbidden("steam_workshop için geçerli bir action gerekli.");
        return a == "open" ? Risk::Dangerous : Risk::Read;
    }
    if (action_type == "home") {
        a = ascii_lower(trim(arg_str(arguments, "action")));
        static const std::set<std::string> ok = {"status", "light", "all_lights", "coffee_brew",
                                                 "coffee_status", "coffee_cancel"};
        if (!ok.count(a)) throw ActionForbidden("home için geçerli bir action gerekli.");
        if (a == "status" || a == "coffee_status") return Risk::Read;
        return Risk::Create;
    }
    if (action_type == "map") {
        a = ascii_lower(trim(arg_str(arguments, "action")));
        static const std::set<std::string> ok = {"home", "set_home", "route", "nearby", "mall",
                                                 "bus_stops", "bus_stop", "bus_line", "geocode"};
        if (!ok.count(a)) throw ActionForbidden("map için geçerli bir action gerekli.");
        return a == "set_home" ? Risk::Modify : Risk::Read;
    }
    if (action_type == "book") {
        a = ascii_lower(trim(arg_str(arguments, "action")));
        if (a.empty()) a = "lookup";
        static const std::set<std::string> ok = {"open_camera", "lookup", "library", "profile"};
        if (!ok.count(a)) throw ActionForbidden("book için action: open_camera, lookup, library veya profile olmalı.");
        return Risk::Read;
    }
    if (action_type == "personal") {
        std::string resource = ascii_lower(trim(arg_str(arguments, "resource")));
        a = ascii_lower(trim(arg_str(arguments, "action")));
        if (a.empty()) a = "list";
        if (resource != "note" && resource != "reminder" && resource != "event")
            throw ActionForbidden("personal için resource: note, reminder veya event olmalı.");
        if (a != "list" && a != "add" && a != "delete" && a != "complete")
            throw ActionForbidden("personal için geçerli bir action gerekli.");
        if (a == "complete" && resource != "reminder")
            throw ActionForbidden("Yalnızca hatırlatıcı tamamlanabilir.");
        if (a == "list") return Risk::Read;
        return a == "add" ? Risk::Create : Risk::Modify;
    }
    if (action_type == "system_control") {
        a = ascii_lower(trim(arg_str(arguments, "action")));
        static const std::set<std::string> ok = {
            "volume_up", "volume_down", "mute", "media_play_pause", "media_next", "media_previous",
            "media_stop", "lock", "sleep", "shutdown", "restart", "cancel_shutdown"};
        if (!ok.count(a)) throw ActionForbidden("system_control için geçerli bir action gerekli.");
        if (a == "shutdown" || a == "restart") {
            if (!arg_true(arguments, "confirm"))
                throw ActionForbidden("Kapatma veya yeniden başlatma için confirm=true gerekli.");
            return Risk::Dangerous;
        }
        if (a == "sleep" || a == "lock") return Risk::Modify;
        return Risk::Create;
    }
    if (action_type == "clipboard") {
        a = ascii_lower(trim(arg_str(arguments, "action")));
        if (a.empty()) a = "read";
        if (a != "read" && a != "write" && a != "clear")
            throw ActionForbidden("clipboard için action: read, write veya clear olmalı.");
        return a == "read" ? Risk::Read : Risk::Create;
    }
    if (action_type == "window_control") {
        a = ascii_lower(trim(arg_str(arguments, "action")));
        if (a.empty()) a = "list";
        if (a == "list_windows") a = "list";
        else if (a == "focus_window") a = "focus";
        else if (a == "close_app") a = "close";
        static const std::set<std::string> ok = {"list", "focus", "minimize", "maximize", "restore",
                                                 "move", "resize", "set_bounds", "close"};
        if (!ok.count(a))
            throw ActionForbidden("window_control action: list, focus, minimize, maximize, restore, move, "
                                  "resize, set_bounds veya close olmalı.");
        if (a == "list") return Risk::Read;
        bool has_title = !trim(arg_str(arguments, "title")).empty();
        bool has_handle = arg_present(arguments, "handle") && arg_str(arguments, "handle") != "";
        if (!has_title && !has_handle)
            throw ActionForbidden("window_control için title veya handle gerekli.");
        if (a == "close") return Risk::Dangerous;
        return Risk::Modify;
    }
    if (action_type == "find_text_on_screen")
        return arg_true(arguments, "click") ? Risk::Dangerous : Risk::Read;
    if (action_type == "screen_act") {
        std::string ref = trim(arg_str(arguments, "ref"));
        if (!ref.empty()) {
            if (!std::regex_match(ref, std::regex("^s[0-9]+:e[0-9]+$")))
                throw ActionForbidden("screen_act ref biçimi sN:eM olmalı.");
        } else if (!arg_present(arguments, "element_id")) {
            throw ActionForbidden("screen_act için ref veya geçerli bir element_id gerekli.");
        }
        std::string act = ascii_lower(trim(arg_str(arguments, "action")));
        if (act.empty()) act = "click";
        static const std::set<std::string> ok = {"click", "double_click", "right_click", "type", "focus",
                                                 "set_value", "clear", "toggle", "check", "uncheck", "expand",
                                                 "collapse", "select", "scroll", "scroll_to", "hover", "drag"};
        if (!ok.count(act))
            throw ActionForbidden("screen_act action: click, double_click, right_click, type, focus, set_value, "
                                  "clear, toggle, check, uncheck, expand, collapse, select, scroll, scroll_to, "
                                  "hover veya drag olmalı.");
        if (act == "drag") {
            std::string to_ref = trim(arg_str(arguments, "to_ref"));
            bool has_xy = arg_present(arguments, "to_x") && arg_present(arguments, "to_y");
            if (to_ref.empty() && !has_xy) throw ActionForbidden("drag için to_ref veya to_x/to_y gerekli.");
            if (!to_ref.empty() && !std::regex_match(to_ref, std::regex("^s[0-9]+:e[0-9]+$")))
                throw ActionForbidden("to_ref biçimi sN:eM olmalı.");
        }
        if (arg_str(arguments, "text").size() > 10000) throw ActionForbidden("screen_act metni çok uzun.");
        if (arg_str(arguments, "expect").size() > 200 || arg_str(arguments, "expect_window").size() > 200)
            throw ActionForbidden("screen_act doğrulama metni çok uzun.");
        return Risk::Dangerous;
    }
    if (action_type == "ui_control") {
        a = ascii_lower(trim(arg_str(arguments, "action")));
        if (a.empty()) a = "click";
        static const std::set<std::string> ok = {"click", "type", "focus", "set_value", "clear", "toggle",
                                                 "check", "uncheck", "expand", "collapse", "select", "scroll",
                                                 "scroll_to", "hover"};
        if (!ok.count(a)) throw ActionForbidden("ui_control için geçerli bir action gerekli.");
        if (arg_str(arguments, "text").size() > 10000) throw ActionForbidden("ui_control metni çok uzun.");
        return Risk::Dangerous;
    }
    if (action_type == "send_keys" || action_type == "run_command" || action_type == "run_tests" ||
        action_type == "apply_self_changes" || action_type == "mouse_control" || action_type == "draw_shape" ||
        action_type == "install_dependency" || action_type == "open_system_uri")
        return Risk::Dangerous;
    if (action_type == "undo_last") return Risk::Modify;
    throw ActionForbidden("Tanınmayan işlem türü: " + action_type);
}

SafetyServices build_services(core::Settings& settings, KillSwitch* shared_kill) {
    fs::path data_dir = settings.data_dir();
    std::vector<std::pair<fs::path, std::string>> forbidden;

    // Jarvis'in kendi kod kökü: bu modülün derlendiği proje kökü burada bilinmez;
    // data_dir ve sistem klasörleri korunur, uygulama kökü için CWD bir üstü kullanılır.
    std::error_code ec;
    forbidden.emplace_back(fs::current_path(ec).parent_path(), "Jarvis'in kendi kodu");
    forbidden.emplace_back(data_dir, "Jarvis ayarları, yedekleri ve günlükleri");
#ifdef _WIN32
    for (const char* env : {"WINDIR", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"}) {
        if (const char* v = std::getenv(env)) forbidden.emplace_back(fs::path(v), env);
    }
#endif
    forbidden.emplace_back(user_home() / "AppData", "Uygulama verileri (AppData)");

    std::vector<fs::path> roots = default_roots();
    for (const auto& p : settings.extra_roots()) roots.push_back(path_from_utf8(p));
    std::vector<fs::path> base_roots = default_roots();

    SafetyServices svc;
    svc.policy = PathPolicy(roots, forbidden, base_roots.empty() ? fs::path() : base_roots.front());
    svc.gate = PermissionGate(
        [&settings]() { return settings.access_mode(); },
        [&settings]() { return settings.strict_after_web(); });
    svc.audit = AuditLog(data_dir / "logs" / "audit.jsonl");
    svc.undo = std::make_shared<UndoStore>(data_dir / "undo");
    svc.kill = shared_kill ? shared_kill : &global_kill();
    svc.settings = &settings;
    return svc;
}

}  // namespace jarvis::ai
