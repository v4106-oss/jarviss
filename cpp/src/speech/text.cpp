#include "jarvis/speech/text.hpp"

#include <algorithm>
#include <cctype>
#include <cstdio>
#include <cwctype>
#include <map>
#include <regex>
#include <string>
#include <vector>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

namespace jarvis::speech {
namespace {

const std::string kDefaultStyle = "jarvis";

// Geniş karakter üzerinde çalışmak Python'daki len() sayımını birebir verir:
// parça sınırları bayt değil karakter sayar.
std::wstring to_w(const std::string& s) {
#ifdef _WIN32
    if (s.empty()) return {};
    int n = MultiByteToWideChar(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), nullptr, 0);
    std::wstring w(n, 0);
    MultiByteToWideChar(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), w.data(), n);
    return w;
#else
    std::wstring w;
    w.reserve(s.size());
    for (unsigned char c : s) w.push_back(static_cast<wchar_t>(c));
    return w;
#endif
}

std::string to_u8(const std::wstring& w) {
#ifdef _WIN32
    if (w.empty()) return {};
    int n = WideCharToMultiByte(CP_UTF8, 0, w.data(), static_cast<int>(w.size()),
                                nullptr, 0, nullptr, nullptr);
    std::string s(n, 0);
    WideCharToMultiByte(CP_UTF8, 0, w.data(), static_cast<int>(w.size()), s.data(), n,
                        nullptr, nullptr);
    return s;
#else
    std::string s;
    s.reserve(w.size());
    for (wchar_t c : w) s.push_back(static_cast<char>(c));
    return s;
#endif
}

bool is_space(wchar_t c) { return std::iswspace(c) != 0; }

bool is_sentence_end(wchar_t c) {
    return c == L'.' || c == L'!' || c == L'?' || c == L';' || c == L':' || c == L'…';
}

std::wstring strip_ws(const std::wstring& in) {
    std::size_t a = 0, b = in.size();
    while (a < b && is_space(in[a])) ++a;
    while (b > a && is_space(in[b - 1])) --b;
    return in.substr(a, b - a);
}

std::wstring rstrip_chars(const std::wstring& in, const std::wstring& chars) {
    std::size_t b = in.size();
    while (b > 0 && chars.find(in[b - 1]) != std::wstring::npos) --b;
    return in.substr(0, b);
}

void replace_all(std::string& s, std::string_view from, std::string_view to) {
    if (from.empty()) return;
    std::size_t pos = 0;
    while ((pos = s.find(from, pos)) != std::string::npos) {
        s.replace(pos, from.size(), to);
        pos += to.size();
    }
}

std::vector<std::wstring> split_ws_runs(const std::wstring& in) {
    std::vector<std::wstring> out;
    std::size_t i = 0;
    while (i < in.size()) {
        while (i < in.size() && is_space(in[i])) ++i;
        std::size_t start = i;
        while (i < in.size() && !is_space(in[i])) ++i;
        if (i > start) out.push_back(in.substr(start, i - start));
    }
    return out;
}

// Python re.split(r"(?<=[.!?…;:])\s+") — ECMAScript'te lookbehind yok, elle taranır.
std::vector<std::wstring> split_sentences(const std::wstring& clean) {
    std::vector<std::wstring> out;
    std::size_t piece_start = 0;
    std::size_t i = 0;
    while (i < clean.size()) {
        if (is_space(clean[i]) && i > 0 && is_sentence_end(clean[i - 1])) {
            std::size_t run = i;
            while (run < clean.size() && is_space(clean[run])) ++run;
            std::wstring piece = clean.substr(piece_start, i - piece_start);
            if (!piece.empty()) out.push_back(piece);
            piece_start = run;
            i = run;
            continue;
        }
        ++i;
    }
    if (piece_start < clean.size()) out.push_back(clean.substr(piece_start));
    return out;
}

std::vector<std::wstring> split_long_w(const std::wstring& text, int max_chars) {
    std::vector<std::wstring> parts;
    std::wstring current;
    for (const std::wstring& word : split_ws_runs(text)) {
        if (current.empty()) {
            current = word;
        } else if (static_cast<int>(current.size()) + 1 + static_cast<int>(word.size()) <= max_chars) {
            current += L" ";
            current += word;
        } else {
            parts.push_back(current);
            current = word;
        }
    }
    if (!current.empty()) parts.push_back(current);
    return parts;
}

