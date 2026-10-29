#include "jarvis/tools/window_tools.hpp"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <map>
#include <set>
#include <thread>

#include "jarvis/ai/safety.hpp"
#include "jarvis/core/util.hpp"
#include "jarvis/core/win_path.hpp"

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

using nlohmann::json;

namespace jarvis::tools {
namespace {

std::string ascii_lower(std::string value) {
    for (char& c : value) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return value;
}

// --- argüman okuyucuları (Python str(args.get(...)) / int(...)) --------------

bool arg_present(const json& args, const std::string& key) {
    return args.is_object() && args.contains(key) && !args[key].is_null();
}

std::string arg_text(const json& args, const std::string& key) {
    if (!arg_present(args, key)) return {};
    const json& value = args.at(key);
    if (value.is_string()) return value.get<std::string>();
    return value.dump();
}

std::optional<long long> parse_int(const std::string& text) {
    const std::string value = util::trim(text);
    if (value.empty()) return std::nullopt;
    const char* cursor = value.c_str();
    if (*cursor == '+' || *cursor == '-') ++cursor;
    if (!*cursor) return std::nullopt;
    for (const char* scan = cursor; *scan; ++scan) {
        if (!std::isdigit(static_cast<unsigned char>(*scan))) return std::nullopt;
    }
    try {
        std::size_t used = 0;
        const long long number = std::stoll(value, &used);
        if (used != value.size()) return std::nullopt;
        return number;
    } catch (const std::exception&) {
        return std::nullopt;
    }
}

// json değerini Python int() gibi zorlar; olmuyorsa nullopt.
std::optional<long long> coerce_int(const json& value) {
    if (value.is_number_integer()) return value.get<long long>();
    if (value.is_number_float()) return static_cast<long long>(value.get<double>());
    if (value.is_boolean()) return value.get<bool>() ? 1 : 0;
    if (value.is_string()) return parse_int(value.get<std::string>());
    return std::nullopt;
}

// --- normalize_text (app/core/screen_agent.py) ------------------------------
// Python: İ/I/ı → i, sonra NFKD + birleştirme işaretlerini at + casefold, sonra
// boşlukları tekilleştir. NFKD yerine çözümlenebilir Latin aksanlarının taban
// harfi tablosu kullanılıyor; gerçek pencere başlıklarında aynı sonucu verir.
// Unicode'da çözümlenmeyen bağlar (æ, ß, ø, ł, đ) Python gibi olduğu gibi kalır.
struct FoldEntry {
    unsigned int code;
    char base;
};

const FoldEntry kFolds[] = {
    {0x00C0, 'A'}, {0x00C1, 'A'}, {0x00C2, 'A'}, {0x00C3, 'A'}, {0x00C4, 'A'}, {0x00C5, 'A'},
    {0x00C7, 'C'}, {0x00C8, 'E'}, {0x00C9, 'E'}, {0x00CA, 'E'}, {0x00CB, 'E'}, {0x00CC, 'I'},
    {0x00CD, 'I'}, {0x00CE, 'I'}, {0x00CF, 'I'}, {0x00D1, 'N'}, {0x00D2, 'O'}, {0x00D3, 'O'},
    {0x00D4, 'O'}, {0x00D5, 'O'}, {0x00D6, 'O'}, {0x00D9, 'U'}, {0x00DA, 'U'}, {0x00DB, 'U'},
    {0x00DC, 'U'}, {0x00DD, 'Y'},
    {0x00E0, 'a'}, {0x00E1, 'a'}, {0x00E2, 'a'}, {0x00E3, 'a'}, {0x00E4, 'a'}, {0x00E5, 'a'},
    {0x00E7, 'c'}, {0x00E8, 'e'}, {0x00E9, 'e'}, {0x00EA, 'e'}, {0x00EB, 'e'}, {0x00EC, 'i'},
    {0x00ED, 'i'}, {0x00EE, 'i'}, {0x00EF, 'i'}, {0x00F1, 'n'}, {0x00F2, 'o'}, {0x00F3, 'o'},
    {0x00F4, 'o'}, {0x00F5, 'o'}, {0x00F6, 'o'}, {0x00F9, 'u'}, {0x00FA, 'u'}, {0x00FB, 'u'},
    {0x00FC, 'u'}, {0x00FD, 'y'}, {0x00FF, 'y'},
    {0x0100, 'A'}, {0x0102, 'A'}, {0x0104, 'A'}, {0x0106, 'C'}, {0x0108, 'C'}, {0x010A, 'C'},
    {0x010C, 'C'}, {0x010E, 'D'}, {0x0110, 'D'}, {0x0112, 'E'}, {0x0114, 'E'}, {0x0116, 'E'},
    {0x0118, 'E'}, {0x011A, 'E'}, {0x011C, 'G'}, {0x011E, 'G'}, {0x0120, 'G'}, {0x0122, 'G'},
    {0x0124, 'H'}, {0x0128, 'I'}, {0x012A, 'I'}, {0x012C, 'I'}, {0x012E, 'I'}, {0x0130, 'I'},
    {0x0132, 'I'}, {0x0134, 'J'}, {0x0136, 'K'}, {0x0139, 'L'}, {0x013B, 'L'}, {0x013D, 'L'},
    {0x013F, 'L'}, {0x0141, 'L'}, {0x0143, 'N'}, {0x0145, 'N'}, {0x0147, 'N'}, {0x014C, 'O'},
    {0x014E, 'O'}, {0x0150, 'O'}, {0x0154, 'R'}, {0x0156, 'R'}, {0x0158, 'R'}, {0x015A, 'S'},
    {0x015C, 'S'}, {0x015E, 'S'}, {0x0160, 'S'}, {0x0162, 'T'}, {0x0164, 'T'}, {0x0168, 'U'},
    {0x016A, 'U'}, {0x016C, 'U'}, {0x016E, 'U'}, {0x0170, 'U'}, {0x0172, 'U'}, {0x0174, 'W'},
    {0x0176, 'Y'}, {0x0178, 'Y'}, {0x0179, 'Z'}, {0x017B, 'Z'}, {0x017D, 'Z'},
    {0x0101, 'a'}, {0x0103, 'a'}, {0x0105, 'a'}, {0x0107, 'c'}, {0x0109, 'c'}, {0x010B, 'c'},
    {0x010D, 'c'}, {0x010F, 'd'}, {0x0111, 'd'}, {0x0113, 'e'}, {0x0115, 'e'}, {0x0117, 'e'},
    {0x0119, 'e'}, {0x011B, 'e'}, {0x011D, 'g'}, {0x011F, 'g'}, {0x0121, 'g'}, {0x0123, 'g'},
    {0x0125, 'h'}, {0x0129, 'i'}, {0x012B, 'i'}, {0x012D, 'i'}, {0x012F, 'i'}, {0x0131, 'i'},
    {0x0133, 'i'}, {0x0135, 'j'}, {0x0137, 'k'}, {0x013A, 'l'}, {0x013C, 'l'}, {0x013E, 'l'},
    {0x0140, 'l'}, {0x0142, 'l'}, {0x0144, 'n'}, {0x0146, 'n'}, {0x0148, 'n'}, {0x014D, 'o'},
    {0x014F, 'o'}, {0x0151, 'o'}, {0x0155, 'r'}, {0x0157, 'r'}, {0x0159, 'r'}, {0x015B, 's'},
    {0x015D, 's'}, {0x015F, 's'}, {0x0161, 's'}, {0x0163, 't'}, {0x0165, 't'}, {0x0169, 'u'},
    {0x016B, 'u'}, {0x016D, 'u'}, {0x016F, 'u'}, {0x0171, 'u'}, {0x0173, 'u'}, {0x0175, 'w'},
    {0x0177, 'y'}, {0x017A, 'z'}, {0x017C, 'z'}, {0x017E, 'z'},
};

char folded_base(unsigned int code) {
    for (const FoldEntry& entry : kFolds) {
        if (entry.code == code) return entry.base;
    }
    return 0;
}

// UTF-8 kod noktası okur; index'i ilerletir. Bozuk baytta index 1 artar.
unsigned int next_code_point(const std::string& text, std::size_t& index) {
    if (index >= text.size()) return 0;
    const unsigned char lead = static_cast<unsigned char>(text[index]);
    std::size_t extra = 0;
    unsigned int code = 0;
    if (lead < 0x80) {
        code = lead;
    } else if ((lead & 0xE0) == 0xC0) {
        code = lead & 0x1Fu;
        extra = 1;
    } else if ((lead & 0xF0) == 0xE0) {
        code = lead & 0x0Fu;
        extra = 2;
    } else if ((lead & 0xF8) == 0xF0) {
        code = lead & 0x07u;
        extra = 3;
    } else {
        ++index;
        return 0xFFFD;
    }
    if (index + extra >= text.size()) {
        ++index;
        return 0xFFFD;
    }
    for (std::size_t i = 1; i <= extra; ++i) {
        const unsigned char byte = static_cast<unsigned char>(text[index + i]);
        if ((byte & 0xC0) != 0x80) {
            ++index;
            return 0xFFFD;
        }
        code = (code << 6) | (byte & 0x3Fu);
    }
    index += extra + 1;
    return code;
}

void append_utf8(std::string& out, unsigned int code) {
    if (code < 0x80) {
        out.push_back(static_cast<char>(code));
    } else if (code < 0x800) {
        out.push_back(static_cast<char>(0xC0 | (code >> 6)));
        out.push_back(static_cast<char>(0x80 | (code & 0x3F)));
    } else if (code < 0x10000) {
        out.push_back(static_cast<char>(0xE0 | (code >> 12)));
        out.push_back(static_cast<char>(0x80 | ((code >> 6) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | (code & 0x3F)));
    } else {
        out.push_back(static_cast<char>(0xF0 | (code >> 18)));
        out.push_back(static_cast<char>(0x80 | ((code >> 12) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | ((code >> 6) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | (code & 0x3F)));
    }
}

const std::map<std::string, std::string>& alias_table() {
    static const std::map<std::string, std::string> kAliases = {
        {"list", "list"},      {"list_windows", "list"},  {"focus", "focus"},
        {"focus_window", "focus"}, {"minimize", "minimize"}, {"maximize", "maximize"},
        {"restore", "restore"},    {"move", "move"},         {"resize", "resize"},
        {"set_bounds", "set_bounds"}, {"close", "close"},   {"close_app", "close"},
    };
    return kAliases;
}

// Python CRITICAL_PROCESSES kümesi aynen alındı.
const std::set<std::string>& critical_processes() {
    static const std::set<std::string> kCritical = {
        "system",      "registry",   "smss.exe",      "csrss.exe",    "wininit.exe",
        "services.exe", "lsass.exe", "winlogon.exe",  "dwm.exe",      "explorer.exe",
        "consent.exe",  "logonui.exe", "fontdrvhost.exe", "sihost.exe", "taskhostw.exe",
    };
    return kCritical;
}

// Python SECURE_PROCESSES / SECURE_TITLE_PATTERNS (screen_agent.py).
const std::set<std::string>& secure_processes() {
    static const std::set<std::string> kSecure = {
        "consent.exe", "logonui.exe", "credentialuibroker.exe"};
    return kSecure;
}

const char* const kSecureTitlePatterns[] = {
    "user account control",
    "kullanici hesabi denetimi",
    "do you want to allow this app",
    "bu uygulamanin degisiklik yapmasina izin ver",
};

}  // namespace

std::string normalize_text(const std::string& value) {
    std::string pre = value;
    // İ (U+0130) ve ı (U+0131) bayt çiftleri; UTF-8'te kendi başlarına gelirler.
    for (std::size_t at = pre.find("\xC4\xB0"); at != std::string::npos;
         at = pre.find("\xC4\xB0", at))
        pre.replace(at, 2, "i");
    for (std::size_t at = pre.find("\xC5\x9F"); at != std::string::npos;
         at = pre.find("\xC5\x9F", at))
        pre.replace(at, 2, "i");
    for (std::size_t at = pre.find('I'); at != std::string::npos; at = pre.find('I', at))
        pre.replace(at, 1, "i");

    std::string folded;
    folded.reserve(pre.size());
    std::size_t index = 0;
    while (index < pre.size()) {
        const unsigned int code = next_code_point(pre, index);
        if (code < 0x80) {
            folded.push_back(static_cast<char>(std::tolower(static_cast<unsigned char>(code))));
            continue;
        }
        const char base = folded_base(code);
        if (base) {
            // Taban harfler ASCII; Python casefold'ın NFKD'den SONRA geldiği için
            // "Ç" → "c" olur (büyük harf tabanına katlamak yetmez).
            folded.push_back(
                static_cast<char>(std::tolower(static_cast<unsigned char>(base))));
        } else {
            append_utf8(folded, code ? code : 0xFFFD);
        }
    }

    // Python " ".join(value.split()): ardışık boşluklar tek boşluğa döner, baş
    // ve son boşluklar atılır. Unicode boşluklarından yalnızca ASCII kümesi
    // sayılır (boşluk, sekme, satır sonu türleri).
    std::string out;
    out.reserve(folded.size());
    bool pending_space = false;
    for (const char c : folded) {
        const bool space = c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f' || c == '\v';
        if (space) {
            pending_space = !out.empty();
            continue;
        }
        if (pending_space) {
            out.push_back(' ');
            pending_space = false;
        }
        out.push_back(c);
    }
    return out;
}

std::vector<unsigned int> code_points(const std::string& value) {
    std::vector<unsigned int> out;
    out.reserve(value.size());
    std::size_t index = 0;
    while (index < value.size()) {
        const std::size_t before = index;
        const unsigned int code = next_code_point(value, index);
        if (index == before) ++index;
        out.push_back(code ? code : 0xFFFD);
    }
    return out;
}

std::optional<std::string> resolve_action(const std::string& raw) {
    const auto found = alias_table().find(ascii_lower(util::trim(raw)));
    if (found == alias_table().end()) return std::nullopt;
    return found->second;
}

bool is_critical_process(const std::string& process) {
    return critical_processes().count(normalize_text(process)) > 0;
}

std::optional<std::string> secure_desktop_reason(const std::string& title,
                                                 const std::string& process) {
    const std::string folded = normalize_text(process);
    if (secure_processes().count(folded)) {
        return "Güvenli masaüstü süreci etkin (" + folded + "); otomasyon durduruldu.";
    }
    const std::string normalized_title = normalize_text(title);
    for (const char* pattern : kSecureTitlePatterns) {
        if (!normalized_title.empty() && normalized_title.find(pattern) != std::string::npos) {
            return "UAC / güvenli masaüstü penceresi açık; Jarvis bu ekranda işlem yapamaz.";
        }
    }
    return std::nullopt;
}

std::optional<std::string> safety_reason(const std::string& title, const std::string& process,
                                         const std::string& action, long long process_id) {
    if (auto reason = secure_desktop_reason(title, process); reason) return reason;
    if (action == "close") {
        if (is_critical_process(process)) return "Kritik sistem süreci kapatılamaz: " + process;
#ifdef _WIN32
        if (process_id == static_cast<long long>(GetCurrentProcessId())) {
            return "Jarvis'in kendi penceresi bu araçla kapatılamaz.";
        }
#else
        (void)process_id;
#endif
    }
    return std::nullopt;
}

std::variant<long long, std::string> checked_int(const json& args, const std::string& key,
                                                 long long minimum, long long maximum) {
    if (!arg_present(args, key)) return "Pencere işlemi için " + key + " gerekli.";
    const json& value = args.at(key);
    const std::optional<long long> number = coerce_int(value);
    if (!number) return key + " sayı olmalı: " + value.dump();
    if (*number < minimum || *number > maximum) {
        return key + " " + std::to_string(minimum) + " ile " + std::to_string(maximum) +
               " arasında olmalı.";
    }
    return *number;
}

std::variant<std::size_t, std::string> select_title_match(const std::vector<std::string>& titles,
                                                          const std::string& wanted) {
    const std::string needle = normalize_text(wanted);
    std::vector<std::size_t> matches;
    for (std::size_t i = 0; i < titles.size(); ++i) {
        if (!needle.empty() && normalize_text(titles[i]) == needle) matches.push_back(i);
    }
    if (matches.empty()) {
        for (std::size_t i = 0; i < titles.size(); ++i) {
            if (!needle.empty() && normalize_text(titles[i]).find(needle) != std::string::npos)
                matches.push_back(i);
        }
    }
    if (matches.empty()) return "Açık pencere bulunamadı: " + wanted;
    if (matches.size() > 1) {
        std::string names;
        for (std::size_t i = 0; i < matches.size() && i < 4; ++i) {
            if (!names.empty()) names += ", ";
            names += titles[matches[i]];
        }
        return "Pencere adı belirsiz: " + names;
    }
    return matches.front();
}

json WindowInfo::to_json() const {
    json edges = json::array();
    for (const long long edge : this->rect) edges.push_back(edge);
    return json{
        {"handle", handle},
        {"title", title},
        {"process", process},
        {"process_id", process_id},
        {"rect", edges},
        {"minimized", minimized},
        {"maximized", maximized},
        {"foreground", foreground},
    };
}

std::string WindowTools::list_windows(const json& args) {
    const std::string query = normalize_text(arg_text(args, "filter"));
    long long limit = 40;
    if (const auto parsed = parse_int(arg_text(args, "limit"))) limit = *parsed;
    if (limit < 1) limit = 1;
    if (limit > static_cast<long long>(kMaxWindows)) limit = static_cast<long long>(kMaxWindows);

    std::vector<WindowInfo> rows;
    for (const WindowInfo& info : collect_windows()) {
        if (!query.empty()) {
            const std::string hay = normalize_text(info.title + " " + info.process);
            if (hay.find(query) == std::string::npos) continue;
        }
        rows.push_back(info);
    }
    // Python: (not foreground, normalize_text(title)) — stabil sıralama.
    std::stable_sort(rows.begin(), rows.end(), [](const WindowInfo& a, const WindowInfo& b) {
        if (a.foreground != b.foreground) return b.foreground;
        return normalize_text(a.title) < normalize_text(b.title);
    });
    if (rows.size() > static_cast<std::size_t>(limit)) rows.resize(static_cast<std::size_t>(limit));

    json windows = json::array();
    for (const WindowInfo& info : rows) windows.push_back(info.to_json());
    const json payload{
        {"count", static_cast<long long>(rows.size())},
        {"windows", windows},
        {"note",
         "Pencereler canlı sistemden okundu. İşlem için title veya handle kullan; başlıklar "
         "değişebilir, işlem öncesi listeyi yenile."},
    };
    return payload.dump();
}

#ifdef _WIN32
namespace {

struct Row {
    HWND hwnd = nullptr;
    WindowInfo info;
};

std::string u8(const std::wstring& wide) { return util::wide_to_utf8(wide); }

std::wstring window_text_of(HWND hwnd) {
    const int length = GetWindowTextLengthW(hwnd);
    if (length <= 0) return {};
    std::wstring buffer(static_cast<std::size_t>(length) + 1, L'\0');
    const int written = GetWindowTextW(hwnd, buffer.data(), length + 1);
    if (written <= 0) return {};
    buffer.resize(static_cast<std::size_t>(written));
    return buffer;
}

// psutil.Process(pid).name() karşılığı: exe tam yolunun dosya adı, küçük harfli.
std::string process_name_of(HWND hwnd, long long& process_id) {
    DWORD pid = 0;
    GetWindowThreadProcessId(hwnd, &pid);
    process_id = static_cast<long long>(pid);
    if (!pid) return {};
    HANDLE process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
    if (!process) return {};
    std::wstring name(1024, L'\0');
    DWORD size = static_cast<DWORD>(name.size());
    std::string out;
    if (QueryFullProcessImageNameW(process, 0, name.data(), &size) && size > 0) {
        name.resize(size);
        const std::size_t separator = name.find_last_of(L"\\/");
        if (separator != std::wstring::npos) name = name.substr(separator + 1);
        CharLowerW(name.data());
        out = u8(name);
    }
    CloseHandle(process);
    return out;
}

// Python _rect_of: geçerli alanı olmayan dikdörtgen "yok" sayılır.
std::vector<long long> rect_of(HWND hwnd) {
    RECT rectangle{};
    if (!GetWindowRect(hwnd, &rectangle)) return {};
    const long long left = rectangle.left;
    const long long top = rectangle.top;
    const long long right = rectangle.right;
    const long long bottom = rectangle.bottom;
    if (right <= left || bottom <= top) return {};
    return {left, top, right, bottom};
}

bool is_maximized_of(HWND hwnd) {
    WINDOWPLACEMENT placement{};
    placement.length = sizeof(placement);
    if (!GetWindowPlacement(hwnd, &placement)) return false;
    return placement.showCmd == SW_SHOWMAXIMIZED;
}

long long foreground_handle() {
    return static_cast<long long>(reinterpret_cast<std::uintptr_t>(GetForegroundWindow()));
}

WindowInfo describe(HWND hwnd) {
    WindowInfo info;
    info.handle = static_cast<long long>(reinterpret_cast<std::uintptr_t>(hwnd));
    info.title = u8(window_text_of(hwnd));
    info.process = process_name_of(hwnd, info.process_id);
    wchar_t class_name[256] = {0};
    const int written = GetClassNameW(hwnd, class_name, 255);
    if (written > 0) info.window_class = u8(std::wstring(class_name, static_cast<std::size_t>(written)));
    info.rect = rect_of(hwnd);
    info.minimized = IsIconic(hwnd) != FALSE;
    info.maximized = is_maximized_of(hwnd);
    info.foreground = info.handle != 0 && info.handle == foreground_handle();
    return info;
}

// WindowsUI.focus karşılığı: fare/klavye göndermeden öne getirir ve gerçekten
// etkin olduğunu geri okuyarak doğrular.
bool focus_window(HWND hwnd) {
    if (IsIconic(hwnd)) ShowWindow(hwnd, SW_RESTORE);
    SetActiveWindow(hwnd);
    SetFocus(hwnd);
    std::this_thread::sleep_for(std::chrono::milliseconds(250));
    if (GetForegroundWindow() == hwnd) return true;
    HWND current = GetForegroundWindow();
    DWORD owner = 0;
    const DWORD target_thread = GetWindowThreadProcessId(hwnd, &owner);
    const DWORD foreground_thread = current ? GetWindowThreadProcessId(current, &owner) : 0;
    if (target_thread && foreground_thread && target_thread != foreground_thread) {
        AttachThreadInput(foreground_thread, target_thread, TRUE);
        BringWindowToTop(hwnd);
        SetForegroundWindow(hwnd);
        AttachThreadInput(foreground_thread, target_thread, FALSE);
    } else {
        SetForegroundWindow(hwnd);
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
    return GetForegroundWindow() == hwnd;
}

BOOL CALLBACK collect_callback(HWND hwnd, LPARAM parameter) {
    auto* rows = reinterpret_cast<std::vector<Row>*>(parameter);
    if (!IsWindowVisible(hwnd)) return TRUE;
    if (GetWindow(hwnd, GW_OWNER) != nullptr) return TRUE;
    if (window_text_of(hwnd).empty()) return TRUE;
    Row row;
    row.hwnd = hwnd;
    row.info = describe(hwnd);
    rows->push_back(std::move(row));
    return TRUE;
}

std::vector<Row> collect_rows() {
    std::vector<Row> rows;
    EnumWindows(&collect_callback, reinterpret_cast<LPARAM>(&rows));
    return rows;
}

// Python _int_arg: aralık dışı/eksik/alansa ActionForbidden'a dönüşür.
long long require_int(const json& args, const char* key, long long minimum, long long maximum) {
    std::variant<long long, std::string> result = checked_int(args, key, minimum, maximum);
    if (auto* text = std::get_if<std::string>(&result)) throw ai::ActionForbidden(*text);
    return std::get<long long>(result);
}

// Python expected sözlüğü: args.get(key, varsayılan) + int(); aralık denetimi yok.
long long expected_int(const json& args, const char* key, long long fallback) {
    if (!arg_present(args, key)) return fallback;
    const json& value = args.at(key);
    const auto number = coerce_int(value);
    if (!number) throw ai::ActionForbidden(std::string(key) + " sayı olmalı: " + value.dump());
    return *number;
}

}  // namespace
#endif  // _WIN32

std::vector<WindowInfo> collect_windows() {
    std::vector<WindowInfo> out;
#ifdef _WIN32
    for (const Row& row : collect_rows()) out.push_back(row.info);
#endif
    return out;
}

bool focus_window_handle(long long handle) {
#ifdef _WIN32
    if (handle <= 0) return false;
    const auto hwnd = reinterpret_cast<HWND>(static_cast<std::uintptr_t>(handle));
    if (!IsWindow(hwnd)) return false;
    return focus_window(hwnd);
#else
    (void)handle;
    return false;
#endif
}

#ifdef _WIN32

std::string WindowTools::run(const json& args) {
    std::string raw_action = arg_text(args, "action");
    if (util::trim(raw_action).empty()) raw_action = "list";
    const std::optional<std::string> action = resolve_action(raw_action);
    if (!action) {
        throw ai::ActionForbidden(
            "window_control action: list, focus, minimize, maximize, restore, move, resize, "
            "set_bounds veya close olmalı.");
    }
    const std::string& kind = *action;
    if (kind == "list") return list_windows(args);

    // --- hedef pencere (Python _resolve) ---
    const std::vector<Row> rows = collect_rows();
    const Row* target = nullptr;
    long long handle = 0;
    if (!arg_text(args, "handle").empty() && arg_present(args, "handle")) {
        const auto wanted = coerce_int(args.at("handle"));
        if (!wanted) throw ai::ActionForbidden("handle sayı olmalı.");
        handle = *wanted;
    }
    if (handle != 0) {
        for (const Row& row : rows) {
            if (row.info.handle == handle) {
                target = &row;
                break;
            }
        }
        if (!target) {
            throw ai::ActionForbidden("Handle " + std::to_string(handle) +
                                      " ile açık pencere bulunamadı.");
        }
    } else {
        const std::string title = util::trim(arg_text(args, "title"));
        if (title.empty()) throw ai::ActionForbidden("Pencere işlemi için title veya handle gerekli.");
        std::vector<std::string> titles;
        titles.reserve(rows.size());
        for (const Row& row : rows) titles.push_back(row.info.title);
        const auto match = select_title_match(titles, title);
        if (auto* text = std::get_if<std::string>(&match)) throw ai::ActionForbidden(*text);
        target = &rows[std::get<std::size_t>(match)];
    }
    const HWND hwnd = target->hwnd;

    if (auto reason =
            safety_reason(target->info.title, target->info.process, kind, target->info.process_id)) {
        throw ai::ActionForbidden(*reason);
    }

    const WindowInfo before = describe(hwnd);
    std::string mechanism;

    if (kind == "focus") {
        if (!focus_window(hwnd)) {
            throw ai::ActionForbidden("Pencere öne getirilemedi; işlem durduruldu.");
        }
        mechanism = "foreground_verified";
    } else if (kind == "minimize") {
        ShowWindow(hwnd, SW_MINIMIZE);
        mechanism = "minimize";
    } else if (kind == "maximize") {
        ShowWindow(hwnd, SW_MAXIMIZE);
        mechanism = "maximize";
    } else if (kind == "restore") {
        ShowWindow(hwnd, SW_RESTORE);
        mechanism = "restore";
    } else if (kind == "move" || kind == "resize" || kind == "set_bounds") {
        const std::vector<long long> base =
            before.rect.empty() ? std::vector<long long>{0, 0, 800, 600} : before.rect;
        long long x = base[0];
        long long y = base[1];
        long long width = base[2] - base[0];
        long long height = base[3] - base[1];
        if (kind == "move" || kind == "set_bounds") {
            x = require_int(args, "x", -kMaxCoordinate, kMaxCoordinate);
            y = require_int(args, "y", -kMaxCoordinate, kMaxCoordinate);
        }
        if (kind == "resize" || kind == "set_bounds") {
            width = require_int(args, "width", kMinWindowSize, kMaxWindowSize);
            height = require_int(args, "height", kMinWindowSize, kMaxWindowSize);
        }
        SetWindowPos(hwnd, nullptr, static_cast<int>(x), static_cast<int>(y),
                     static_cast<int>(width), static_cast<int>(height), SWP_NOZORDER | SWP_NOACTIVATE);
        mechanism = kind;
    } else if (kind == "close") {
        if (!PostMessageW(hwnd, WM_CLOSE, 0, 0)) {
            throw ai::ActionForbidden("Pencere kapatma isteği gönderilemedi.");
        }
        mechanism = "wm_close";
    }

    const bool quick =
        kind == "focus" || kind == "minimize" || kind == "maximize" || kind == "restore";
    std::this_thread::sleep_for(std::chrono::milliseconds(quick ? 250 : 350));
    const WindowInfo after = describe(hwnd);

    if (kind == "close") {
        bool closed = false;
        const auto deadline = std::chrono::steady_clock::now() +
                              std::chrono::milliseconds(
                                  static_cast<long long>(kCloseWaitSeconds * 1000.0));
        while (std::chrono::steady_clock::now() < deadline) {
            if (!IsWindow(hwnd)) {
                closed = true;
                break;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(150));
        }
        const json payload{
            {"action", kind},
            {"mechanism", mechanism},
            {"window", before.to_json()},
            {"closed", closed},
            {"note",
             closed ? "Pencere kapandı."
                    : "Kapatma istendi. Uygulama kaydedilmemiş belge için kendi onay penceresini "
                      "açmış olabilir; zorla sonlandırma yapılmadı."},
        };
        return payload.dump();
    }

    bool verified = true;
    if (kind == "focus") {
        verified = after.foreground;
    } else if (kind == "minimize") {
        verified = after.minimized;
    } else if (kind == "maximize") {
        verified = after.maximized;
    } else if (kind == "restore") {
        verified = !after.minimized && !after.maximized;
    } else if (kind == "move" || kind == "resize" || kind == "set_bounds") {
        const std::vector<long long> base =
            before.rect.empty() ? std::vector<long long>{0, 0, 800, 600} : before.rect;
        const std::map<std::string, long long> expected{
            {"x", expected_int(args, "x", base[0])},
            {"y", expected_int(args, "y", base[1])},
            {"width", expected_int(args, "width", base[2] - base[0])},
            {"height", expected_int(args, "height", base[3] - base[1])},
        };
        if (!after.rect.empty()) {
            const std::map<std::string, long long> actual{
                {"x", after.rect[0]},
                {"y", after.rect[1]},
                {"width", after.rect[2] - after.rect[0]},
                {"height", after.rect[3] - after.rect[1]},
            };
            for (const auto& [key, value] : expected) {
                if (std::abs(actual.at(key) - value) > 12) verified = false;
            }
        }
    }

    const json payload{
        {"action", kind},
        {"mechanism", mechanism},
        {"window", before.to_json()},
        {"result", after.to_json()},
        {"verified", verified},
        {"note",
         verified ? "Pencere durumu işlem sonrası yeniden okunarak doğrulandı."
                 : "Pencere komutu gönderildi ancak durum doğrulanamadı; yeni list_windows ile "
                   "kontrol et."},
    };
    return payload.dump();
}

#else

std::string WindowTools::run(const json& args) {
    (void)args;
    throw ai::ActionForbidden("Pencere yönetimi yalnızca Windows sürümünde bağlıdır.");
}

#endif  // _WIN32

}  // namespace jarvis::tools
