#include "jarvis/speech/voice.hpp"

#include "jarvis/speech/text.hpp"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <regex>
#include <set>
#include <string>
#include <vector>

namespace jarvis::speech {
namespace {

const std::set<std::string> kStopWords = {"dur",  "sus",   "kes",    "iptal", "bekle",
                                          "kapat", "yeter", "hayir", "tamam", "sessiz"};

// Google Türkçe modeli "Jarvis"i sık bozar; bu kökler tek ada bağlar.
const std::vector<std::string> kWakeRoots = {"jarv", "carv", "jarw", "carw",
                                             "jerv", "corv", "korv", "curv"};
const std::set<std::string> kWakeSpellings = {"hejiyan", "heycan", "heyjan", "hejyan", "hecyan"};
const std::set<std::string> kHeyWords = {"hey", "hej", "hi"};

const std::vector<std::string> kHostOrder = {"wasapi", "directsound", "mme", "wdm-ks"};

std::string trim(const std::string& in) {
    std::size_t a = 0, b = in.size();
    while (a < b && std::isspace(static_cast<unsigned char>(in[a]))) ++a;
    while (b > a && std::isspace(static_cast<unsigned char>(in[b - 1]))) --b;
    return in.substr(a, b - a);
}

std::vector<std::string> split_ws(const std::string& in) {
    std::vector<std::string> out;
    std::size_t i = 0;
    while (i < in.size()) {
        while (i < in.size() && std::isspace(static_cast<unsigned char>(in[i]))) ++i;
        std::size_t start = i;
        while (i < in.size() && !std::isspace(static_cast<unsigned char>(in[i]))) ++i;
        if (i > start) out.push_back(in.substr(start, i - start));
    }
    return out;
}

std::string strip_punct(const std::string& in) {
    std::size_t a = 0, b = in.size();
    static const std::string kPunct = ".,!?";
    while (a < b && kPunct.find(in[a]) != std::string::npos) ++a;
    while (b > a && kPunct.find(in[b - 1]) != std::string::npos) --b;
    return in.substr(a, b - a);
}

std::string alnum_only(const std::string& in) {
    std::string out;
    for (char c : in) {
        if (std::isalnum(static_cast<unsigned char>(c))) out.push_back(c);
    }
    return out;
}

bool wake_like(const std::string& token) {
    std::string folded = alnum_only(fold_tr(token));
    if (folded.empty()) return false;
    if (kWakeSpellings.count(folded) != 0) return true;
    for (const std::string& root : kWakeRoots) {
        if (folded.rfind(root, 0) == 0) return true;
    }
    return false;
}

std::string as_text(const nlohmann::json& value) {
    if (value.is_string()) return value.get<std::string>();
    if (value.is_number() || value.is_boolean()) return value.dump();
    return "";
}

}  // namespace

double level_from_rms(double rms) {
    if (rms <= kLevelFloor) return 0.0;
    const double span = std::log10(kLevelCeil / kLevelFloor);
    return std::max(0.0, std::min(1.0, std::log10(rms / kLevelFloor) / span));
}

std::vector<std::string> words_of(const std::string& text) {
    static const std::regex word_re("[a-z0-9]+");
    std::vector<std::string> out;
    const std::string folded = fold_tr(text);
    for (auto it = std::sregex_iterator(folded.begin(), folded.end(), word_re);
         it != std::sregex_iterator(); ++it) {
        out.push_back(it->str());
    }
    return out;
}

bool should_accept_barge(const std::string& text) {
    std::vector<std::string> words = words_of(text);
    if (words.empty()) return false;
    if (words.size() >= 2) return true;
    return kStopWords.count(words.front()) != 0;
}

std::string normalize_wake(const std::string& text) {
    std::vector<std::string> tokens = split_ws(text);
    if (tokens.empty()) return text;

    for (std::size_t index = 0; index < tokens.size(); ++index) {
        if (!wake_like(tokens[index])) continue;
        std::size_t start = index;
        if (index > 0) {
            std::string previous = strip_punct(fold_tr(tokens[index - 1]));
            if (kHeyWords.count(previous) != 0) start = index - 1;
        }
        std::vector<std::string> replaced;
        replaced.insert(replaced.end(), tokens.begin(), tokens.begin() + static_cast<long>(start));
        replaced.push_back("Jarvis");
        replaced.insert(replaced.end(), tokens.begin() + static_cast<long>(index) + 1, tokens.end());
        tokens = std::move(replaced);
        break;
    }

    // "heyjarvis biz nasılsın" → "biz" Türkçe ses tanımanın Jarvis ekidir.
    if (tokens.size() > 2 && tokens[0] == "Jarvis" &&
        strip_punct(fold_tr(tokens[1])) == "biz" &&
        fold_tr(tokens[2]).rfind("nasil", 0) == 0) {
        tokens.erase(tokens.begin() + 1);
    }

    std::string out;
    for (std::size_t i = 0; i < tokens.size(); ++i) {
        if (i) out += " ";
        out += tokens[i];
    }
    return out;
}

std::string pick_transcript(const nlohmann::json& payload) {
    if (payload.is_string()) return trim(payload.get<std::string>());
    if (!payload.is_object()) return "";

    if (!payload.contains("alternative") || !payload.at("alternative").is_array()) {
        return trim(payload.contains("transcript") ? as_text(payload.at("transcript")) : "");
    }

    std::vector<std::string> texts;
    for (const nlohmann::json& item : payload.at("alternative")) {
        if (!item.is_object() || !item.contains("transcript")) continue;
        std::string text = trim(as_text(item.at("transcript")));
        if (!text.empty()) texts.push_back(std::move(text));
    }
    if (texts.empty()) return "";
    for (const std::string& text : texts) {
        if (fold_tr(text).find("jarv") != std::string::npos) return text;
    }
    for (const std::string& text : texts) {
        for (const std::string& token : split_ws(text)) {
            if (wake_like(token)) return text;
        }
    }
    return texts.front();
}

int host_rank(const std::string& host_name) {
    std::string folded = fold_tr(host_name);
    for (std::size_t i = 0; i < kHostOrder.size(); ++i) {
        if (folded.find(kHostOrder[i]) != std::string::npos) return static_cast<int>(i);
    }
    return static_cast<int>(kHostOrder.size());
}

std::optional<int> resolve_microphone_index(
    const std::vector<std::pair<int, std::string>>& devices, const std::string& wanted_raw) {
    std::string wanted = fold_tr(trim(wanted_raw));
    if (wanted.empty()) return std::nullopt;
    for (const auto& [index, label] : devices) {
        if (fold_tr(label) == wanted) return index;
    }
    for (const auto& [index, label] : devices) {
        std::string folded = fold_tr(label);
        if (folded.rfind(wanted, 0) == 0 || wanted.rfind(folded, 0) == 0) return index;
    }
    return std::nullopt;
}

std::vector<std::pair<int, std::string>> order_microphones(
    const std::vector<std::pair<int, std::string>>& devices,
    const std::vector<std::string>& hosts) {
    struct Row {
        int rank;
        int index;
        std::string name;
    };
    std::vector<Row> rows;
    rows.reserve(devices.size());
    for (std::size_t i = 0; i < devices.size(); ++i) {
        const std::string host = i < hosts.size() ? hosts[i] : std::string();
        rows.push_back({host_rank(host), devices[i].first, devices[i].second});
    }
    std::stable_sort(rows.begin(), rows.end(), [](const Row& a, const Row& b) {
        if (a.rank != b.rank) return a.rank < b.rank;
        return a.index < b.index;
    });
    std::vector<std::pair<int, std::string>> out;
    std::set<std::string> seen;
    for (const Row& row : rows) {
        std::string key = fold_tr(row.name);
        if (seen.count(key) != 0) continue;
        seen.insert(key);
        out.emplace_back(row.index, row.name);
    }
    return out;
}

}  // namespace jarvis::speech
