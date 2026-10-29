#include "jarvis/ai/agent.hpp"

#include <cstdlib>
#include <cwctype>
#include <regex>
#include <string>
#include <unordered_map>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

namespace fs = std::filesystem;

namespace jarvis::ai {

namespace {

std::wstring ws(const std::string& s) {
#ifdef _WIN32
    if (s.empty()) return {};
    int n = MultiByteToWideChar(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), nullptr, 0);
    std::wstring w(n, 0);
    MultiByteToWideChar(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), w.data(), n);
    return w;
#else
    std::wstring w;
    w.reserve(s.size());
    for (unsigned char c : s) w.push_back(c);
    return w;
#endif
}

std::string su(const std::wstring& w) {
#ifdef _WIN32
    if (w.empty()) return {};
    int n = WideCharToMultiByte(CP_UTF8, 0, w.data(), static_cast<int>(w.size()), nullptr, 0, nullptr, nullptr);
    std::string s(n, 0);
    WideCharToMultiByte(CP_UTF8, 0, w.data(), static_cast<int>(w.size()), s.data(), n, nullptr, nullptr);
    return s;
#else
    std::string s;
    s.reserve(w.size());
    for (wchar_t c : w) s.push_back(static_cast<char>(c));
    return s;
#endif
}

// UTF-8 <-> fs::path köprüleri: MSVC dar dizeleri yerel kod sayfası sanır.
fs::path p8(const std::string& utf8_text) { return fs::path(ws(utf8_text)); }

std::string u8s(const fs::path& p) { return su(p.wstring()); }

std::wstring to_lower(const std::wstring& w) {
    std::wstring out = w;
    for (wchar_t& c : out) c = static_cast<wchar_t>(std::towlower(c));
    return out;
}

bool is_space(wchar_t c) { return std::iswspace(c) != 0; }

std::wstring wstrip_ws(const std::wstring& in) {
    std::size_t a = 0, b = in.size();
    while (a < b && is_space(in[a])) ++a;
    while (b > a && is_space(in[b - 1])) --b;
    return in.substr(a, b - a);
}

std::wstring wstrip_chars(const std::wstring& in, const std::wstring& chars) {
    std::size_t a = 0, b = in.size();
    while (a < b && chars.find(in[a]) != std::wstring::npos) ++a;
    while (b > a && chars.find(in[b - 1]) != std::wstring::npos) --b;
    return in.substr(a, b - a);
}

std::wstring collapse_ws(const std::wstring& in) {
    std::wregex re(L"\\s+");
    return std::regex_replace(in, re, L" ");
}

// std::wregex'te \b yalnızca ASCII harf/rakam tanıdığı için "aç"/"başlat" gibi
// ç,ş içeren kelimelerde sınır hiç bulunur. Python'in Unicode \b davranışı burada elle kurulur.
bool is_word_char(wchar_t c) { return std::iswalnum(c) != 0 || c == L'_'; }

bool has_word(const std::wstring& text, const std::wstring& word) {
    if (word.empty()) return false;
    for (std::size_t at = text.find(word); at != std::wstring::npos; at = text.find(word, at + 1)) {
        bool left_ok = at == 0 || !is_word_char(text[at - 1]);
        std::size_t right = at + word.size();
        bool right_ok = right >= text.size() || !is_word_char(text[right]);
        if (left_ok && right_ok) return true;
    }
    return false;
}

std::filesystem::path user_desktop() {
#ifdef _WIN32
    if (const char* up = std::getenv("USERPROFILE")) return fs::path(up) / "Desktop";
#else
    if (const char* home = std::getenv("HOME")) return fs::path(home) / "Desktop";
#endif
    return {};
}

}  // namespace

std::string ActionPlan::summary() const {
    std::string out;
    int i = 1;
    for (const auto& a : actions) {
        if (i > 1) out += "\n";
        out += std::to_string(i) + ". " + a.description;
        ++i;
    }
    return out;
}

ActionPlanner::ActionPlanner(fs::path desktop) : desktop_(desktop.empty() ? user_desktop() : std::move(desktop)) {}

