#include "jarvis/tools/app_launcher.hpp"

#include <algorithm>
#include <array>
#include <cctype>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <deque>
#include <map>
#include <set>
#include <thread>
#include <tuple>
#include <unordered_map>

#include <nlohmann/json.hpp>

#include "jarvis/ai/safety.hpp"
#include "jarvis/core/util.hpp"
#include "jarvis/core/win_path.hpp"
#include "jarvis/tools/window_tools.hpp"

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <shellapi.h>
#include <shobjidl.h>
#include <tlhelp32.h>
#include <objbase.h>
#endif

namespace fs = std::filesystem;
using nlohmann::json;

namespace jarvis::tools {
namespace {

std::string ascii_lower(std::string value) {
    for (char& c : value) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return value;
}

bool starts_with(const std::string& value, const std::string& prefix) {
    return value.size() >= prefix.size() && value.compare(0, prefix.size(), prefix) == 0;
}

bool ends_with(const std::string& value, const std::string& suffix) {
    return value.size() >= suffix.size() &&
           value.compare(value.size() - suffix.size(), suffix.size(), suffix) == 0;
}

// Python " ".join(text.split()) — normalize_text zaten tekilleştirir.
std::vector<std::string> split_words(const std::string& value) {
    std::vector<std::string> out;
    std::string current;
    for (const char c : value) {
        if (c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f' || c == '\v') {
            if (!current.empty()) {
                out.push_back(current);
                current.clear();
            }
        } else {
            current.push_back(c);
        }
    }
    if (!current.empty()) out.push_back(current);
    return out;
}

bool contains(const std::vector<std::string>& haystack, const std::string& needle) {
    return std::find(haystack.begin(), haystack.end(), needle) != haystack.end();
}

const std::map<std::string, std::string>& system_apps() {
    static const std::map<std::string, std::string> kApps = {
        {"notepad", "notepad.exe"},
        {"not defteri", "notepad.exe"},
        {"calc", "calc.exe"},
        {"hesap makinesi", "calc.exe"},
        {"explorer", "explorer.exe"},
        {"dosya gezgini", "explorer.exe"},
    };
    return kApps;
}

const std::vector<std::string> kIgnoredShortcutWords = {"uninstall", "kaldir", "remove", "yardim",
                                                        "help", "readme"};

const std::set<std::string>& generic_words() {
    static const std::set<std::string> kGeneric = {
        "windows", "microsoft", "google", "the", "of", "and", "ve", "app", "application",
        "client", "launcher", "game", "games", "oyun", "uygulama", "desktop"};
    return kGeneric;
}

const std::set<std::string>& play_labels() {
    static const std::set<std::string> kPlay = {
        "play", "oyna", "play now", "oyna simdi", "launch", "launch game", "start game",
        "oyunu oyna", "oyunu baslat"};
    return kPlay;
}

const std::vector<std::string> kExeSkipWords = {
    "unins", "uninstall", "update", "updater", "setup", "installer", "crash", "report",
    "helper", "service", "test", "benchmark", "diagnostic", "mux", "inject", "page"};

// Python Path(path).stem: son uzantıyı atar ("chrome.exe" → "chrome").
std::string stem_of(const std::string& name) {
    const std::size_t dot = name.find_last_of('.');
    if (dot == std::string::npos || dot == 0) return name;
    return name.substr(0, dot);
}

std::string join(const std::vector<std::string>& items, const std::string& separator) {
    std::string out;
    for (std::size_t i = 0; i < items.size(); ++i) {
        if (i) out += separator;
        out += items[i];
    }
    return out;
}

}  // namespace

// --------------------------------------------------------------- salt metin
std::string AppLauncher::normalize(const std::string& value) {
    std::string folded = normalize_text(value);
    for (char& c : folded) {
        if (c == '-') c = ' ';
    }
    return util::collapse_whitespace(folded);
}

double AppLauncher::similarity(const std::string& left, const std::string& right) {
    // difflib.SequenceMatcher(None, a, b).ratio(): otomatik çöp eşiği kısa
    // metinlerde devreye girmediği için çöp kümesi boştur.
    const std::vector<unsigned int> a = code_points(left);
    const std::vector<unsigned int> b = code_points(right);
    const long long la = static_cast<long long>(a.size());
    const long long lb = static_cast<long long>(b.size());
    if (la + lb == 0) return 1.0;

    std::unordered_map<unsigned int, std::vector<long long>> b2j;
    for (long long j = 0; j < lb; ++j) b2j[b[static_cast<std::size_t>(j)]].push_back(j);

    long long matches = 0;
    std::deque<std::array<long long, 4>> queue{{{{0, la, 0, lb}}}};
    while (!queue.empty()) {
        const std::array<long long, 4> box = queue.front();
        queue.pop_front();
        const long long alo = box[0];
        const long long ahi = box[1];
        const long long blo = box[2];
        const long long bhi = box[3];

        long long besti = alo;
        long long bestj = blo;
        long long bestsize = 0;
        std::map<long long, long long> j2len;
        for (long long i = alo; i < ahi; ++i) {
            std::map<long long, long long> new_j2len;
            const auto found = b2j.find(a[static_cast<std::size_t>(i)]);
            if (found != b2j.end()) {
                for (const long long j : found->second) {
                    if (j < blo) continue;
                    if (j >= bhi) break;
                    const auto previous = j2len.find(j - 1);
                    const long long k = previous == j2len.end() ? 1 : previous->second + 1;
                    new_j2len[j] = k;
                    if (k > bestsize) {
                        besti = i - k + 1;
                        bestj = j - k + 1;
                        bestsize = k;
                    }
                }
            }
            j2len.swap(new_j2len);
        }
        if (bestsize == 0) continue;

        // Çöp kümesi boş olduğundan bu uzatma döngüleri difflib'de olduğu gibi
        // burada da uygulanır (sonuç değişmez, sadakat için korundu).
        while (besti > alo && bestj > blo &&
               a[static_cast<std::size_t>(besti - 1)] == b[static_cast<std::size_t>(bestj - 1)]) {
            --besti;
            --bestj;
            ++bestsize;
        }
        while (besti + bestsize < ahi && bestj + bestsize < bhi &&
               a[static_cast<std::size_t>(besti + bestsize)] ==
                   b[static_cast<std::size_t>(bestj + bestsize)]) {
            ++bestsize;
        }

        matches += bestsize;
        if (alo < besti && blo < bestj) queue.push_back({alo, besti, blo, bestj});
        if (besti + bestsize < ahi && bestj + bestsize < bhi)
            queue.push_back({besti + bestsize, ahi, bestj + bestsize, bhi});
    }
    return 2.0 * static_cast<double>(matches) / static_cast<double>(la + lb);
}

double AppLauncher::score(const std::string& query, const std::string& stem) {
    const std::string name = normalize(stem);
    if (query == name) return 1.0;
    if (starts_with(name, query) || starts_with(query, name)) return 0.9;
    const std::vector<std::string> query_tokens = split_words(query);
    const std::vector<std::string> name_tokens = split_words(name);
    const double ratio = similarity(query, name);
    // İlk anlamlı kelime eşleşmiyorsa benzer son ek yeterli değildir:
    // "obs studio" sorgusu "Roblox Studio" kısayoluna kaymamalı.
    if (!query_tokens.empty() && !contains(name_tokens, query_tokens.front())) {
        if (query_tokens.size() == 1 && ratio >= 0.85) return ratio;
        return 0.0;
    }
    if (!query_tokens.empty()) {
        bool all = true;
        for (const std::string& token : query_tokens) {
            if (!contains(name_tokens, token)) {
                all = false;
                break;
            }
        }
        if (all) return 0.85;
    }
    return ratio;
}

std::string AppLauncher::validate(const std::string& application) {
    const std::string name = util::trim(application);
    if (name.empty() || name.find('\0') != std::string::npos || name.find('\r') != std::string::npos ||
        name.find('\n') != std::string::npos) {
        throw ai::ActionForbidden("Geçerli bir uygulama adı gerekli.");
    }
    if (ends_with(ascii_lower(name), ".lnk") || name.find('/') != std::string::npos ||
        name.find('\\') != std::string::npos) {
        throw ai::ActionForbidden("Uygulama yolu yerine yalnızca uygulama adı kullanılmalı.");
    }
    return name;
}

std::vector<std::string> AppLauncher::tokens(const std::string& text) {
    // re.split(r"[\W_]+", normalize(text)) — boş parçalar atılır.
    const std::string folded = normalize(text);
    std::vector<std::string> out;
    std::string current;
    for (const char raw : folded) {
        const unsigned char c = static_cast<unsigned char>(raw);
        const bool word = std::isalnum(c) != 0 || c >= 0x80;
        if (word) {
            current.push_back(raw);
        } else if (!current.empty()) {
            out.push_back(current);
            current.clear();
        }
    }
    if (!current.empty()) out.push_back(current);
    return out;
}

std::vector<std::string> AppLauncher::app_tokens(const std::string& application) {
    std::vector<std::string> out;
    for (const std::string& token : tokens(application)) {
        if (util::utf8_length(token) < 2 || generic_words().count(token) || contains(out, token)) continue;
        out.push_back(token);
    }
    return out;
}

bool AppLauncher::window_matches(const std::string& application, const std::string& title) {
    const std::vector<std::string> wanted = app_tokens(application);
    if (wanted.empty()) {
        const std::string normalized = normalize(application);
        return !normalized.empty() && normalize(title).find(normalized) != std::string::npos;
    }
    const std::vector<std::string> title_tokens = tokens(title);
    std::vector<std::string> matched;
    for (const std::string& token : wanted) {
        if (contains(title_tokens, token)) matched.push_back(token);
    }
    if (matched.size() == wanted.size()) return true;
    const std::size_t needed = std::max<std::size_t>(1, (wanted.size() + 1) / 2);
    return contains(matched, wanted.front()) && matched.size() >= needed;
}

bool AppLauncher::is_play_label(const std::string& text) {
    const std::string key = join(tokens(text), " ");
    if (key.empty()) return false;
    if (play_labels().count(key)) return true;
    return util::utf8_length(key) <= 20 &&
           (starts_with(key, "play ") || starts_with(key, "oyna ") || starts_with(key, "oyunu "));
}

bool AppLauncher::is_ignored_shortcut(const std::string& normalized_name) {
    const std::vector<std::string> words = split_words(normalized_name);
    for (const std::string& word : kIgnoredShortcutWords) {
        if (contains(words, word)) return true;
    }
    return false;
}

std::string AppLauncher::system_command(const std::string& application) {
    const auto found = system_apps().find(normalize_text(application));
    return found == system_apps().end() ? std::string() : found->second;
}

#ifdef _WIN32
namespace {

std::string u8(const std::wstring& wide) { return util::wide_to_utf8(wide); }
std::wstring wide(const std::string& value) { return util::utf8_to_wide(value); }

std::string last_error_text() {
    const DWORD code = GetLastError();
    wchar_t* buffer = nullptr;
    std::string text = "hata kodu " + std::to_string(code);
    const DWORD written = FormatMessageW(
        FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM |
            FORMAT_MESSAGE_IGNORE_INSERTS,
        nullptr, code, MAKELANGID(LANG_NEUTRAL, SUBLANG_DEFAULT), reinterpret_cast<wchar_t*>(&buffer),
        0, nullptr);
    if (written && buffer) {
        text = u8(std::wstring(buffer, buffer + written));
        text = util::collapse_whitespace(util::trim(text));
    }
    if (buffer) LocalFree(buffer);
    return text;
}

// CreateProcessW komut satırı: boşluk/tırnak içeren argümanlar kabaca sarılır.
std::wstring quote_argument(const std::wstring& value) {
    if (!value.empty() && value.find_first_of(L" \t\"") == std::wstring::npos) return value;
    std::wstring out = L"\"";
    std::size_t slashes = 0;
    for (const wchar_t c : value) {
        if (c == L'\\') {
            ++slashes;
            continue;
        }
        if (c == L'"') {
            out.append(slashes * 2 + 1, L'\\');
            out.push_back(L'"');
        } else {
            out.append(slashes, L'\\');
            out.push_back(c);
        }
        slashes = 0;
    }
    out.append(slashes * 2, L'\\');
    out.push_back(L'"');
    return out;
}

std::wstring command_line_of(const std::vector<std::wstring>& argv) {
    std::wstring line;
    for (std::size_t i = 0; i < argv.size(); ++i) {
        if (i) line += L' ';
        line += quote_argument(argv[i]);
    }
    return line;
}

// subprocess.Popen karşılığı: süreç başlatır, bekiklemez (Python gibi).
bool spawn(const std::vector<std::wstring>& argv, const std::wstring& working_directory,
           std::string& error) {
    if (argv.empty()) return false;
    std::wstring line = command_line_of(argv);
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    PROCESS_INFORMATION process{};
    const BOOL started = CreateProcessW(nullptr, line.data(), nullptr, nullptr, FALSE,
                                        CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT, nullptr,
                                        working_directory.empty() ? nullptr : working_directory.c_str(),
                                        &startup, &process);
    if (!started) {
        error = last_error_text();
        return false;
    }
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return true;
}

// os.startfile / ShellExecuteW karşılığı.
bool shell_execute(const std::wstring& path, const std::wstring& parameters, std::string& error) {
    const HINSTANCE instance =
        ShellExecuteW(nullptr, L"open", path.c_str(), parameters.empty() ? nullptr : parameters.c_str(),
                      nullptr, SW_SHOWNORMAL);
    if (reinterpret_cast<std::uintptr_t>(instance) > 32) return true;
    error = "ShellExecute kodu " + std::to_string(static_cast<long long>(
                                       reinterpret_cast<std::intptr_t>(instance)));
    return false;
}

// Komutu gizli pencereyle çalıştırır, standart çıkışını toplar. Zaman aşımında
// beklemeyi bırakır; süreç sonlandırılmaz (Python timeout gibi öldürme yok).
std::string capture_stdout(const std::vector<std::wstring>& argv, long long timeout_ms) {
    std::string output;
    if (argv.empty()) return output;
    SECURITY_ATTRIBUTES attributes{};
    attributes.nLength = sizeof(attributes);
    attributes.bInheritHandle = TRUE;
    HANDLE read_pipe = nullptr;
    HANDLE write_pipe = nullptr;
    if (!CreatePipe(&read_pipe, &write_pipe, &attributes, 0)) return output;
    SetHandleInformation(read_pipe, HANDLE_FLAG_INHERIT, 0);

    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESTDHANDLES;
    startup.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
    startup.hStdOutput = write_pipe;
    startup.hStdError = GetStdHandle(STD_ERROR_HANDLE);
    PROCESS_INFORMATION process{};
    std::wstring line = command_line_of(argv);
    const BOOL started = CreateProcessW(nullptr, line.data(), nullptr, nullptr, TRUE,
                                        CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT, nullptr,
                                        nullptr, &startup, &process);
    CloseHandle(write_pipe);
    if (!started) {
        CloseHandle(read_pipe);
        return output;
    }
    CloseHandle(process.hThread);

    const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
    bool exited = false;
    for (;;) {
        DWORD available = 0;
        if (PeekNamedPipe(read_pipe, nullptr, 0, nullptr, &available, nullptr) && available > 0) {
            char buffer[32768];
            const DWORD chunk =
                available < static_cast<DWORD>(sizeof(buffer)) ? available : sizeof(buffer);
            DWORD read = 0;
            if (!ReadFile(read_pipe, buffer, chunk, &read, nullptr) || read == 0) break;
            output.append(buffer, read);
            continue;
        }
        exited = WaitForSingleObject(process.hProcess, 30) == WAIT_OBJECT_0;
        if (exited) break;
        if (std::chrono::steady_clock::now() >= deadline) break;
    }
    if (exited) {
        for (;;) {
            char buffer[32768];
            DWORD read = 0;
            if (!ReadFile(read_pipe, buffer, static_cast<DWORD>(sizeof(buffer)), &read, nullptr) ||
                read == 0)
                break;
            output.append(buffer, read);
        }
    }
    CloseHandle(process.hProcess);
    CloseHandle(read_pipe);
    return output;
}

// Python _process_names: psutil.process_iter(["name"])
std::vector<std::string> process_names() {
    std::vector<std::string> names;
    const HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snapshot == INVALID_HANDLE_VALUE) return names;
    PROCESSENTRY32W entry{};
    entry.dwSize = sizeof(entry);
    if (Process32FirstW(snapshot, &entry)) {
        do {
            names.push_back(u8(entry.szExeFile));
        } while (Process32NextW(snapshot, &entry));
    }
    CloseHandle(snapshot);
    return names;
}

// Python _matching_processes: exe gövdesi ilk anlamlı kelimelerden biriyle başlar.
std::vector<std::string> matching_processes(const std::string& application) {
    const std::vector<std::string> wanted = AppLauncher::app_tokens(application);
    if (wanted.empty()) return {};
    std::vector<std::string> matches;
    for (const std::string& name : process_names()) {
        std::string stem;
        for (const std::string& token : AppLauncher::tokens(stem_of(name))) stem += token;
        if (stem.empty()) continue;
        for (const std::string& token : wanted) {
            if (starts_with(stem, token)) {
                matches.push_back(name);
                break;
            }
        }
    }
    return matches;
}

// Python _visible_windows: başlık → en büyük alan; sınıfı görmezden gelinenler yok.
std::map<std::string, long long> visible_windows() {
    static const std::set<std::string> kIgnoredClasses = {"Worker Window", "Shell_TrayWnd", "Progman",
                                                          "CEF-OSC-WIDGET"};
    std::map<std::string, long long> windows;
    for (const WindowInfo& info : collect_windows()) {
        const std::string title = util::trim(info.title);
        if (title.empty() || kIgnoredClasses.count(info.window_class)) continue;
        if (info.rect.size() < 4) continue;
        const long long area = (info.rect[2] - info.rect[0]) * (info.rect[3] - info.rect[1]);
        const long long best = std::max<long long>(0, area);
        const auto found = windows.find(title);
        if (found == windows.end() || best > found->second) windows[title] = best;
    }
    return windows;
}

// Python _wait_for_new_window: yeni açılan en büyük alanlı başlık.
std::string wait_for_new_window(const std::map<std::string, long long>& known, double timeout_seconds,
                                const AppLauncher::StopPredicate& should_stop) {
    const auto deadline = std::chrono::steady_clock::now() +
                          std::chrono::milliseconds(static_cast<long long>(timeout_seconds * 1000.0));
    while (std::chrono::steady_clock::now() < deadline) {
        if (should_stop && should_stop()) throw ai::ActionForbidden("İşlem durduruldu.");
        std::vector<std::pair<long long, std::string>> fresh;
        for (const auto& [title, area] : visible_windows()) {
            if (known.count(title)) continue;
            fresh.emplace_back(area, title);
        }
        if (!fresh.empty()) {
            std::sort(fresh.begin(), fresh.end(),
                      [](const std::pair<long long, std::string>& a,
                         const std::pair<long long, std::string>& b) {
                          if (a.first != b.first) return a.first > b.first;
                          return a.second > b.second;
                      });
            return fresh.front().second;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(300));
    }
    return {};
}

// Python _window_for_process: süreç adı ipucundan görünen ilk başlık.
std::string window_for_process(const std::string& process_hint) {
    const std::string hint = AppLauncher::normalize(process_hint);
    if (hint.empty()) return {};
    for (const WindowInfo& info : collect_windows()) {
        const std::string title = util::trim(info.title);
        if (title.empty()) continue;
        const std::string stem = AppLauncher::normalize(stem_of(info.process));
        if (!stem.empty() && stem.find(hint) != std::string::npos) return title;
    }
    return {};
}

// Python _focus_running: başlıkta geçen adı öne getirir (küçük harf alt dizi).
std::string focus_running(const std::string& application) {
    const std::string needle = normalize_text(application);
    if (needle.empty()) return {};
    for (const WindowInfo& info : collect_windows()) {
        const std::string title = util::trim(info.title);
        if (title.empty() || normalize_text(title).find(needle) == std::string::npos) continue;
        if (!focus_window_handle(info.handle)) continue;
        return title;
    }
    return {};
}

// Python _wait_for_target: başlık eşleşmesi ya da süreç kanıtı.
std::string wait_for_target(const std::string& application, double timeout_seconds,
                            const AppLauncher::StopPredicate& should_stop, bool check_processes) {
    const auto deadline = std::chrono::steady_clock::now() +
                          std::chrono::milliseconds(static_cast<long long>(timeout_seconds * 1000.0));
    long long checks = 0;
    while (std::chrono::steady_clock::now() < deadline) {
        if (should_stop && should_stop())
            throw ai::ActionForbidden("İşlem kullanıcı tarafından durduruldu.");
        for (const auto& [title, area] : visible_windows()) {
            (void)area;
            if (AppLauncher::window_matches(application, title)) return title;
        }
        if (check_processes && checks % 3 == 0) {
            const std::vector<std::string> matches = matching_processes(application);
            if (!matches.empty()) return application + " (süreç: " + matches.front() + ")";
        }
        ++checks;
        std::this_thread::sleep_for(std::chrono::milliseconds(800));
    }
    return {};
}

// Python _shortcut_target: WScript.Shell CreateShortcut TargetPath.
struct ComGuard {
    HRESULT status;
    ComGuard() : status(CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED)) {}
    ~ComGuard() {
        if (SUCCEEDED(status)) CoUninitialize();
    }
};

}  // namespace

