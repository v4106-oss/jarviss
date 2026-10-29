#include "jarvis/ai/executor.hpp"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <regex>
#include <set>
#include <stdexcept>
#include <thread>
#include <unordered_map>
#include <cwctype>

#include "jarvis/core/util.hpp"
#include "jarvis/core/win_path.hpp"
#include "jarvis/tools/screen_capture.hpp"
#include "jarvis/tools/ui_automation.hpp"
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
#endif

namespace fs = std::filesystem;
using jarvis::util::utf8_length;
using jarvis::util::utf8_take;
using nlohmann::json;

namespace jarvis::ai {
namespace {

std::string u8s(const fs::path& path) {
    std::u8string raw = path.u8string();
    std::string out;
    out.reserve(raw.size());
    for (char8_t c : raw) out.push_back(static_cast<char>(c));
    return out;
}

fs::path path_from_u8(const std::string& utf8) {
#ifdef _WIN32
    return fs::path(jarvis::util::utf8_to_wide(utf8));
#else
    return fs::path(utf8);
#endif
}

std::string ascii_lower(std::string value) {
    for (char& c : value) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return value;
}

// --- argüman okuyucuları (Python str(args.get(...))) -------------------------

bool arg_missing(const json& args, const char* key) {
    return !args.is_object() || !args.contains(key) || args[key].is_null();
}

std::string arg_text(const json& args, const char* key) {
    if (arg_missing(args, key)) return {};
    const json& value = args.at(key);
    if (value.is_string()) return value.get<std::string>();
    return value.dump();
}

std::string arg_text_or(const json& args, const char* key, const std::string& fallback) {
    std::string value = util::trim(arg_text(args, key));
    return value.empty() ? fallback : value;
}

bool arg_yes(const std::string& folded) {
    static const std::set<std::string> kTrue = {"true", "1", "evet", "acik", "açık", "on"};
    return kTrue.count(folded) > 0;
}

bool arg_no(const std::string& folded) {
    static const std::set<std::string> kFalse = {"false", "0", "hayir", "hayır", "kapali", "kapalı",
                                                 "off", ""};
    return kFalse.count(folded) > 0;
}

// Python bool(x) yerine daha güvenli karşılık: "false"/"0" gibi metinler yanlışlıkla
// üzerine yazma onayı sayılmaz.
bool arg_flag(const json& args, const char* key, bool fallback) {
    if (arg_missing(args, key)) return fallback;
    const json& value = args.at(key);
    if (value.is_boolean()) return value.get<bool>();
    if (value.is_number()) return value.get<double>() != 0.0;
    const std::string folded = ascii_lower(util::trim(value.is_string() ? value.get<std::string>()
                                                                        : value.dump()));
    if (arg_yes(folded)) return true;
    if (arg_no(folded)) return false;
    return !folded.empty();
}

std::optional<long long> arg_int(const json& args, const char* key) {
    if (arg_missing(args, key)) return std::nullopt;
    const json& value = args.at(key);
    if (value.is_number_integer()) return value.get<long long>();
    if (value.is_number()) return static_cast<long long>(value.get<double>());
    if (value.is_boolean()) return value.get<bool>() ? 1 : 0;
    if (value.is_string()) {
        const std::string text = util::trim(value.get<std::string>());
        try {
            std::size_t used = 0;
            const long long parsed = std::stoll(text, &used);
            if (used == text.size()) return parsed;
        } catch (const std::exception&) {
        }
    }
    return std::nullopt;
}

// --- küçük metin/yol yardımcıları -------------------------------------------

std::string strip(const std::string& value) { return util::trim(value); }

// Python urllib.parse.quote_plus karşılığı (harf/rakım ve -._~ dışındaki her şey).
std::string quote_plus(const std::string& value) {
    static const char* kHex = "0123456789ABCDEF";
    std::string out;
    for (unsigned char byte : value) {
        const bool safe = (byte >= 'A' && byte <= 'Z') || (byte >= 'a' && byte <= 'z') ||
                          (byte >= '0' && byte <= '9') || byte == '-' || byte == '.' ||
                          byte == '_' || byte == '~';
        if (safe) {
            out.push_back(static_cast<char>(byte));
        } else if (byte == ' ') {
            out.push_back('+');
        } else {
            out.push_back('%');
            out.push_back(kHex[byte >> 4]);
            out.push_back(kHex[byte & 0x0F]);
        }
    }
    return out;
}

std::string url_scheme(const std::string& url) {
    const std::size_t sep = url.find("://");
    if (sep == std::string::npos) return {};
    return ascii_lower(url.substr(0, sep));
}

std::string not_wired(const std::string& tool) {
    return "❌ Bu araç bu C++ sürümünde henüz bağlı değil: " + tool +
           ". Yanıtında bunu açıkça söyle ve başka araç dene.";
}

// --- Windows süreç koşturucu -------------------------------------------------

#ifdef _WIN32
std::wstring quote_argument(const std::wstring& argument) {
    if (argument.empty()) return L"\"\"";
    if (argument.find_first_of(L" \t\"") == std::wstring::npos) return argument;
    std::wstring out;
    out.push_back(L'"');
    std::size_t i = 0;
    while (i < argument.size()) {
        std::size_t slashes = 0;
        while (i + slashes < argument.size() && argument[i + slashes] == L'\\') ++slashes;
        if (i + slashes == argument.size()) {  // sonda: tırnak yorumlanmasın diye iki katına çıkar
            out.append(slashes * 2, L'\\');
            break;
        }
        if (argument[i + slashes] == L'"') {
            out.append(slashes * 2 + 1, L'\\');
            out.push_back(L'"');
        } else {
            out.append(slashes, L'\\');
            out.push_back(argument[i + slashes]);
        }
        i += slashes + 1;
    }
    out.push_back(L'"');
    return out;
}

std::wstring command_line(const std::vector<std::string>& argv) {
    std::wstring line;
    for (std::size_t i = 0; i < argv.size(); ++i) {
        if (i) line.push_back(L' ');
        line += quote_argument(jarvis::util::utf8_to_wide(argv[i]));
    }
    return line;
}

// Ortam bloğu: GEMINI_API_KEY asla çocuk sürece geçmez; Python UTF-8'e zorlanır.
std::wstring environment_block() {
    std::vector<std::wstring> entries;
    LPWCH raw = GetEnvironmentStringsW();
    if (raw) {
        const wchar_t* cursor = raw;
        while (*cursor) {
            std::wstring item(cursor);
            entries.push_back(item);
            cursor += item.size() + 1;
        }
        FreeEnvironmentStringsW(raw);
    }
    std::wstring block;
    for (const std::wstring& item : entries) {
        std::wstring name = item.substr(0, item.find(L'='));
        std::wstring lower = name;
        for (wchar_t& c : lower) c = static_cast<wchar_t>(std::towlower(c));
        if (lower == L"GEMINI_API_KEY" || lower == L"PYTHONIOENCODING") continue;
        block += item;
        block.push_back(L'\0');
    }
    block += L"PYTHONIOENCODING=utf-8";
    block.push_back(L'\0');
    block.push_back(L'\0');
    return block;
}

struct ProcessResult {
    bool killed = false;
    bool timed_out = false;
    bool failed = false;
    int code = -1;
    std::string output;
    std::string errors;
};

void drain_nonblocking(HANDLE handle, std::string& sink, bool& ended) {
    ended = false;
    DWORD available = 0;
    if (!PeekNamedPipe(handle, nullptr, 0, nullptr, &available, nullptr)) {
        ended = true;
        return;
    }
    while (available > 0) {
        char buffer[32768];
        const DWORD chunk = available < static_cast<DWORD>(sizeof(buffer))
                                ? available
                                : static_cast<DWORD>(sizeof(buffer));
        DWORD read = 0;
        if (!ReadFile(handle, buffer, chunk, &read, nullptr) || read == 0) {
            ended = true;
            return;
        }
        sink.append(buffer, read);
        available -= read;
    }
}

// Süreç kapandıktan sonra boruyu sonuna kadar okur; yazar kalmadığı için bloklamaz.
void drain_to_end(HANDLE handle, std::string& sink) {
    for (;;) {
        char buffer[32768];
        DWORD read = 0;
        if (!ReadFile(handle, buffer, static_cast<DWORD>(sizeof(buffer)), &read, nullptr) ||
            read == 0) {
            return;
        }
        sink.append(buffer, read);
    }
}

ProcessResult run_process(const std::vector<std::string>& argv, const fs::path& working_directory,
                          long long timeout_ms, const KillSwitch& kill) {
    ProcessResult result;
    if (argv.empty()) {
        result.failed = true;
        return result;
    }
    SECURITY_ATTRIBUTES attributes{};
    attributes.nLength = sizeof(attributes);
    attributes.bInheritHandle = TRUE;

    HANDLE stdout_read = nullptr, stdout_write = nullptr;
    HANDLE stderr_read = nullptr, stderr_write = nullptr;
    if (!CreatePipe(&stdout_read, &stdout_write, &attributes, 0) ||
        !CreatePipe(&stderr_read, &stderr_write, &attributes, 0)) {
        result.failed = true;
        return result;
    }
    SetHandleInformation(stdout_read, HANDLE_FLAG_INHERIT, 0);
    SetHandleInformation(stderr_read, HANDLE_FLAG_INHERIT, 0);

    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESTDHANDLES;
    startup.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
    startup.hStdOutput = stdout_write;
    startup.hStdError = stderr_write;

    PROCESS_INFORMATION process{};
    std::wstring line = command_line(argv);
    std::wstring env = environment_block();
    std::wstring directory = working_directory.empty() ? std::wstring() : working_directory.wstring();

    const BOOL started = CreateProcessW(
        nullptr, line.data(), nullptr, nullptr, TRUE,
        CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT, env.data(),
        directory.empty() ? nullptr : directory.c_str(), &startup, &process);

    CloseHandle(stdout_write);
    CloseHandle(stderr_write);

    if (!started) {
        CloseHandle(stdout_read);
        CloseHandle(stderr_read);
        result.failed = true;
        return result;
    }
    CloseHandle(process.hThread);

    const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
    bool exited = false;
    while (!exited) {
        bool stdout_done = false;
        bool stderr_done = false;
        drain_nonblocking(stdout_read, result.output, stdout_done);
        drain_nonblocking(stderr_read, result.errors, stderr_done);

        if (kill.is_set()) {
            TerminateProcess(process.hProcess, 1);
            result.killed = true;
            break;
        }
        exited = WaitForSingleObject(process.hProcess, 30) == WAIT_OBJECT_0;
        if (exited) break;
        if (std::chrono::steady_clock::now() >= deadline) {
            TerminateProcess(process.hProcess, 1);
            result.timed_out = true;
            break;
        }
    }

    DWORD exit_code = STILL_ACTIVE;
    if (GetExitCodeProcess(process.hProcess, &exit_code)) result.code = static_cast<int>(exit_code);
    if (!result.killed && !result.timed_out) {
        drain_to_end(stdout_read, result.output);
        drain_to_end(stderr_read, result.errors);
    }
    CloseHandle(stdout_read);
    CloseHandle(stderr_read);
    CloseHandle(process.hProcess);
    return result;
}

void send_media_key(UINT virtual_key) {
    keybd_event(static_cast<BYTE>(virtual_key), 0, 0, 0);
    keybd_event(static_cast<BYTE>(virtual_key), 0, KEYEVENTF_KEYUP, 0);
}

bool open_clipboard() {
    for (int attempt = 0; attempt < 6; ++attempt) {
        if (OpenClipboard(nullptr)) return true;
        Sleep(40);
    }
    return false;
}

std::string clipboard_read_text() {
    if (!open_clipboard()) return {};
    std::string text;
    HANDLE data = GetClipboardData(CF_UNICODETEXT);
    if (data) {
        const wchar_t* wide = static_cast<const wchar_t*>(GlobalLock(data));
        if (wide) {
            text = jarvis::util::wide_to_utf8(std::wstring(wide));
            GlobalUnlock(data);
        }
    }
    CloseClipboard();
    return text;
}

void clipboard_write_text(const std::string& utf8) {
    const std::wstring wide = jarvis::util::utf8_to_wide(utf8);
    const SIZE_T bytes = (wide.size() + 1) * sizeof(wchar_t);
    HANDLE memory = GlobalAlloc(GMEM_MOVEABLE, bytes);
    if (!memory) throw std::runtime_error("Pano belleği ayrılamadı.");
    wchar_t* target = static_cast<wchar_t*>(GlobalLock(memory));
    if (!target) {
        GlobalFree(memory);
        throw std::runtime_error("Pano belleği kilidi açılamadı.");
    }
    std::memcpy(target, wide.data(), bytes);
    GlobalUnlock(memory);
    if (!open_clipboard()) {
        GlobalFree(memory);
        throw std::runtime_error("Pano açılamadı.");
    }
    EmptyClipboard();
    if (!SetClipboardData(CF_UNICODETEXT, memory)) {
        GlobalFree(memory);
        CloseClipboard();
        throw std::runtime_error("Panoya yazılamadı.");
    }
    CloseClipboard();
}

void clipboard_clear() {
    if (!open_clipboard()) throw std::runtime_error("Pano açılamadı.");
    EmptyClipboard();
    CloseClipboard();
}

// Öğeyi Geri Dönüşüm Kutusuna taşır (Python send2trash karşılığı).
void send_to_trash(const fs::path& path) {
    std::wstring target = path.wstring();
    std::wstring buffer = target;
    buffer.push_back(L'\0');
    buffer.push_back(L'\0');

    SHFILEOPSTRUCTW operation{};
    operation.wFunc = FO_DELETE;
    operation.pFrom = buffer.c_str();
    operation.pTo = nullptr;
    operation.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT;
    const int result = SHFileOperationW(&operation);
    if (result != 0 || operation.fAnyOperationsAborted) {
        throw std::runtime_error("Öğe Geri Dönüşüm Kutusuna taşınamadı (kod " +
                                 std::to_string(result) + ").");
    }
}

bool shell_open(const std::string& target, const std::string& parameters = {}) {
    const std::wstring wide = jarvis::util::utf8_to_wide(target);
    const std::wstring params = jarvis::util::utf8_to_wide(parameters);
    HINSTANCE instance = ShellExecuteW(nullptr, L"open", wide.c_str(),
                                      params.empty() ? nullptr : params.c_str(), nullptr,
                                      SW_SHOWNORMAL);
    return reinterpret_cast<INT_PTR>(instance) > 32;
}
#endif  // _WIN32

bool path_exists(const fs::path& path) {
    std::error_code ec;
    return fs::exists(path, ec);
}

bool path_is_dir(const fs::path& path) {
    std::error_code ec;
    return fs::is_directory(path, ec);
}

long long round_half_up(double value) { return static_cast<long long>(std::llround(value)); }

}  // namespace

void make_parents(const fs::path& directory, ChangeSet& changes) {
    std::vector<fs::path> missing;
    fs::path current = directory;
    std::error_code ec;
    while (!fs::exists(current, ec) && current.has_parent_path() && current.parent_path() != current) {
        missing.push_back(current);
        current = current.parent_path();
        ec = std::error_code();
    }
    for (auto it = missing.rbegin(); it != missing.rend(); ++it) {
        std::error_code create_ec;
        fs::create_directory(*it, create_ec);
        if (create_ec) throw std::runtime_error("Klasör oluşturulamadı: " + u8s(*it));
        changes.created(*it);
    }
}

// --------------------------------------------------------------------- kurulum

DesktopExecutor::DesktopExecutor(SafetyServices& services, storage::Storage& storage,
                                 core::SkillLibrary& skills, home::SmartHome& home)
    : services_(services), storage_(storage), skills_(skills), home_(home) {}

// ------------------------------------------------------------- plan değerlendirmesi

PlanAssessment DesktopExecutor::assess(const ActionPlan& plan, bool tainted) {
    Risk highest = Risk::Read;
    std::vector<std::string> reasons;
    std::string decision = "allow";
    int index = 0;
    for (const PlannedAction& action : plan.actions) {
        ++index;
        Risk risk = Risk::Read;
        try {
            risk = classify_action(action.action_type, action.arguments, services_.policy);
        } catch (const SafetyError& error) {
            PlanAssessment assessment;
            assessment.decision = "deny";
            assessment.risk = Risk::Forbidden;
            assessment.reasons = {std::to_string(index) + ". adım: " + error.what()};
            return assessment;
        }
        if (static_cast<int>(risk) > static_cast<int>(highest)) highest = risk;
        const Decision verdict = services_.gate.decide(risk, tainted);
        if (verdict.action == "deny") {
            PlanAssessment assessment;
            assessment.decision = "deny";
            assessment.risk = risk;
            assessment.reasons = {std::to_string(index) + ". adım: " + verdict.reason};
            return assessment;
        }
        if (verdict.action == "ask") {
            decision = "ask";
            if (std::find(reasons.begin(), reasons.end(), verdict.reason) == reasons.end()) {
                reasons.push_back(verdict.reason);
            }
        }
    }
    PlanAssessment assessment;
    assessment.decision = decision;
    assessment.risk = highest;
    assessment.reasons = reasons;
    return assessment;
}

std::vector<std::string> DesktopExecutor::execute_plan(
    const ActionPlan& plan, const std::function<void(const std::string&)>& on_progress) {
    std::vector<std::string> results;
    const std::size_t total = plan.actions.size();
    for (std::size_t i = 0; i < total; ++i) {
        if (services_.kill->is_set()) {
            results.push_back("⛔ Durduruldu: kalan adımlar yapılmadı.");
            break;
        }
        const PlannedAction& action = plan.actions[i];
        if (on_progress) {
            on_progress(std::to_string(i + 1) + "/" + std::to_string(total) + ": " +
                        action.description);
        }
        const std::string result = execute(action);
        results.push_back(result);
        if (result.rfind("❌", 0) == 0) break;
    }
    return results;
}

std::string DesktopExecutor::execute(const PlannedAction& action) {
    std::optional<Risk> risk;
    std::string result;
    try {
        risk = classify_action(action.action_type, action.arguments, services_.policy);
        result = run(action);
    } catch (const SafetyError& error) {
        result = "❌ ";
        result += error.what();
    } catch (const std::exception& error) {
        result = "❌ İşlem başarısız: ";
        result += error.what();
    }
    json extra{{"result", utf8_take(result, 400)}};
    if (risk) extra["risk"] = risk_label(*risk);
    services_.audit.record(action.action_type, action.arguments, extra);
    return result;
}

// ----------------------------------------------------------------------- dispatch

std::string DesktopExecutor::run(const PlannedAction& action) {
    const std::string& kind = action.action_type;
    const json& args = action.arguments;

    if (kind == "undo_last") return undo_last();
    if (kind == "create_folder") return create_folder(args);
    if (kind == "write_text_file" || kind == "append_text_file") return write_or_append(kind, args);
    if (kind == "copy_path" || kind == "move_path") return copy_or_move(kind, args);
    if (kind == "delete_path") return delete_path(args);
    if (kind == "open_path") return open_path(args);

    if (kind == "run_command") return run_command(args);
    if (kind == "open_system_uri") return open_system_uri(args);
    if (kind == "browser_open" || kind == "search_web") return open_link(kind, args);
    if (kind == "open_web_page") return open_web_page(args);
    if (kind == "open_application") return open_application(args);
    if (kind == "window_control") return window_control(args);
    if (kind == "inspect_ui") return inspect_ui(args);
    if (kind == "ui_control") return ui_control(args);
    if (kind == "send_keys") return send_keys(args);

    if (kind == "capture_screen") return capture_screen(args);
    if (kind == "find_text_on_screen") return find_text_on_screen(args);
    if (kind == "screen_wait") return screen_wait(args);
    if (kind == "inspect_image") return inspect_image(args);

    if (kind == "clipboard") return clipboard(args);
    if (kind == "system_control") return system_control(args);
    if (kind == "memory") return memory(args);
    if (kind == "skill") return skill(args);
    if (kind == "home") return home_tool(args);

    return not_wired(kind);
}

// ------------------------------------------------------------------ dosya araçları

std::string DesktopExecutor::create_folder(const json& args) {
    const std::string raw = arg_text(args, "path");
    const fs::path path = services_.policy.resolve(raw);
    if (path_exists(path)) return "❌ Hedef zaten var: " + raw;
    if (!services_.undo) throw ActionForbidden("Geri alma deposu kapalı; klasör oluşturulamıyor.");
    ChangeSet changes = services_.undo->begin("create_folder");
    try {
        make_parents(path, changes);
        changes.commit();
    } catch (...) {
        changes.discard();
        throw;
    }
    return "✅ Klasör oluşturuldu: " + u8s(path);
}

std::string DesktopExecutor::write_or_append(const std::string& kind, const json& args) {
    const fs::path path = services_.policy.resolve(arg_text(args, "path"));
    const std::string content = arg_text(args, "content");
    if (utf8_length(content) > kMaxWriteChars) {
        return "❌ İçerik çok büyük (en fazla 5 milyon karakter).";
    }
    const bool overwrite = arg_flag(args, "overwrite", false);
    if (path_is_dir(path)) return "❌ Bu bir klasör, dosya yazılamaz: " + u8s(path);
    const bool existed = path_exists(path);
    if (existed && !overwrite && kind == "write_text_file") {
        return "❌ Dosya zaten var, üzerine yazılmadı: " + u8s(path);
    }
    if (!services_.undo) throw ActionForbidden("Geri alma deposu kapalı; dosya yazılamıyor.");

    ChangeSet changes = services_.undo->begin(kind);
    try {
        if (existed) {
            changes.backup(path);
        } else {
            make_parents(path.parent_path(), changes);
        }
        std::FILE* stream = nullptr;
#ifdef _WIN32
        const int opened = _wfopen_s(&stream, path.c_str(),
                                     kind == "append_text_file" ? L"ab" : L"wb");
#else
        stream = std::fopen(u8s(path).c_str(), kind == "append_text_file" ? "ab" : "wb");
        const int opened = stream ? 0 : errno;
#endif
        if (opened != 0 || stream == nullptr) {
            throw std::runtime_error("Dosya açılırken hata: " + u8s(path));
        }
        std::fwrite(content.data(), 1, content.size(), stream);
        std::fclose(stream);
        if (!path_exists(path)) {
            changes.discard();
            return "❌ Dosya oluşturulamadı: " + u8s(path);
        }
        if (!existed) changes.created(path);
        changes.commit();
    } catch (...) {
        changes.discard();
        throw;
    }
    return "✅ Dosya yazıldı: " + u8s(path);
}

std::string DesktopExecutor::copy_or_move(const std::string& kind, const json& args) {
    const fs::path source = services_.policy.resolve(arg_text(args, "source"));
    const fs::path destination = services_.policy.resolve(arg_text(args, "destination"));
    const bool copying = kind == "copy_path";
    const std::string verb = copying ? "Kopyalanacak" : "Taşınacak";
    if (!path_exists(source)) return "❌ " + verb + " öğe bulunamadı: " + u8s(source);
    if (path_exists(destination)) {
        return "❌ Hedef zaten var, üzerine yazılmadı: " + u8s(destination);
    }
    if (!services_.undo) throw ActionForbidden("Geri alma deposu kapalı; işlem yapılamıyor.");

    ChangeSet changes = services_.undo->begin(kind);
    try {
        make_parents(destination.parent_path(), changes);
        std::error_code ec;
        if (copying) {
            fs::copy(source, destination,
                     fs::copy_options::recursive | fs::copy_options::copy_symlinks, ec);
            if (ec) throw std::runtime_error("Kopyalama başarısız: " + ec.message());
            changes.created(destination);
        } else {
            fs::rename(source, destination, ec);
            if (ec) {
                // Farklı disk/izin durumu: Python shutil.move gibi kopyala + sil.
                ec = std::error_code();
                if (path_is_dir(source)) {
                    fs::copy(source, destination, fs::copy_options::recursive, ec);
                    if (!ec) fs::remove_all(source, ec);
                } else {
                    fs::copy_file(source, destination, fs::copy_options::skip_symlinks, ec);
                    if (!ec) fs::remove(source, ec);
                }
                if (ec) throw std::runtime_error("Taşıma başarısız: " + ec.message());
            }
            changes.moved(source, destination);
        }
        changes.commit();
    } catch (...) {
        changes.discard();
        throw;
    }
    return std::string(copying ? "✅ Kopyalandı: " : "✅ Taşındı: ") + u8s(source) + " → " +
           u8s(destination);
}

std::string DesktopExecutor::delete_path(const json& args) {
    const fs::path path = services_.policy.resolve(arg_text(args, "path"));
    if (!path_exists(path)) return "❌ Silinecek öğe bulunamadı: " + u8s(path);
#ifdef _WIN32
    send_to_trash(path);
#else
    throw ActionForbidden("Güvenli silme yalnızca Windows'ta kullanılabilir.");
#endif
    if (services_.undo) {
        ChangeSet changes = services_.undo->begin("delete_path");
        changes.trashed(path);
        changes.commit();
    }
    return "✅ Geri Dönüşüm Kutusuna taşındı: " + u8s(path);
}

std::string DesktopExecutor::open_path(const json& args) {
    const fs::path path = services_.policy.resolve(arg_text(args, "path"));
    if (!path_exists(path)) return "❌ Açılacak dosya bulunamadı: " + u8s(path);
#ifdef _WIN32
    if (!shell_open(u8s(path))) throw std::runtime_error("Dosya açıldı sayılamadı: " + u8s(path));
#else
    throw ActionForbidden("Dosya açma yalnızca Windows'ta desteklenir.");
#endif
    return "✅ Dosya açıldı: " + u8s(path);
}

std::string DesktopExecutor::undo_last() {
    if (!services_.undo) return "❌ Geri alma deposu bu sürümde kapalı.";
    const UndoOutcome outcome = services_.undo->undo_last();
    const std::string header =
        outcome.ok ? "✅ Son değişiklik geri alındı" : "❌ Geri alma tamamlanamadı";
    if (!outcome.has_label) {
        return "❌ " + (outcome.messages.empty() ? std::string("Geri alınacak bir işlem yok.")
                                                 : outcome.messages.front());
    }
    std::string text = header + " (" + outcome.label + "):\n";
    for (std::size_t i = 0; i < outcome.messages.size(); ++i) {
        if (i) text += "\n";
        text += outcome.messages[i];
    }
    return text;
}

// ------------------------------------------------------------- açma / çalıştırma

std::string DesktopExecutor::run_command(const json& args) {
    const std::string language = ascii_lower(arg_text(args, "language"));
    const std::string code = arg_text(args, "code");
    if ((language != "powershell" && language != "python") || util::trim(code).empty()) {
        throw ActionForbidden("run_command için powershell/python ve boş olmayan kod gerekli.");
    }
    static const std::regex forbidden(
        "\\b(reg(\\.exe)?\\s+(add|delete)|regedit|net\\s+(user|localgroup)|takeown|icacls|"
        "bcdedit|diskpart|runas|remove-itemproperty|set-itemproperty|new-itemproperty)\\b|"
        "-verb\\s+runas",
        std::regex_constants::icase);
    static const std::regex automation(
        "\\b(import|from)\\s+(pyautogui|pywinauto|pynput|autopy|keyboard|mouse|win32gui|win32api|"
        "win32con|win32com)\\b|\\b(pyautogui|pywinauto|pynput|autopy|win32gui|win32api|win32con|"
        "win32com)\\s*\\.\\b|\\b(SendKeys|SendInput|SetForegroundWindow|SetCursorPos|mouse_event|"
        "keybd_event)\\b",
        std::regex_constants::icase);

    std::string lowered = ascii_lower(code);
    std::replace(lowered.begin(), lowered.end(), '/', '\\');
    bool touches_forbidden = std::regex_search(code, forbidden);
    if (!touches_forbidden) {
        for (const auto& [zone, label] : services_.policy.forbidden()) {
            std::string needle = ascii_lower(u8s(zone));
            if (needle.empty()) continue;
            if (lowered.find(needle) != std::string::npos) {
                touches_forbidden = true;
                break;
            }
        }
    }
    if (touches_forbidden) {
        throw ActionForbidden(
            "Komut sistem, kayıt defteri, hesap veya Jarvis korumalı alanına dokunuyor.");
    }
    if (std::regex_search(code, automation)) {
        throw ActionForbidden(
            "Masaüstü otomasyonu run_command içinde kullanılamaz; mouse_control, ui_control veya "
            "send_keys araçlarını kullan.");
    }

    const fs::path working =
        services_.policy.resolve(arg_missing(args, "working_directory") ? "."
                                                                       : arg_text(args, "working_directory"));
    if (!path_is_dir(working)) {
        throw ActionForbidden("Çalışma klasörü bulunamadı: " + u8s(working));
    }

    std::vector<std::string> argv;
    std::string script = code;
    if (language == "powershell") {
        script = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + code;
        argv = {"powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script};
    } else {
        const char* from_env = std::getenv("JARVIS_PYTHON");
        argv = {from_env && *from_env ? from_env : "python", "-c", code};
    }

    long long timeout_seconds = 30;
    if (const auto parsed = arg_int(args, "timeout_seconds")) timeout_seconds = *parsed;
    timeout_seconds = std::clamp<long long>(timeout_seconds, 1, 120);

#ifdef _WIN32
    const ProcessResult process =
        run_process(argv, working, timeout_seconds * 1000, *services_.kill);
    if (process.killed) return "❌ Komut Durdur düğmesiyle iptal edildi.";
    if (process.timed_out) {
        return "❌ Komut " + std::to_string(timeout_seconds) + " saniyede zaman aşımına uğradı.";
    }
    if (process.failed) {
        return "❌ İşlem başarısız: Komut başlatılamadı (" + argv.front() + ").";
    }
    std::string combined = process.output;
    if (!process.errors.empty()) combined += "\nHata:\n" + process.errors;
    combined = util::trim(combined);
    if (utf8_length(combined) > 12000) {
        combined = utf8_take(combined, 12000) + "\n… Çıktı kısaltıldı.";
    }
    return std::string(process.code == 0 ? "✅" : "❌") + " Komut çıkış kodu: " +
           std::to_string(process.code) + "\n" + (combined.empty() ? "Çıktı yok." : combined);
#else
    throw ActionForbidden("run_command yalnızca Windows sürümünde bağlıdır.");
#endif
}

std::string DesktopExecutor::open_system_uri(const json& args) {
    const std::string uri = arg_text(args, "uri");
    if (uri.empty() || utf8_length(uri) > 2000 || uri.find_first_of('\0') != std::string::npos ||
        uri.find('\r') != std::string::npos || uri.find('\n') != std::string::npos ||
        uri.find('\t') != std::string::npos) {
        throw ActionForbidden("Geçerli ve tek satırlık bir uygulama bağlantısı gerekli.");
    }
    if (url_scheme(uri) != "steam") {
        throw ActionForbidden("Yalnızca steam:// bağlantıları açılabilir.");
    }
    const std::string lower = ascii_lower(uri);
    if (lower.rfind("steam://openurl/", 0) == 0) {
        const std::string target = uri.substr(std::strlen("steam://openurl/"));
        const std::string scheme = url_scheme(target);
        if (scheme != "http" && scheme != "https") {
            throw ActionForbidden("steam://openurl yalnızca http/https adresi açabilir.");
        }
    }
#ifdef _WIN32
    if (!shell_open(uri)) throw std::runtime_error("Bağlantı açılamadı: " + uri);
#else
    throw ActionForbidden("Uygulama bağlantısı yalnızca Windows'ta açılabilir.");
#endif
    return "✅ Uygulama bağlantısı açıldı: " + uri;
}

bool DesktopExecutor::open_in_browser(const std::string& url, std::string& message,
                                      const std::string& label) {
    if (browser_listener_) {
        browser_listener_(url);
        message = "✅ " + label + " Jarvis tarayıcısında açıldı: " + url;
        return true;
    }
#ifdef _WIN32
    if (!shell_open(url)) return false;
    message = "✅ " + label + " varsayılan tarayıcıda açıldı: " + url;
    return true;
#else
    (void)label;
    return false;
#endif
}

std::string DesktopExecutor::open_link(const std::string& tool, const json& args) {
    std::string url;
    std::string label = "Sayfa";
    if (tool == "search_web") {
        const std::string query = util::trim(arg_text(args, "query"));
        if (query.empty()) throw ActionForbidden("search_web için query gerekli.");
        url = "https://www.google.com/search?q=" + quote_plus(query);
        label = "Arama";
    } else {
        url = util::trim(arg_text(args, "url"));
        if (url.empty()) {
            url = util::trim(arg_text(args, "query"));
            if (!url.empty()) url = "https://" + url;
        }
        if (url.empty()) throw ActionForbidden(tool + " için url gerekli.");
    }
    const std::string scheme = url_scheme(url);
    if (scheme != "http" && scheme != "https") {
        throw ActionForbidden(tool == "search_web"
                                  ? "Arama yalnızca http/https bağlantısı açabilir."
                                  : "Tarayıcı yalnızca http/https adresleri açabilir.");
    }
    std::string message;
    if (open_in_browser(url, message, label)) return message;
    throw std::runtime_error("Bağlantı açılamadı: " + url);
}

// ------------------------------------------------------------------ uygulama araçları

std::string DesktopExecutor::open_application(const json& args) {
    const std::string application = util::trim(arg_text(args, "application"));
    if (application.empty()) throw ActionForbidden("open_application için application gerekli.");
    const std::string launched = applications_.launch(application, [this] {
        return services_.kill->is_set();
    });
    return "✅ Uygulama hazır: " + launched +
           ". Başlatma gerçek pencere/süreç ile doğrulandı. Uygulama içinde ek bir işlem gerekiyorsa "
           "inspect_ui ile arayüzü incele.";
}

std::string DesktopExecutor::open_web_page(const json& args) {
    const std::string application = util::trim(arg_text(args, "application"));
    const std::string url = util::trim(arg_text(args, "url"));
    if (application.empty() || url.empty())
        throw ActionForbidden("open_web_page için application ve url gerekli.");
    return "✅ Sayfa belirtilen uygulamada açıldı: " + applications_.open_url(application, url);
}

std::string DesktopExecutor::window_control(const json& args) {
    tools::WindowTools window_tools;
    return window_tools.run(args);
}

std::string DesktopExecutor::inspect_ui(const json& args) {
    const std::string title = util::trim(arg_text(args, "window_title"));
    if (title.empty()) throw ActionForbidden("inspect_ui için window_title gerekli.");
    long long limit = 60;
    if (const auto parsed = arg_int(args, "limit")) limit = *parsed;
    return ui_automation_.inspect_ui(title, limit, util::trim(arg_text(args, "name_filter")));
}

std::string DesktopExecutor::ui_control(const json& args) {
    return ui_automation_.control(args);
}

std::string DesktopExecutor::send_keys(const json& args) {
    return ui_automation_.keys(args);
}

// ---------------------------------------------------------------- ekran araçları

std::string DesktopExecutor::capture_screen(const json& args) {
    return tools::capture_screen(args, screenshot_sink_);
}

std::string DesktopExecutor::find_text_on_screen(const json& args) {
    const std::string result = ui_automation_.find_text(args);
    // Python _find_text_and_verify: tıklamadan sonra ekran yakalanıp modelin
    // sonraki adımına eklenir; model sayfanın gerçekten değiştiğini kendi
    // gözünden doğrular. Yakalama başarısız olursa tıklama yine de geçerlidir.
    if (!arg_flag(args, "click", false) || !screenshot_sink_) return result;
    try {
        std::this_thread::sleep_for(std::chrono::milliseconds(350));
        std::string captured;
        tools::capture_screen(json::object(), [this, &captured](const std::string& path) {
            captured = path;
            screenshot_sink_(path);
        });
        if (captured.empty()) return result;
        json data = json::parse(result);
        data["screenshot"] = captured;
        data["note"] =
            data.value("note", std::string()) +
            " Tıklama sonrası doğrulama ekran görüntüsü modelin sonraki adımına eklendi; "
            "sayfanın gerçekten değiştiğini bu görüntüden kontrol et.";
        return data.dump();
    } catch (const std::exception&) {
        return result;
    }
}

std::string DesktopExecutor::screen_wait(const json& args) {
    return ui_automation_.wait_for(args);
}

std::string DesktopExecutor::inspect_image(const json& args) {
    const std::string raw = util::trim(arg_text(args, "path"));
    if (raw.empty()) throw ActionForbidden("inspect_image için path gerekli.");
    return tools::inspect_image(services_.policy.resolve(raw), screenshot_sink_);
}

// ------------------------------------------------------------------ masaüstü servisleri

std::string DesktopExecutor::clipboard(const json& args) {
    const std::string action = ascii_lower(util::trim(arg_text_or(args, "action", "read")));
#ifdef _WIN32
    if (action == "read") {
        const std::string text = clipboard_read_text();
        if (text.empty()) return "ℹ️ Pano boş.";
        const std::string preview = utf8_length(text) <= 4000
                                        ? text
                                        : utf8_take(text, 4000) + "… (kısaltıldı)";
        return "✅ Pano içeriği (" + std::to_string(utf8_length(text)) + " karakter):\n" + preview;
    }
    if (action == "write") {
        const std::string text = arg_text(args, "text");
        if (text.empty()) throw std::runtime_error("Panoya yazılacak metin boş olamaz.");
        clipboard_write_text(text);
        return "✅ Panoya yazıldı (" + std::to_string(utf8_length(text)) + " karakter).";
    }
    if (action == "clear") {
        clipboard_clear();
        return "✅ Pano temizlendi.";
    }
#else
    throw ActionForbidden("Pano araçları yalnızca Windows'ta bağlı.");
#endif
    throw ActionForbidden("clipboard için action: read, write veya clear olmalı.");
}

std::string DesktopExecutor::system_control(const json& args) {
    const std::string action = ascii_lower(util::trim(arg_text(args, "action")));
    long long seconds = 60;
    if (const auto parsed = arg_int(args, "seconds")) seconds = *parsed;
    if (seconds == 0) seconds = 60;
    const std::string reason =
        util::collapse_whitespace(arg_text_or(args, "reason", "Jarvis"));

#ifdef _WIN32
    static const std::unordered_map<std::string, std::pair<UINT, std::string>> kKeys = {
        {"volume_up", {0xAF, "Ses seviyesi artırıldı."}},
        {"volume_down", {0xAE, "Ses seviyesi azaltıldı."}},
        {"mute", {0xAD, "Ses sessize alındı / geri açıldı."}},
        {"media_play_pause", {0xB3, "Medya oynat/duraklat gönderildi."}},
        {"media_next", {0xB0, "Sonraki parçaya geçildi."}},
        {"media_previous", {0xB1, "Önceki parçaya geçildi."}},
        {"media_stop", {0xB2, "Medya durduruldu."}},
    };
    if (const auto found = kKeys.find(action); found != kKeys.end()) {
        send_media_key(found->second.first);
        return "✅ " + found->second.second;
    }
    if (action == "lock") {
        if (!LockWorkStation()) throw std::runtime_error("Ekran kilidi başlatılamadı.");
        return "✅ Ekran kilitlendi.";
    }
    if (action == "sleep") {
        const ProcessResult result =
            run_process({"rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"}, {}, 20000,
                        *services_.kill);
        if (result.failed || result.code != 0) {
            throw std::runtime_error(result.errors.empty() ? "Uyku modu başlatılamadı."
                                                            : util::trim(result.errors));
        }
        return "✅ Bilgisayar uyku moduna alınıyor.";
    }
    if (action == "cancel_shutdown") {
        const ProcessResult result =
            run_process({"shutdown", "/a"}, {}, 20000, *services_.kill);
        if (result.failed) throw std::runtime_error("Zamanlanmış kapatma iptal edilemedi.");
        if (result.code == 1116) return "ℹ️ İptal edilecek zamanlanmış kapatma yok.";
        if (result.code != 0) {
            throw std::runtime_error(result.errors.empty() ? "Zamanlanmış kapatma iptal edilemedi."
                                                            : util::trim(result.errors));
        }
        return "✅ Zamanlanmış kapatma/yeniden başlatma iptal edildi.";
    }
    if (action == "shutdown" || action == "restart") {
        const long long delay = std::max<long long>(15, std::min<long long>(seconds, 3600));
        std::string note = utf8_take(reason.empty() ? "Jarvis" : reason, 180);
        if (note.empty()) note = "Jarvis";
        const ProcessResult result =
            run_process({"shutdown", action == "shutdown" ? "/s" : "/r", "/t",
                         std::to_string(delay), "/c", note},
                        {}, 20000, *services_.kill);
        if (result.failed || result.code != 0) {
            throw std::runtime_error(result.errors.empty() ? "Güç işlemi başlatılamadı."
                                                            : util::trim(result.errors));
        }
        return "✅ Bilgisayar " + std::to_string(delay) + " saniye içinde " +
               (action == "shutdown" ? "kapanacak" : "yeniden başlatılacak") +
               ". İptal için system_control action=cancel_shutdown kullan.";
    }
#else
    (void)seconds;
    (void)reason;
#endif
    throw std::runtime_error("Desteklenmeyen sistem işlemi: " + action);
}

std::string DesktopExecutor::memory(const json& args) {
    const std::string action = ascii_lower(util::trim(arg_text_or(args, "action", "list")));
    static const std::size_t kMaxKey = 80;
    static const std::size_t kMaxValue = 2000;
    static const std::size_t kMaxNotes = 200;

    if (action == "list") {
        long long limit = 50;
        if (const auto parsed = arg_int(args, "limit")) limit = *parsed;
        limit = std::clamp<long long>(limit, 1, static_cast<long long>(kMaxNotes));
        std::vector<json> rows = storage_.get_all_memory();
        if (rows.size() > static_cast<std::size_t>(limit)) rows.resize(static_cast<std::size_t>(limit));
        if (rows.empty()) return "ℹ️ Kalıcı hafızada kayıtlı not yok.";
        std::string text = "✅ " + std::to_string(rows.size()) + " kalıcı not:";
        for (const json& row : rows) {
            std::string value = util::collapse_whitespace(
                row.contains("value") && row["value"].is_string() ? row["value"].get<std::string>()
                                                                  : std::string());
            const std::string key = row.contains("key") && row["key"].is_string()
                                        ? row["key"].get<std::string>()
                                        : std::string();
            text += "\n• " + key + ": " + utf8_take(value, 300);
        }
        return text;
    }
    if (action == "add") {
        const std::string value = util::trim(arg_text(args, "value"));
        if (value.empty()) throw std::runtime_error("Kaydedilecek not metni boş olamaz.");
        if (utf8_length(value) > kMaxValue) {
            throw std::runtime_error("Not en fazla 2000 karakter olabilir.");
        }
        std::string key = util::collapse_whitespace(arg_text(args, "key"));
        if (key.empty()) {
            key = utf8_take(util::collapse_whitespace(value), 50);
            if (key.empty()) key = "not";
        }
        key = utf8_take(key, kMaxKey);
        const std::vector<json> existing = storage_.get_all_memory();
        const bool already = storage_.get_memory(key).has_value();
        if (existing.size() >= kMaxNotes && !already) {
            throw std::runtime_error("Hafıza sınırı dolu (200 not); önce eski bir notu sil.");
        }
        storage_.save_memory(key, value);
        return "✅ Kalıcı hafızaya kaydedildi: " + key;
    }
    if (action == "delete") {
        const std::string key = utf8_take(util::trim(arg_text(args, "key")), kMaxKey);
        if (key.empty()) throw std::runtime_error("Silinecek not için key gerekli.");
        if (!storage_.get_memory(key)) {
            return "ℹ️ '" + key + "' anahtarında kayıtlı not bulunamadı.";
        }
        storage_.delete_memory(key);
        return "✅ Kalıcı not silindi: " + key;
    }
    throw std::runtime_error("Hafıza için action: list/add/delete olabilir.");
}

std::string DesktopExecutor::skill(const json& args) {
    try {
        return skills_.run(args);
    } catch (const core::SkillError& error) {
        return "❌ İşlem başarısız: " + std::string(error.what());
    }
}

// --------------------------------------------------------------- akıllı ev aracı

std::string DesktopExecutor::home_tool(const json& args) {
    const std::string action = ascii_lower(util::trim(arg_text_or(args, "action", "status")));
    std::string room = ascii_lower(util::trim(arg_text_or(args, "room", "salon")));
    if (room != "salon" && room != "calisma") room = "salon";
    std::string kind = ascii_lower(util::trim(arg_text_or(args, "kind", "turk")));
    if (kind != "turk" && kind != "filtre") kind = "turk";

    if (action == "status") {
        const json data = home_.snapshot();
        std::vector<std::string> lines;
        if (data.contains("lights")) {
            for (const auto& [key, light] : data["lights"].items()) {
                (void)key;
                if (light.value("on", false)) {
                    lines.push_back("• " + light.value("name", std::string()) + ": açık, %" +
                                    std::to_string(light.value("brightness", 0)) + ", " +
                                    light.value("tone_label", std::string()));
                } else {
                    lines.push_back("• " + light.value("name", std::string()) + ": kapalı");
                }
            }
        }
        if (data.contains("coffees")) {
            for (const auto& [key, coffee] : data["coffees"].items()) {
                (void)key;
                const std::string status = coffee.value("status", std::string());
                std::string detail;
                if (status == "demleniyor") {
                    detail = "demleniyor, " + std::to_string(coffee.value("remaining", 0)) +
                             " sn kaldı";
                } else if (status == "hazir") {
                    if (coffee.value("kind", std::string()) == "turk") {
                        detail = "hazır, " + coffee.value("foam_label", std::string()) + ", " +
                                 coffee.value("sugar_label", std::string());
                    } else {
                        detail = "hazır, " + std::to_string(coffee.value("cups", 0)) + " fincan";
                    }
                } else {
                    detail = "makine boş";
                }
                lines.push_back("• " + coffee.value("name", std::string()) + ": " + detail);
            }
        }
        std::string text = "✅ Akıllı ev durumu (simülasyon):\n";
        for (std::size_t i = 0; i < lines.size(); ++i) {
            if (i) text += "\n";
            text += lines[i];
        }
        return text;
    }

    if (action == "light" || action == "all_lights") {
        std::optional<bool> on;
        if (!arg_missing(args, "on")) {
            if (args.at("on").is_string()) {
                on = arg_yes(ascii_lower(util::trim(args.at("on").get<std::string>())));
            } else {
                on = arg_flag(args, "on", false);
            }
        }
        std::optional<int> brightness;
        if (const auto parsed = arg_int(args, "brightness")) brightness = static_cast<int>(*parsed);
        std::optional<std::string> tone;
        const std::string tone_key = ascii_lower(util::trim(arg_text(args, "tone")));
        if (!tone_key.empty() && home::is_tone_key(tone_key)) tone = tone_key;
        if (!on && !brightness && !tone) {
            return "❌ Işık için on, brightness veya tone bilgisi gerekli.";
        }
        if (action == "all_lights") {
            const std::vector<home::Light> lights =
                home_.set_all_lights(on, brightness, tone);
            std::string text = "✅ Tüm ışıklar güncellendi (simülasyon): ";
            for (std::size_t i = 0; i < lights.size(); ++i) {
                if (i) text += " · ";
                text += lights[i].describe();
            }
            return text;
        }
        const home::Light& light = home_.set_light(room, on, brightness, tone);
        return "✅ " + light.describe() + " (simülasyon)";
    }

    if (action == "coffee_brew") {
        std::optional<int> cups;
        if (const auto parsed = arg_int(args, "cups")) cups = static_cast<int>(*parsed);
        std::optional<std::string> foam;
        const std::string foam_key = ascii_lower(util::trim(arg_text(args, "foam")));
        if (!foam_key.empty() && home::is_foam_key(foam_key)) foam = foam_key;
        std::optional<std::string> sugar;
        const std::string sugar_key = ascii_lower(util::trim(arg_text(args, "sugar")));
        if (!sugar_key.empty() && home::is_sugar_key(sugar_key)) sugar = sugar_key;

        const auto [started, coffee, seconds] = home_.brew(kind, cups, foam, sugar);
        if (!started) {
            const double now = home_.now();
            const long long remaining =
                std::max<long long>(0, round_half_up(coffee.ready_at.value_or(now) - now));
            return "❌ " + coffee.name() + " zaten demleniyor · " + std::to_string(remaining) +
                   " sn kaldı.";
        }
        const std::string extra = kind == "filtre"
                                      ? " · " + std::to_string(coffee.cups) + " fincan"
                                      : std::string();
        return "✅ " + coffee.name() + " başladı · " + std::to_string(static_cast<long long>(seconds)) +
               " sn" + extra + ". Süre dolunca hazır olacak. (simülasyon)";
    }

    if (action == "coffee_cancel") {
        const home::Coffee coffee = home_.cancel_coffee(kind);
        return "✅ " + coffee.name() + " iptal edildi. (simülasyon)";
    }

    const home::Coffee coffee = home_.coffee(kind);
    return "✅ " + coffee.describe(home_.now()) + " (simülasyon)";
}

}  // namespace jarvis::ai