std::string ActionPlanner::clean_name(const std::string& value) {
    std::wstring w = wstrip_ws(ws(value));
    w = wstrip_chars(w, L" \t\n\r\"'“”");
    w = collapse_ws(w);
    w = wstrip_chars(w, L" .");
    return su(w);
}

std::string ActionPlanner::with_text_extension(const std::string& name) {
    if (!p8(name).extension().empty()) return name;
    return name + ".txt";
}

fs::path ActionPlanner::desktop_path(const std::string& name) const {
    return desktop_ / p8(name);
}

int ActionPlanner::count_of(const std::string& raw) {
    if (raw.empty()) return 1;
    std::string s = su(to_lower(ws(raw)));
    // trim
    std::size_t a = 0, b = s.size();
    while (a < b && std::isspace(static_cast<unsigned char>(s[a]))) ++a;
    while (b > a && std::isspace(static_cast<unsigned char>(s[b - 1]))) --b;
    s = s.substr(a, b - a);
    if (s.empty()) return 1;
    bool all_digits = true;
    for (char c : s) if (!std::isdigit(static_cast<unsigned char>(c))) { all_digits = false; break; }
    if (all_digits) {
        long v = std::stol(s);
        if (v < 1) v = 1;
        if (v > 100) v = 100;
        return static_cast<int>(v);
    }
    static const std::unordered_map<std::string, int> words = {
        {"bir", 1}, {"iki", 2}, {"üç", 3}, {"dört", 4}, {"beş", 5}};
    auto it = words.find(s);
    return it != words.end() ? it->second : 1;
}