std::string AppLauncher::shortcut_target(const fs::path& shortcut) {
    std::string out;
    ComGuard com;
    IShellLinkW* link = nullptr;
    const HRESULT created = CoCreateInstance(CLSID_ShellLink, nullptr, CLSCTX_INPROC_SERVER,
                                             IID_IShellLinkW, reinterpret_cast<void**>(&link));
    if (FAILED(created) || !link) return out;
    IPersistFile* persist = nullptr;
    if (SUCCEEDED(link->QueryInterface(IID_IPersistFile, reinterpret_cast<void**>(&persist)))) {
        if (SUCCEEDED(persist->Load(shortcut.c_str(), STGM_READ))) {
            wchar_t buffer[MAX_PATH] = {0};
            if (SUCCEEDED(link->GetPath(buffer, MAX_PATH, nullptr, SLGP_RAWPATH)) && buffer[0])
                out = u8(buffer);
        }
        persist->Release();
    }
    link->Release();
    return out;
}

#else  // !_WIN32 — keşif ve başlatma Win32 gerektirir.

std::string AppLauncher::shortcut_target(const fs::path& shortcut) {
    (void)shortcut;
    return {};
}

#endif  // _WIN32

// ------------------------------------------------------------------- keşif

#ifdef _WIN32
namespace {

bool has_extension(const fs::path& path, const std::string& extension) {
    std::string tail = path.extension().string();
    for (char& c : tail) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return tail == extension;
}

void collect_shortcuts(const fs::path& directory, std::vector<fs::path>& out) {
    std::error_code ec;
    if (!fs::is_directory(directory, ec)) return;
    fs::recursive_directory_iterator it(directory, fs::directory_options::skip_permission_denied, ec);
    if (ec) return;
    const fs::recursive_directory_iterator end;
    for (; it != end; it.increment(ec)) {
        if (ec) {
            ec.clear();
            continue;
        }
        std::error_code inner;
        if (it->is_symlink(inner)) continue;
        if (!it->is_regular_file(inner)) continue;
        if (inner) continue;
        if (has_extension(it->path(), ".lnk")) out.push_back(it->path());
    }
}

std::string readable_stem(const fs::path& path) { return stem_of(u8(path.filename())); }

}  // namespace

