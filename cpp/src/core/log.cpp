#include <jarvis/core/log.hpp>

#include <fstream>
#include <string>

#include <jarvis/core/util.hpp>

#ifdef _WIN32
#include <windows.h>
#endif

namespace jarvis::log {
namespace {

std::filesystem::path project_root() {
    return std::filesystem::current_path();
}

}  // namespace

std::filesystem::path logs_dir() {
    std::filesystem::path dir = project_root() / "logs";
    std::error_code ec;
    std::filesystem::create_directories(dir, ec);
    return dir;
}

void write_crash_header() {
    try {
        std::ofstream out(logs_dir() / "jarvis_crash.log", std::ios::app);
        if (!out) return;
        out << "\n=== " << util::now_utc_iso() << " ===\n";
    } catch (...) {
    }
}

void report_startup_error(const std::string& details) {
    try {
        std::ofstream out(logs_dir() / "baslatma_hatasi.log", std::ios::trunc);
        if (out) out << details;
    } catch (...) {
    }

#ifdef _WIN32
    const std::string message =
        "Jarvis baslatilamadi.\n\n" + details + "\n\nAyrinti: logs\\baslatma_hatasi.log";
    const int wide_len =
        ::MultiByteToWideChar(CP_UTF8, 0, message.c_str(), -1, nullptr, 0);
    if (wide_len > 0) {
        std::wstring wide(static_cast<std::size_t>(wide_len), L'\0');
        ::MultiByteToWideChar(CP_UTF8, 0, message.c_str(), -1, wide.data(), wide_len);
        ::MessageBoxW(nullptr, wide.c_str(), L"Jarvis", MB_ICONERROR);
    }
#endif
}
}  // namespace jarvis::log