std::optional<ActionPlan> ActionPlanner::plan(const std::string& prompt) {
    std::wstring text_w = wstrip_ws(ws(prompt));
    if (text_w.empty()) return std::nullopt;
    std::wstring lower = to_lower(text_w);
    std::string text = su(text_w);

    // geri al
    static const std::wregex undo_re(
        L"(?:(?:son\\s+)?(?:işlemi|değişikliği)|yaptığını|yaptığın\\s+şeyi)?\\s*geri\\s+al[.!]*");
    std::string lower_s = su(lower);
    if (std::regex_match(lower, undo_re) || lower_s == "son işlemi geri al" || lower_s == "geri al") {
        ActionPlan p;
        PlannedAction a;
        a.action_type = "undo_last";
        a.arguments = nlohmann::json::object();
        a.description = "Son değişikliği geri al";
        p.actions.push_back(a);
        return p;
    }

    if (auto cp = plan_folder_and_document(text)) return cp;
    if (auto note = plan_notepad_text(text)) return note;

    static const std::wregex folder_re(
        L"(?:^|\\b)([\\wçğıöşüÇĞİÖŞÜ _.\\-]+?)\\s+ad(?:ı|i)nda\\s+(?:bir\\s+)?(?:klas[oö]r|dizin|folder)\\s+"
        L"(?:oluştur|yarat|aç)",
        std::regex_constants::ECMAScript | std::regex_constants::icase);
    std::wsmatch m;
    if (std::regex_search(text_w, m, folder_re)) {
        std::string name = clean_name(su(m[1].str()));
        if (!name.empty()) {
            ActionPlan p;
            PlannedAction a;
            a.action_type = "create_folder";
            a.arguments = {{"path", u8s(desktop_path(name))}};
            a.description = "Masaüstünde '" + name + "' klasörünü oluştur";
            p.actions.push_back(a);
            return p;
        }
    }

    static const std::wregex file_re(
        L"(?:^|\\b)([\\wçğıöşüÇĞİÖŞÜ _.\\-]+?)\\s+ad(?:ı|i)nda\\s+(?:bir\\s+)?(?:metin belgesi|txt dosyası|dosya)\\s+"
        L"(?:oluştur|yarat)",
        std::regex_constants::ECMAScript | std::regex_constants::icase);
    static const std::wregex file_generic_re(
        L"(?:metin belgesi|txt dosyası)\\s+(?:oluştur|yarat)",
        std::regex_constants::ECMAScript | std::regex_constants::icase);
    if (std::regex_search(text_w, m, file_re) || std::regex_search(lower, file_generic_re)) {
        std::string raw_name = std::regex_search(text_w, m, file_re) ? clean_name(su(m[1].str()))
                                                                    : "yeni_dosya";
        if (raw_name.empty()) raw_name = "yeni_dosya";
        std::string name = with_text_extension(raw_name);
        ActionPlan p;
        PlannedAction a;
        a.action_type = "write_text_file";
        a.arguments = {{"path", u8s(desktop_path(name))}, {"content", ""}, {"overwrite", false}};
        a.description = "Masaüstünde '" + name + "' adlı boş metin belgesini oluştur";
        p.actions.push_back(a);
        return p;
    }

    static const std::wregex search_re(
        L"(?:google(?:'da)?\\s+ara|internette\\s+ara)\\s+(.+)",
        std::regex_constants::ECMAScript | std::regex_constants::icase);
    // Türkçe konuşmada "ara" fiili sonda olur: "google'da <sorgu> ara", "<sorgu> internette ara".
    static const std::wregex search_google_wrap_re(
        L"google(?:'da)?\\s+(.+?)\\s+ara",
        std::regex_constants::ECMAScript | std::regex_constants::icase);
    static const std::wregex search_net_tail_re(
        L"(.+?)\\s+internette\\s+ara",
        std::regex_constants::ECMAScript | std::regex_constants::icase);
    std::string query;
    if (std::regex_search(text_w, m, search_re))
        query = clean_name(su(m[1].str()));
    else if (std::regex_search(text_w, m, search_google_wrap_re))
        query = clean_name(su(m[1].str()));
    else if (std::regex_search(text_w, m, search_net_tail_re))
        query = clean_name(su(m[1].str()));
    if (!query.empty()) {
        ActionPlan p;
        PlannedAction a;
        a.action_type = "search_web";
        a.arguments = {{"query", query}};
        a.description = "Google'da '" + query + "' ara";
        p.actions.push_back(a);
        return p;
    }

    static const std::vector<std::pair<std::string, std::string>> app_map = {
        {"not defteri", "notepad"}, {"notepad", "notepad"}, {"hesap makinesi", "calc"},
        {"calculator", "calc"}, {"dosya gezgini", "explorer"}, {"explorer", "explorer"},
        {"vs code", "code"}, {"vscode", "code"}, {"chrome", "chrome"}};
    bool wants_open = has_word(lower, L"aç") || has_word(lower, L"başlat");
    if (wants_open) {
        for (const auto& [spoken, command] : app_map) {
            if (lower_s.find(spoken) != std::string::npos) {
                std::string pretty = spoken;
                if (!pretty.empty()) pretty[0] = static_cast<char>(std::toupper(static_cast<unsigned char>(pretty[0])));
                ActionPlan p;
                PlannedAction a;
                a.action_type = "open_application";
                a.arguments = {{"application", command}};
                a.description = pretty + " uygulamasını aç";
                p.actions.push_back(a);
                return p;
            }
        }
    }

    static const std::wregex delete_re(
        L"(?:dosya|klas[oö]r)\\s+['\"“”]?([^'\"“”]+?)['\"“”]?\\s+(?:sil|kaldır)",
        std::regex_constants::ECMAScript | std::regex_constants::icase);
    if (std::regex_search(text_w, m, delete_re)) {
        std::string name = clean_name(su(m[1].str()));
        if (!name.empty()) {
            ActionPlan p;
            PlannedAction a;
            a.action_type = "delete_path";
            a.arguments = {{"path", u8s(desktop_path(name))}};
            a.description = "Masaüstündeki '" + name + "' öğesini Geri Dönüşüm Kutusuna taşı";
            a.risk = "destructive";
            p.actions.push_back(a);
            return p;
        }
    }

    return std::nullopt;
}