std::optional<fs::path> AppLauncher::find_shortcut(const std::string& application) const {
    const std::string query = normalize(application);
    if (query.empty()) return std::nullopt;
    struct Hit {
        double score;
        fs::path path;
        std::string stem;
    };
    std::vector<Hit> matches;
    for (const fs::path& directory : start_menu_dirs_) {
        std::vector<fs::path> shortcuts;
        collect_shortcuts(directory, shortcuts);
        for (const fs::path& shortcut : shortcuts) {
            const std::string stem = readable_stem(shortcut);
            const std::string name = normalize(stem);
            if (is_ignored_shortcut(name)) continue;
            const double value = score(query, stem);
            if (value >= 0.55) matches.push_back({value, shortcut, stem});
        }
    }
    if (matches.empty()) return std::nullopt;
    std::stable_sort(matches.begin(), matches.end(),
                     [](const Hit& a, const Hit& b) { return a.score > b.score; });
    if (matches.size() > 1 && matches.front().score < 1.0 &&
        std::abs(matches.front().score - matches[1].score) < 0.03) {
        std::vector<std::string> names;
        for (std::size_t i = 0; i < matches.size() && i < 4; ++i) names.push_back(matches[i].stem);
        throw ai::ActionForbidden("Uygulama adı belirsiz: " + application + ". Olası eşleşmeler: " +
                                  join(names, ", "));
    }
    return matches.front().path;
}