std::string trim_lower(const std::string& in) {
    std::size_t a = 0, b = in.size();
    while (a < b && std::isspace(static_cast<unsigned char>(in[a]))) ++a;
    while (b > a && std::isspace(static_cast<unsigned char>(in[b - 1]))) --b;
    std::string out = in.substr(a, b - a);
    for (char& c : out) {
        if (c >= 'A' && c <= 'Z') c = static_cast<char>(c - 'A' + 'a');
    }
    return out;
}

std::pair<std::wstring, std::wstring> split_head(const std::wstring& text, int limit) {
    if (static_cast<int>(text.size()) <= limit) return {text, L""};
    std::wstring window = text.substr(0, static_cast<std::size_t>(limit));
    // Python rfind bulunamadığında -1 verir; npos ile karıştırılırsa max() her zaman npos'u seçer.
    auto signed_pos = [](std::size_t v) {
        return v == std::wstring::npos ? -1LL : static_cast<long long>(v);
    };
    long long cut = std::max({signed_pos(window.rfind(L", ")),
                              signed_pos(window.rfind(L"; ")),
                              signed_pos(window.rfind(L": "))});
    if (cut >= 24 && cut + 1 < static_cast<long long>(text.size())) {
        std::size_t at = static_cast<std::size_t>(cut);
        return {strip_ws(text.substr(0, at + 1)), strip_ws(text.substr(at + 1))};
    }
    cut = signed_pos(window.rfind(L' '));
    if (cut <= 0) {
        return {window, strip_ws(text.substr(window.size()))};
    }
    std::size_t at = static_cast<std::size_t>(cut);
    return {strip_ws(text.substr(0, at)), strip_ws(text.substr(at))};
}

const std::map<std::string, VoiceStyle>& style_table() {
    static const std::map<std::string, VoiceStyle> table = {
        {"jarvis", {kDefaultStyle, "en-US-AndrewMultilingualNeural", "-10Hz",
                    "Jarvis · özel, derin, hızlı"}},
        {"natural", {"natural", "tr-TR-EmelNeural", "+0Hz", "Emel · doğal Türkçe"}},
        {"warm", {"warm", "en-US-AvaMultilingualNeural", "+0Hz", "Ava · sıcak kadın"}},
        {"male", {"male", "en-AU-WilliamMultilingualNeural", "-10Hz", "William · derin erkek"}},
    };
    return table;
}

const std::map<std::string, int>& rate_offset_table() {
    static const std::map<std::string, int> offsets = {
        {"jarvis", 24}, {"natural", 12}, {"warm", 24}, {"male", 22}};
    return offsets;
}

std::string style_key(const std::string& style) {
    std::string key = trim_lower(style);
    return key.empty() ? kDefaultStyle : key;
}

}  // namespace

std::string fold_tr(const std::string& text) {
    std::string s = text;
    // Sıra önemli: çok baytlı harfler ASCII "I" değişimine dokunmaz.
    static const std::pair<const char*, const char*> kMap[] = {
        {"İ", "i"}, {"ı", "i"}, {"Ş", "s"}, {"ş", "s"}, {"Ğ", "g"}, {"ğ", "g"},
        {"Ü", "u"}, {"ü", "u"}, {"Ö", "o"}, {"ö", "o"}, {"Ç", "c"}, {"ç", "c"},
    };
    for (const auto& [from, to] : kMap) replace_all(s, from, to);
    for (char& c : s) {
        if (c >= 'A' && c <= 'Z') c = static_cast<char>(c - 'A' + 'a');
    }
    return s;
}

const VoiceStyle& style_parts(const std::string& style) {
    const auto& table = style_table();
    auto it = table.find(style_key(style));
    return it != table.end() ? it->second : table.at(kDefaultStyle);
}

std::string style_label(const std::string& style) { return style_parts(style).label; }

int style_rate_offset(const std::string& style) {
    const auto& offsets = rate_offset_table();
    auto it = offsets.find(style_key(style));
    return it != offsets.end() ? it->second : offsets.at(kDefaultStyle);
}

std::string rate_text(int voice_rate, const std::string& style) {
    int value = voice_rate + style_rate_offset(style);
    value = std::max(-20, std::min(24, value));
    char buffer[16];
    std::snprintf(buffer, sizeof(buffer), "%+d%%", value);
    return buffer;
}

