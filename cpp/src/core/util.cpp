#include <jarvis/core/util.hpp>

#ifdef _WIN32
#include <jarvis/core/win_path.hpp>
#include <shellapi.h>
#endif

#include <cctype>
#include <chrono>
#include <cstdio>
#include <ctime>
#include <set>
#include <unordered_map>
#include <vector>

namespace jarvis::util {

std::string trim(std::string_view s) {
    std::size_t b = 0;
    std::size_t e = s.size();
    while (b < e && std::isspace(static_cast<unsigned char>(s[b]))) ++b;
    while (e > b && std::isspace(static_cast<unsigned char>(s[e - 1]))) --e;
    return std::string(s.substr(b, e - b));
}

std::string collapse_whitespace(std::string_view s) {
    std::string out;
    out.reserve(s.size());
    bool pending_space = false;
    for (const char c : s) {
        if (c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f' || c == '\v') {
            pending_space = true;
            continue;
        }
        if (pending_space && !out.empty()) out.push_back(' ');
        pending_space = false;
        out.push_back(c);
    }
    return out;
}

std::string to_lower_ascii(std::string_view s) {
    std::string out(s);
    for (char& c : out) {
        if (c >= 'A' && c <= 'Z') c = static_cast<char>(c - 'A' + 'a');
    }
    return out;
}

std::string normalize_clock_text(std::string_view value, std::string_view fallback) {
    const std::string text = collapse_whitespace(value);
    const std::size_t pos = text.find(':');
    if (pos == std::string::npos) return std::string(fallback);
    const std::string hour_s = trim(std::string_view(text).substr(0, pos));
    const std::string min_s = trim(std::string_view(text).substr(pos + 1));
    try {
        const int hour = std::stoi(hour_s);
        const int minute = std::stoi(min_s);
        if (hour < 0 || hour > 23 || minute < 0 || minute > 59) {
            return std::string(fallback);
        }
        char buf[8];
        std::snprintf(buf, sizeof(buf), "%02d:%02d", hour, minute);
        return buf;
    } catch (...) {
        return std::string(fallback);
    }
}

int clamp_int(long long value, int lo, int hi) {
    if (value < lo) return lo;
    if (value > hi) return hi;
    return static_cast<int>(value);
}

std::string now_utc_iso() {
    using namespace std::chrono;
    const auto now = system_clock::now();
    const auto ms = duration_cast<milliseconds>(now.time_since_epoch()) % 1000;
    const std::time_t tt = system_clock::to_time_t(now);
    std::tm tm{};
#ifdef _WIN32
    gmtime_s(&tm, &tt);
#else
    gmtime_r(&tt, &tm);
#endif
    char buf[48];
    std::snprintf(buf, sizeof(buf), "%04d-%02d-%02dT%02d:%02d:%02d.%03d+00:00",
                  tm.tm_year + 1900, tm.tm_mon + 1, tm.tm_mday, tm.tm_hour, tm.tm_min,
                  tm.tm_sec, static_cast<int>(ms.count()));
    return buf;
}

std::string normalize_theme(std::string_view name) {
    const std::string v = to_lower_ascii(trim(name));
    static const std::unordered_map<std::string, std::string> aliases = {
        {"siyah", "black"},   {"kirmizi", "black"}, {"kırmızı", "black"},
        {"ultron", "black"},  {"grafit", "black"},  {"sade", "black"},
        {"mavi", "midnight"}, {"gece", "midnight"},
        {"kor", "ember"},     {"amber", "ember"},   {"turuncu", "ember"},
        {"kum", "ember"},     {"sicak", "ember"},   {"sıcak", "ember"},
        {"yesil", "forest"},  {"yeşil", "forest"},  {"zumrut", "forest"},
        {"zümrüt", "forest"}, {"orman", "forest"},  {"adacayi", "forest"},
        {"adaçayı", "forest"}, {"beyaz", "white"},  {"aydinlik", "white"},
        {"aydınlık", "white"}, {"light", "white"},
    };
    std::string canonical = v;
    const auto it = aliases.find(v);
    if (it != aliases.end()) canonical = it->second;

    static const std::set<std::string> themes = {"black", "midnight", "ember", "forest", "white"};
    return themes.count(canonical) ? canonical : "black";
}

std::size_t utf8_length(const std::string& value) {
    std::size_t count = 0;
    for (std::size_t i = 0; i < value.size();) {
        const unsigned char byte = static_cast<unsigned char>(value[i]);
        std::size_t width = 1;
        if ((byte & 0xE0) == 0xC0) width = 2;
        else if ((byte & 0xF0) == 0xE0) width = 3;
        else if ((byte & 0xF8) == 0xF0) width = 4;
        if (width > 1 && i + width > value.size()) width = 1;  // kopuk devam baytı
        i += width;
        ++count;
    }
    return count;
}

std::string utf8_take(const std::string& value, std::size_t limit) {
    if (limit >= value.size()) return value;  // bayt sayısı limiti geçmiyorsa kırpma yok
    std::size_t count = 0;
    std::size_t i = 0;
    while (i < value.size() && count < limit) {
        const unsigned char byte = static_cast<unsigned char>(value[i]);
        std::size_t width = 1;
        if ((byte & 0xE0) == 0xC0) width = 2;
        else if ((byte & 0xF0) == 0xE0) width = 3;
        else if ((byte & 0xF8) == 0xF0) width = 4;
        if (width > 1 && i + width > value.size()) width = 1;
        i += width;
        ++count;
    }
    return value.substr(0, i);
}

namespace {

// `i` konumundaki UTF-8 dizisinin bayt uzunluğunu döner; geçersizse 0.
std::size_t utf8_seq_length(const std::string& s, std::size_t i) {
    const unsigned char head = static_cast<unsigned char>(s[i]);
    const std::size_t left = s.size() - i;
    const auto cont = [&](std::size_t offset) {
        const unsigned char c = static_cast<unsigned char>(s[i + offset]);
        return (c & 0xC0) == 0x80;
    };
    if (head < 0x80) return 1;
    if (head >= 0xC2 && head <= 0xDF) {
        if (left < 2 || !cont(1)) return 0;
        return 2;
    }
    if (head >= 0xE0 && head <= 0xEF) {
        if (left < 3 || !cont(1) || !cont(2)) return 0;
        const unsigned char second = static_cast<unsigned char>(s[i + 1]);
        if (head == 0xE0 && second < 0xA0) return 0;  // aşırı kısa kodlama
        if (head == 0xED && second > 0x9F) return 0;   // UTF-16 vekil aralığı
        return 3;
    }
    if (head >= 0xF0 && head <= 0xF4) {
        if (left < 4 || !cont(1) || !cont(2) || !cont(3)) return 0;
        const unsigned char second = static_cast<unsigned char>(s[i + 1]);
        if (head == 0xF0 && second < 0x90) return 0;
        if (head == 0xF4 && second > 0x8F) return 0;   // U+10FFFF ötesi
        return 4;
    }
    return 0;  // 0x80-0xC1 ve 0xF5-0xFF bir başlangıç baytı olamaz
}

}  // namespace

std::string utf8_sanitize(const std::string& value) {
    std::string out;
    out.reserve(value.size());
    for (std::size_t i = 0; i < value.size();) {
        const std::size_t width = utf8_seq_length(value, i);
        if (width == 0) {
            out += "\xEF\xBF\xBD";  // U+FFFD
            ++i;
            continue;
        }
        out.append(value, i, width);
        i += width;
    }
    return out;
}

std::vector<std::string> utf8_arguments(int argc, char* argv[]) {
#ifdef _WIN32
    int count = 0;
    LPWSTR* wide = CommandLineToArgvW(GetCommandLineW(), &count);
    if (wide) {
        std::vector<std::string> out;
        out.reserve(static_cast<std::size_t>(count));
        for (int i = 0; i < count; ++i) out.push_back(wide_to_utf8(wide[i]));
        LocalFree(wide);
        return out;
    }
#endif
    if (argc <= 0) return {};
    return std::vector<std::string>(argv, argv + argc);
}

#ifdef _WIN32
std::wstring utf8_to_wide(const std::string& utf8) {
    if (utf8.empty()) return {};
    const int count = MultiByteToWideChar(CP_UTF8, 0, utf8.data(), static_cast<int>(utf8.size()),
                                          nullptr, 0);
    std::wstring wide(count, 0);
    MultiByteToWideChar(CP_UTF8, 0, utf8.data(), static_cast<int>(utf8.size()), wide.data(), count);
    return wide;
}

std::string wide_to_utf8(const std::wstring& wide) {
    if (wide.empty()) return {};
    const int count = WideCharToMultiByte(CP_UTF8, 0, wide.data(), static_cast<int>(wide.size()),
                                         nullptr, 0, nullptr, nullptr);
    std::string utf8(count, 0);
    WideCharToMultiByte(CP_UTF8, 0, wide.data(), static_cast<int>(wide.size()), utf8.data(), count,
                        nullptr, nullptr);
    return utf8;
}
#endif

}  // namespace jarvis::util