namespace {

fs::path path_from_u8(const std::string& value) { return fs::path(util::utf8_to_wide(value)); }

void scan_app_paths(HKEY hive, const wchar_t* prefix, const std::string& query,
                    std::vector<std::tuple<double, std::string, std::string>>& matches) {
    HKEY root = nullptr;
    if (RegOpenKeyExW(hive, prefix, 0, KEY_READ, &root) != ERROR_SUCCESS) return;
    DWORD subkeys = 0;
    DWORD values = 0;
    if (RegQueryInfoKeyW(root, nullptr, nullptr, nullptr, &subkeys, nullptr, nullptr, &values,
                        nullptr, nullptr, nullptr, nullptr) != ERROR_SUCCESS) {
        RegCloseKey(root);
        return;
    }
    for (DWORD index = 0; index < subkeys; ++index) {
        wchar_t name[256] = {0};
        DWORD length = 256;
        if (RegEnumKeyExW(root, index, name, &length, nullptr, nullptr, nullptr, nullptr) !=
            ERROR_SUCCESS)
            continue;
        const std::string key_name = u8(std::wstring(name, length));
        HKEY key = nullptr;
        if (RegOpenKeyExW(root, name, 0, KEY_READ, &key) != ERROR_SUCCESS) continue;
        wchar_t buffer[1024] = {0};
        DWORD size = sizeof(buffer);
        DWORD type = 0;
        const LONG read = RegQueryValueExW(key, nullptr, nullptr, &type,
                                          reinterpret_cast<BYTE*>(buffer), &size);
        RegCloseKey(key);
        if (read != ERROR_SUCCESS || (type != REG_SZ && type != REG_EXPAND_SZ) || size < 2) continue;
        std::wstring raw(buffer, (size / sizeof(wchar_t)) - 1);
        // Python str(...).strip('"')
        std::size_t first = raw.find_first_not_of(L'"');
        std::size_t last = raw.find_last_not_of(L'"');
        if (first != std::wstring::npos && last != std::wstring::npos)
            raw = raw.substr(first, last - first + 1);
        const std::string executable = u8(raw);
        std::error_code ec;
        if (executable.empty() || !fs::is_regular_file(path_from_u8(executable), ec)) continue;
        const double value = AppLauncher::score(query, key_name);
        if (value >= 0.55)
            matches.emplace_back(value, executable, stem_of(key_name));
    }
    RegCloseKey(root);
}

}  // namespace