std::optional<ActionPlan> ActionPlanner::plan_folder_and_document(const std::string& text) {
    std::wstring text_w = ws(text);
    static const std::wregex folder_re(
        L"([\\wçğıöşüÇĞİÖŞÜ _.\\-]+?)\\s+ad(?:ı|i)nda\\s+(?:bir\\s+)?(?:klas[oö]r|dizin|folder)\\s+"
        L"(?:oluştur|yarat)",
        std::regex_constants::ECMAScript | std::regex_constants::icase);
    std::wsmatch m;
    if (!std::regex_search(text_w, m, folder_re)) return std::nullopt;
    std::string folder = clean_name(su(m[1].str()));
    if (folder.empty()) return std::nullopt;

    fs::path folder_path = desktop_path(folder);
    ActionPlan p;
    {
        PlannedAction a;
        a.action_type = "create_folder";
        a.arguments = {{"path", u8s(folder_path)}};
        a.description = "Masaüstünde '" + folder + "' klasörünü oluştur";
        p.actions.push_back(a);
    }

    static const std::wregex file_re(
        L"(?:içine|içerisinde)\\s*[\"“']?([\\wçğıöşüÇĞİÖŞÜ _.\\-]+?)[\"”']?\\s+ad(?:ı|i)nda\\s+(?:bir\\s+)?"
        L"(?:metin belgesi|txt dosyası|dosya)\\s+(?:oluştur|yarat)",
        std::regex_constants::ECMAScript | std::regex_constants::icase);
    if (!std::regex_search(text_w, m, file_re)) return p;
    std::string file_name = with_text_extension(clean_name(su(m[1].str())).empty() ? "yeni_dosya"
                                                                                    : clean_name(su(m[1].str())));

    static const std::wregex content_re(
        L"(?:belgenin\\s+)?içeri(?:ğ|g)i(?:nde)?\\s+(?:(\\d+|bir|iki|üç|dört|beş)\\s*(?:kere|kez)\\s*)?"
        L"[\"“']([^\"”']+)[\"“']\\s*(?:yaz|ekle)",
        std::regex_constants::ECMAScript | std::regex_constants::icase);
    std::string content;
    std::wsmatch cm;
    if (std::regex_search(text_w, cm, content_re)) {
        int repetitions = count_of(su(cm[1].str()));
        std::string one = su(wstrip_ws(cm[2].str()));
        for (int i = 0; i < repetitions; ++i) {
            if (i) content += "\n";
            content += one;
        }
    }

    PlannedAction w;
    w.action_type = "write_text_file";
    w.arguments = {{"path", u8s(folder_path / p8(file_name))}, {"content", content}, {"overwrite", false}};
    w.description = "'" + folder + "' klasörüne '" + file_name + "' dosyasını oluştur ve istenen metni yaz";
    p.actions.push_back(w);
    return p;
}

std::optional<ActionPlan> ActionPlanner::plan_notepad_text(const std::string& text) {
    std::wstring text_w = ws(text);
    static const std::wregex note_open_re(
        L"(?:not defteri|notepad).*?(?:aç|başlat)",
        std::regex_constants::ECMAScript | std::regex_constants::icase);
    if (!std::regex_search(text_w, note_open_re)) return std::nullopt;

    static const std::wregex content_re(
        L"(?:içine\\s+)?[\"“']([^\"”']+)[\"“']\\s*(?:yaz|ekle)",
        std::regex_constants::ECMAScript | std::regex_constants::icase);
    std::wsmatch cm;
    if (!std::regex_search(text_w, cm, content_re)) return std::nullopt;

    fs::path path = desktop_path("Jarvis_Notu.txt");
    std::string content = su(wstrip_ws(cm[1].str()));
    ActionPlan p;
    PlannedAction a;
    a.action_type = "write_text_file";
    a.arguments = {{"path", u8s(path)}, {"content", content}, {"overwrite", true}};
    a.description = "Masaüstünde Jarvis_Notu.txt dosyasını hazırla";
    p.actions.push_back(a);
    PlannedAction b;
    b.action_type = "open_path";
    b.arguments = {{"path", u8s(path)}};
    b.description = "Hazırlanan notu Not Defteri ile aç";
    p.actions.push_back(b);
    return p;
}

}  // namespace jarvis::ai