std::string clean_for_speech(const std::string& text) {
    static const std::wregex code_block(L"```[\\s\\S]*?```");
    static const std::wregex inline_code(L"`([^`]*)`");
    static const std::wregex image(L"!\\[[^\\]]*\\]\\([^)]+\\)");
    static const std::wregex link(L"\\[([^\\]]+)\\]\\([^)]+\\)");
    static const std::wregex url(L"https?://\\S+");
    static const std::wregex marks(L"[*_#>~|]+");
    static const std::wregex space_run(L"\\s+");

    std::wstring w = to_w(text);
    w = std::regex_replace(w, code_block, L" ");
    w = std::regex_replace(w, inline_code, L"$1");
    w = std::regex_replace(w, image, L" ");
    w = std::regex_replace(w, link, L"$1");
    w = std::regex_replace(w, url, L" ");
    w = std::regex_replace(w, marks, L" ");
    w = std::regex_replace(w, space_run, L" ");
    w = strip_ws(w);

    std::wstring printable;
    printable.reserve(w.size());
    for (wchar_t c : w) {
        if (std::iswprint(c) != 0) printable.push_back(c);
    }

    if (static_cast<int>(printable.size()) > kMaxSpeechChars) {
        std::wstring head = printable.substr(0, static_cast<std::size_t>(kMaxSpeechChars));
        std::size_t sp = head.rfind(L' ');
        if (sp != std::wstring::npos) head = head.substr(0, sp);
        printable = rstrip_chars(head, L" ,;:") + L"…";
    }
    return to_u8(printable);
}

std::vector<std::string> split_for_speech(const std::string& text, int max_chars) {
    std::wstring clean = to_w(clean_for_speech(text));
    if (clean.empty()) return {};
    std::vector<std::string> chunks;
    std::wstring current;
    for (const std::wstring& sentence : split_sentences(clean)) {
        std::vector<std::wstring> pieces =
            static_cast<int>(sentence.size()) <= max_chars
                ? std::vector<std::wstring>{sentence}
                : split_long_w(sentence, max_chars);
        for (const std::wstring& piece : pieces) {
            if (current.empty()) {
                current = piece;
            } else if (static_cast<int>(current.size()) + 1 + static_cast<int>(piece.size()) <=
                       max_chars) {
                current += L" ";
                current += piece;
            } else {
                chunks.push_back(to_u8(current));
                current = piece;
            }
        }
    }
    if (!current.empty()) chunks.push_back(to_u8(current));
    return chunks;
}

std::vector<std::string> plan_chunks(const std::string& text, int first_chars, int max_chars) {
    std::wstring clean = to_w(clean_for_speech(text));
    if (clean.empty()) return {};
    std::vector<std::wstring> sentences = split_sentences(clean);
    std::wstring first = sentences.empty() ? clean : sentences.front();
    std::vector<std::wstring> rest;
    if (!sentences.empty()) rest.assign(sentences.begin() + 1, sentences.end());
    if (static_cast<int>(first.size()) > first_chars) {
        auto [head, tail] = split_head(first, first_chars);
        first = head;
        if (!tail.empty()) rest.insert(rest.begin(), tail);
    }
    std::vector<std::string> chunks;
    if (!first.empty()) chunks.push_back(to_u8(first));
    std::wstring joined;
    for (std::size_t i = 0; i < rest.size(); ++i) {
        if (i) joined += L" ";
        joined += rest[i];
    }
    std::string rest_text = to_u8(strip_ws(joined));
    if (!rest_text.empty()) {
        for (std::string& chunk : split_for_speech(rest_text, max_chars)) {
            if (!chunk.empty()) chunks.push_back(std::move(chunk));
        }
    }
    return chunks;
}

std::string concise_for_voice(const std::string& text, int max_chars) {
    std::string clean = clean_for_speech(text);
    std::wstring wide = to_w(clean);
    if (static_cast<int>(wide.size()) <= max_chars) return clean;

    std::wstring result;
    for (const std::wstring& sentence : split_sentences(wide)) {
        std::wstring candidate = result.empty() ? sentence : result + L" " + sentence;
        if (!result.empty() && static_cast<int>(candidate.size()) > max_chars) break;
        if (result.empty() && static_cast<int>(sentence.size()) > max_chars) {
            result = rstrip_chars(split_head(sentence, max_chars).first, L" ,;:");
            break;
        }
        result = candidate;
        if (static_cast<int>(result.size()) >= max_chars * 0.6) break;
    }
    if (result.empty()) result = wide.substr(0, static_cast<std::size_t>(max_chars));
    return to_u8(result);
}

std::optional<int> spoken_limit_for_prompt(const std::string& prompt, int concise) {
    static const std::regex long_speech(
        "\\b(?:haber\\w*|oku|okur\\w*|okuy\\w*|okuma\\w*|detay\\w*|hepsini|tumunu|butun\\w*"
        "|liste\\w*|maddeler\\w*|acikla\\w*|anlat\\w*)\\b");
    if (std::regex_search(fold_tr(prompt), long_speech)) return std::nullopt;
    return concise;
}

}  // namespace jarvis::speech