std::optional<std::pair<std::string, std::string>> AppLauncher::find_registered_app(
    const std::string& application) const {
    const std::string query = normalize(application);
    if (query.empty()) return std::nullopt;
    std::vector<std::tuple<double, std::string, std::string>> matches;
    const wchar_t* prefixes[] = {
        L"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths",
        L"SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\App Paths"};
    for (HKEY hive : {HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE}) {
        for (const wchar_t* prefix : prefixes) scan_app_paths(hive, prefix, query, matches);
    }
    if (matches.empty()) return std::nullopt;
    std::sort(matches.begin(), matches.end(),
              [](const std::tuple<double, std::string, std::string>& a,
                 const std::tuple<double, std::string, std::string>& b) {
                  if (std::get<0>(a) != std::get<0>(b)) return std::get<0>(a) > std::get<0>(b);
                  if (std::get<1>(a) != std::get<1>(b)) return std::get<1>(a) > std::get<1>(b);
                  return std::get<2>(a) > std::get<2>(b);
              });
    return std::make_pair(std::get<1>(matches.front()), std::get<2>(matches.front()));
}

std::optional<std::pair<std::string, std::string>> AppLauncher::find_store_app(
    const std::string& application) const {
    const std::string stdout_text = capture_stdout(
        {L"powershell", L"-NoProfile", L"-NonInteractive", L"-Command",
         L"Get-StartApps | ConvertTo-Json -Compress"},
        12000);
    if (stdout_text.empty()) return std::nullopt;
    json data;
    try {
        data = json::parse(util::utf8_sanitize(stdout_text));
    } catch (const std::exception&) {
        return std::nullopt;
    }
    if (data.is_object()) data = json::array({data});
    if (!data.is_array()) return std::nullopt;
    const std::string query = normalize(application);
    if (query.empty()) return std::nullopt;
    std::vector<std::tuple<double, std::string, std::string>> matches;
    for (const json& item : data) {
        if (!item.is_object()) continue;
        const std::string name = item.value("Name", std::string());
        // Python _score(query, Path(name)) → Path(name).stem kullanılır.
        const double value = score(query, stem_of(name));
        if (value >= 0.55)
            matches.emplace_back(value, item.value("AppID", std::string()), name);
    }
    if (matches.empty()) return std::nullopt;
    std::sort(matches.begin(), matches.end(),
              [](const std::tuple<double, std::string, std::string>& a,
                 const std::tuple<double, std::string, std::string>& b) {
                  if (std::get<0>(a) != std::get<0>(b)) return std::get<0>(a) > std::get<0>(b);
                  if (std::get<1>(a) != std::get<1>(b)) return std::get<1>(a) > std::get<1>(b);
                  return std::get<2>(a) > std::get<2>(b);
              });
    const std::string app_id = std::get<1>(matches.front());
    if (app_id.empty()) return std::nullopt;
    return std::make_pair(app_id, std::get<2>(matches.front()));
}

namespace {

std::string package_product(const fs::path& package) {
    const std::string name = u8(package.filename());
    const std::size_t underscore = name.find('_');
    const std::string identifier = underscore == std::string::npos ? name : name.substr(0, underscore);
    const std::size_t dot = identifier.rfind('.');
    return AppLauncher::normalize(dot == std::string::npos ? identifier : identifier.substr(dot + 1));
}

// Python _winget_executable: dört desen, 250 dosya sınırı, skor sıralaması.
std::string winget_executable(const fs::path& package, const std::string& compact_query,
                              const std::vector<std::string>& wanted) {
    struct Candidate {
        double score;
        std::size_t name_length;
        std::string path;
    };
    std::vector<Candidate> candidates;
    std::set<std::string> seen;

    auto consider = [&](const fs::path& path) {
        const std::string key = u8(path);
        if (seen.count(key)) return;
        if (seen.size() > 250) return;
        seen.insert(key);
        // Python: normalize(path.stem).replace(" ", "")
        std::string stem = AppLauncher::normalize(stem_of(u8(path.filename())));
        stem.erase(std::remove(stem.begin(), stem.end(), ' '), stem.end());
        if (stem.empty()) return;
        for (const std::string& skip : kExeSkipWords) {
            if (stem.find(skip) != std::string::npos) return;
        }
        double value = 0.0;
        if (stem == compact_query) value = 100.0;
        else if (starts_with(stem, compact_query)) value = 90.0;
        else if (!compact_query.empty() && stem.find(compact_query) != std::string::npos) value = 80.0;
        else {
            bool hit = false;
            for (const std::string& token : wanted) {
                if (starts_with(stem, token)) {
                    hit = true;
                    break;
                }
            }
            if (!hit) return;
            value = 60.0 - static_cast<double>(std::min<std::size_t>(stem.size(), 20));
        }
        candidates.push_back({value, u8(path.filename()).size(), key});
    };

    // Python glob desenleri: *.exe, */*.exe, bin/**/*.exe, **/bin/*.exe.
    // Her desen kendi hata kodunu taşır; bir klasör okunamazsa sonraki desen
    // bundan etkilenmez (Python'ın desen başına try/except'i gibi).
    std::error_code scan;
    for (const auto& entry : fs::directory_iterator(package, scan)) {
        std::error_code inner;
        if (entry.is_regular_file(inner) && has_extension(entry.path(), ".exe"))
            consider(entry.path());
    }
    scan.clear();
    for (const auto& entry : fs::directory_iterator(package, scan)) {
        std::error_code inner;
        if (!entry.is_directory(inner)) continue;
        std::error_code child_scan;
        for (const auto& child : fs::directory_iterator(entry.path(), child_scan)) {
            std::error_code child_inner;
            if (child.is_regular_file(child_inner) && has_extension(child.path(), ".exe"))
                consider(child.path());
        }
    }
    scan.clear();
    {
        const fs::path bin = package / "bin";
        std::error_code exists;
        if (fs::is_directory(bin, exists)) {
            fs::recursive_directory_iterator it(bin, fs::directory_options::skip_permission_denied,
                                               scan);
            const fs::recursive_directory_iterator end;
            for (; it != end; it.increment(scan)) {
                if (scan) {
                    scan.clear();
                    continue;
                }
                std::error_code inner;
                if (it->is_regular_file(inner) && has_extension(it->path(), ".exe"))
                    consider(it->path());
            }
            scan.clear();
        }
    }
    {
        fs::recursive_directory_iterator it(package, fs::directory_options::skip_permission_denied,
                                           scan);
        const fs::recursive_directory_iterator end;
        for (; it != end; it.increment(scan)) {
            if (scan) {
                scan.clear();
                continue;
            }
            std::error_code inner;
            if (!it->is_directory(inner)) continue;
            if (ascii_lower(u8(it->path().filename())) != "bin") continue;
            std::error_code child_scan;
            for (const auto& child : fs::directory_iterator(it->path(), child_scan)) {
                std::error_code child_inner;
                if (child.is_regular_file(child_inner) && has_extension(child.path(), ".exe"))
                    consider(child.path());
            }
        }
        scan.clear();
    }

    if (candidates.empty()) return {};
    std::stable_sort(candidates.begin(), candidates.end(),
                     [](const Candidate& a, const Candidate& b) {
                         if (a.score != b.score) return a.score > b.score;
                         if (a.name_length != b.name_length) return a.name_length < b.name_length;
                         return a.path < b.path;
                     });
    return candidates.front().path;
}

long long modified_ns(const fs::path& path) {
    std::error_code ec;
    const auto time = fs::last_write_time(path, ec);
    if (ec) return 0;
    const auto since_epoch = std::chrono::duration_cast<std::chrono::nanoseconds>(
        time.time_since_epoch());
    return since_epoch.count();
}

}  // namespace

std::optional<std::pair<std::string, std::string>> AppLauncher::find_winget_app(
    const std::string& application) const {
    std::error_code ec;
    if (!fs::is_directory(winget_root_, ec)) return std::nullopt;
    std::vector<fs::path> packages;
    for (const auto& entry : fs::directory_iterator(winget_root_, ec)) {
        if (ec) break;
        std::error_code inner;
        if (entry.is_directory(inner)) packages.push_back(entry.path());
    }
    const std::string query = normalize(application);
    std::string compact;
    for (const char c : query) {
        if (c != ' ') compact.push_back(c);
    }
    if (compact.size() < 3) return std::nullopt;
    std::vector<std::string> wanted = app_tokens(application);
    if (wanted.empty()) wanted = split_words(query);

    std::vector<std::tuple<long long, std::string, std::string>> matches;
    for (const fs::path& package : packages) {
        const std::string product = package_product(package);
        if (product.empty()) continue;
        if (!(product == compact || starts_with(product, compact) ||
              product.find(compact) != std::string::npos)) {
            // Kullanıcı ürün adı yerine exe adını söyleyebilir (ör. "obs64").
            if (compact.size() < 3 || !starts_with(product, compact.substr(0, 3))) continue;
        }
        const std::string executable = winget_executable(package, compact, wanted);
        if (executable.empty()) continue;
        matches.emplace_back(modified_ns(package), product, executable);
    }
    if (matches.empty()) return std::nullopt;
    std::sort(matches.begin(), matches.end(),
              [](const std::tuple<long long, std::string, std::string>& a,
                 const std::tuple<long long, std::string, std::string>& b) {
                  if (std::get<0>(a) != std::get<0>(b)) return std::get<0>(a) > std::get<0>(b);
                  if (std::get<1>(a) != std::get<1>(b)) return std::get<1>(a) > std::get<1>(b);
                  return std::get<2>(a) > std::get<2>(b);
              });
    return std::make_pair(std::get<2>(matches.front()), std::get<1>(matches.front()));
}

#else  // !_WIN32

std::optional<fs::path> AppLauncher::find_shortcut(const std::string& application) const {
    (void)application;
    return std::nullopt;
}

std::optional<std::pair<std::string, std::string>> AppLauncher::find_registered_app(
    const std::string& application) const {
    (void)application;
    return std::nullopt;
}

std::optional<std::pair<std::string, std::string>> AppLauncher::find_store_app(
    const std::string& application) const {
    (void)application;
    return std::nullopt;
}

std::optional<std::pair<std::string, std::string>> AppLauncher::find_winget_app(
    const std::string& application) const {
    (void)application;
    return std::nullopt;
}

#endif  // _WIN32

std::vector<std::string> AppLauncher::nearest_candidates(const std::string& application,
                                                        std::size_t count) const {
#ifdef _WIN32
    const std::string query = normalize(application);
    if (query.empty()) return {};
    std::vector<std::pair<double, std::string>> ranked;
    for (const fs::path& directory : start_menu_dirs_) {
        std::vector<fs::path> shortcuts;
        collect_shortcuts(directory, shortcuts);
        for (const fs::path& shortcut : shortcuts) {
            const std::string stem = readable_stem(shortcut);
            if (is_ignored_shortcut(normalize(stem))) continue;
            const double value = score(query, stem);
            if (value >= 0.30) ranked.emplace_back(value, stem);
        }
    }
    for (HKEY hive : {HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE}) {
        const wchar_t* prefixes[] = {
            L"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths",
            L"SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\App Paths"};
        for (const wchar_t* prefix : prefixes) {
            std::vector<std::tuple<double, std::string, std::string>> found;
            scan_app_paths(hive, prefix, query, found);
            for (const auto& item : found) ranked.emplace_back(std::get<0>(item), std::get<2>(item));
        }
    }
    std::stable_sort(ranked.begin(), ranked.end(),
                     [](const std::pair<double, std::string>& a,
                        const std::pair<double, std::string>& b) {
                         if (a.first != b.first) return a.first > b.first;
                         return a.second < b.second;
                     });
    std::vector<std::string> out;
    for (const auto& item : ranked) {
        if (contains(out, item.second)) continue;
        out.push_back(item.second);
        if (out.size() >= count) break;
    }
    return out;
#else
    (void)application;
    (void)count;
    return {};
#endif
}

// ------------------------------------------------------------------ başlatma

AppLauncher::AppLauncher() {
#ifdef _WIN32
    const auto env_path = [](const wchar_t* name) {
        const wchar_t* value = _wgetenv(name);
        return value ? std::wstring(value) : std::wstring();
    };
    const fs::path appdata(env_path(L"APPDATA"));
    const fs::path programdata(env_path(L"PROGRAMDATA"));
    const fs::path localappdata(env_path(L"LOCALAPPDATA"));
    start_menu_dirs_ = {
        appdata / L"Microsoft" / L"Windows" / L"Start Menu" / L"Programs",
        programdata / L"Microsoft" / L"Windows" / L"Start Menu" / L"Programs"};
    winget_root_ = localappdata / L"Microsoft" / L"WinGet" / L"Packages";
#else
    start_menu_dirs_ = {};
#endif
}

AppLauncher::AppLauncher(std::vector<fs::path> start_menu_dirs, fs::path winget_root)
    : start_menu_dirs_(std::move(start_menu_dirs)), winget_root_(std::move(winget_root)) {}

#ifdef _WIN32
namespace {

// Python _settle_window: başlık eşleşmiyorsa süre tanır, sonra oyun istemcisi
// "Oyna" yoluna gider. UIA/OCR tıklaması bu derlemede yok → dürüst gerekçe.
std::string settle_window(const std::string& application, const std::string& title,
                          const AppLauncher::StopPredicate& should_stop) {
    if (AppLauncher::window_matches(application, title)) return title;
    const std::string grace = wait_for_target(application, 4.0, should_stop, false);
    if (!grace.empty()) return grace + " (" + title + " açıldıktan sonra doğrulandı)";
    const std::string reason = title +
                               " açıldı; oyunu başlatmak için Oyna/Play düğmesine basılmalı "
                               "(UIA tıklaması bu derlemede henüz bağlı değil)";
    const std::vector<std::string> running = matching_processes(application);
    if (!running.empty()) {
        return application + " (süreç çalışıyor: " + running.front() +
               "; görünür pencere doğrulanamadı)";
    }
    throw ai::ActionForbidden(
        reason + ". Kullanıcıya sormadan inspect_ui veya find_text_on_screen ile 'Oyna'/'Play' "
                 "düğmesini tıklamayı ve oyun penceresini/sürecini doğrulamayı dene.");
}

// Python _finish_launch: yeni pencere → süreç ipucu → doğrula → çalışanı bul.
std::string finish_launch(const std::map<std::string, long long>& known, const std::string& application,
                          const std::string& process_hint,
                          const AppLauncher::StopPredicate& should_stop) {
    std::string title = wait_for_new_window(known, 12.0, should_stop);
    if (title.empty() && !process_hint.empty()) title = window_for_process(process_hint);
    if (!title.empty()) return settle_window(application, title, should_stop);
    const std::string running = focus_running(application);
    if (!running.empty()) return running;
    if (!matching_processes(application).empty())
        return application + " (çalışan süreç doğrulandı)";
    throw ai::ActionForbidden("Uygulama başlatıldı ama penceresi açılmadı: " + application);
}

}  // namespace

std::string AppLauncher::launch(const std::string& application,
                                const StopPredicate& should_stop) {
    const std::string name = validate(application);
    const std::string already = focus_running(name);
    if (!already.empty()) return already;

    std::map<std::string, long long> known = visible_windows();
    std::vector<std::string> errors;

    const std::string system = system_command(name);
    if (!system.empty()) {
        std::string error;
        if (!spawn({wide(system)}, {}, error)) throw ai::SafetyError("Uygulama açılamadı: " + error);
        return finish_launch(known, name, stem_of(system), should_stop);
    }

    std::optional<fs::path> shortcut;
    try {
        shortcut = find_shortcut(name);
    } catch (const ai::ActionForbidden&) {
        throw;  // "Uygulama adı belirsiz" modeli yönlendirir, yutulmaz.
    }
    if (shortcut) {
        std::string error;
        const std::string target = shortcut_target(*shortcut);
        if (!shell_execute(shortcut->wstring(), {}, error))
            errors.push_back("kısayol açılamadı: " + error);
        else
            return finish_launch(known, name,
                                 stem_of(target.empty() ? readable_stem(*shortcut)
                                                        : u8(fs::path(util::utf8_to_wide(target)).filename())),
                                 should_stop);
    }

    if (const auto registered = find_registered_app(name)) {
        const std::string& executable = registered->first;
        std::string error;
        const fs::path path = fs::path(util::utf8_to_wide(executable));
        if (!spawn({path.wstring()}, path.parent_path().wstring(), error))
            errors.push_back("kayıtlı uygulama çalıştırılamadı: " + error);
        else
            return finish_launch(known, name, stem_of(u8(path.filename())), should_stop);
    }

    if (const auto store = find_store_app(name)) {
        std::string error;
        const std::wstring uri = L"shell:AppsFolder\\" + wide(store->first);
        if (!spawn({L"explorer.exe", uri}, {}, error))
            throw ai::SafetyError("Uygulama açılamadı: " + error);
        return finish_launch(known, name, {}, should_stop);
    }

    if (const auto winget = find_winget_app(name)) {
        const std::string& executable = winget->first;
        std::string error;
        const fs::path path = fs::path(util::utf8_to_wide(executable));
        if (!spawn({path.wstring()}, path.parent_path().wstring(), error))
            errors.push_back("winget uygulaması çalıştırılamadı: " + error);
        else
            return finish_launch(known, name, stem_of(u8(path.filename())), should_stop);
    }

    const std::vector<std::string> candidates = nearest_candidates(name, 3);
    std::string detail = errors.empty() ? std::string()
                                       : " Ayrıntı: " + join(errors, "; ") + ".";
    std::string hint;
    if (!candidates.empty()) hint = " En yakın adaylar: " + join(candidates, ", ") + ".";
    throw ai::ActionForbidden(
        "'" + name + "' adıyla eşleşen bir kısayol veya kayıt bulunamadı." + detail + hint +
        " Bu sonuç uygulamanın kurulu olmadığını kanıtlamaz; tam adla tekrar dene, "
        "check_capabilities veya install_dependency ile kurulum durumunu doğrula. "
        "Kullanıcıya 'kurulu değil' demeden önce alternatif keşif yöntemlerini uygula.");
}

std::string AppLauncher::open_url(const std::string& application, const std::string& url) {
    const std::string name = validate(application);
    const std::string scheme = normalize_text(url.substr(0, url.find(':')));
    if (scheme != "http" && scheme != "https")
        throw ai::ActionForbidden("Yalnızca http/https adresleri açılabilir.");

    std::string executable;
    std::string name_out = name;
    if (const auto shortcut = find_shortcut(name)) {
        executable = shortcut_target(*shortcut);
        name_out = readable_stem(*shortcut);
    }
    if (executable.empty()) {
        if (const auto registered = find_registered_app(name)) {
            executable = registered->first;
            name_out = registered->second;
        }
    }
    std::error_code ec;
    if (executable.empty() || !fs::is_regular_file(fs::path(util::utf8_to_wide(executable)), ec))
        throw ai::ActionForbidden("Bu uygulamanın çalıştırılabilir yolu bulunamadı: " + name);
    std::string error;
    if (!spawn({util::utf8_to_wide(executable), util::utf8_to_wide(url)}, {}, error))
        throw ai::SafetyError("Uygulama açılamadı: " + error);
    return name_out + ": " + url;
}

#else  // !_WIN32

std::string AppLauncher::launch(const std::string& application, const StopPredicate& should_stop) {
    (void)application;
    (void)should_stop;
    throw ai::ActionForbidden("Uygulama başlatma yalnızca Windows sürümünde bağlıdır.");
}

std::string AppLauncher::open_url(const std::string& application, const std::string& url) {
    (void)application;
    (void)url;
    throw ai::ActionForbidden("Uygulama başlatma yalnızca Windows sürümünde bağlıdır.");
}

#endif  // _WIN32

}  // namespace jarvis::tools
