#include "jarvis/core/home.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <initializer_list>
#include <regex>
#include <sstream>
#include <tuple>
#include <vector>

namespace jarvis::home {

namespace {

const std::map<std::string, int> kTurkishNumbers = {
    {"bir", 1}, {"iki", 2}, {"uc", 3}, {"dort", 4}, {"bes", 5},
    {"on", 10}, {"yirmi", 20}, {"otuz", 30}, {"kirk", 40}, {"elli", 50},
    {"altmis", 60}, {"yetmis", 70}, {"seksen", 80}, {"doksan", 90}, {"yuz", 100},
};

const std::vector<std::string> kToneKeys = {"sicak", "notr", "soguk"};
const std::vector<std::string> kFoamKeys = {"az", "orta", "bol"};
const std::vector<std::string> kSugarKeys = {"sade", "az", "orta", "cok"};

std::string tone_label(const std::string& t) {
    if (t == "sicak") return "Sıcak";
    if (t == "soguk") return "Soğuk";
    return "Nötr";
}

std::string foam_label(const std::string& f) {
    if (f == "az") return "Az köpüklü";
    if (f == "bol") return "Bol köpüklü";
    return "Orta köpüklü";
}

std::string sugar_label(const std::string& s) {
    if (s == "sade") return "Sade";
    if (s == "az") return "Az şekerli";
    if (s == "cok") return "Çok şekerli";
    return "Orta şekerli";
}

std::string ascii_lower(std::string s) {
    for (char& c : s) {
        if (c >= 'A' && c <= 'Z') c = char(c - 'A' + 'a');
    }
    return s;
}

double monotonic_now() {
    return std::chrono::duration<double>(
               std::chrono::steady_clock::now().time_since_epoch())
        .count();
}

void replace_all(std::string& s, const std::string& from, const std::string& to) {
    if (from.empty()) return;
    size_t pos = 0;
    while ((pos = s.find(from, pos)) != std::string::npos) {
        s.replace(pos, from.size(), to);
        pos += to.size();
    }
}

std::vector<std::string> split_words(const std::string& s) {
    std::vector<std::string> out;
    std::istringstream iss(s);
    std::string w;
    while (iss >> w) out.push_back(w);
    return out;
}

bool contains(const std::string& s, const std::string& sub) {
    return s.find(sub) != std::string::npos;
}

bool contains_any(const std::string& s, std::initializer_list<std::string> subs) {
    for (const auto& sub : subs) {
        if (contains(s, sub)) return true;
    }
    return false;
}

bool word_in(const std::vector<std::string>& words, const std::string& w) {
    return std::find(words.begin(), words.end(), w) != words.end();
}

bool any_word_starts_with(const std::vector<std::string>& words,
                          std::initializer_list<std::string> prefixes) {
    for (const auto& w : words) {
        for (const auto& p : prefixes) {
            if (w.rfind(p, 0) == 0) return true;
        }
    }
    return false;
}

std::optional<int> number_in(const std::string& text) {
    std::smatch m;
    if (std::regex_search(text, m, std::regex(R"(\b\d{1,3}\b)"))) {
        return std::stoi(m.str());
    }
    std::smatch after;
    bool found = std::regex_search(text, after, std::regex(R"(yuzde\s+([a-z0-9 ]+))"));
    if (!found) {
        found = std::regex_search(text, after, std::regex(R"(%\s*([a-z0-9 ]+))"));
    }
    if (found && after.size() > 1) {
        std::string raw = after[1].str();
        size_t a = raw.find_first_not_of(' ');
        if (a == std::string::npos) return std::nullopt;
        size_t b = raw.find_last_not_of(' ');
        raw = raw.substr(a, b - a + 1);
        bool digits = !raw.empty() &&
                      std::all_of(raw.begin(), raw.end(),
                                  [](char c) { return c >= '0' && c <= '9'; });
        if (digits) return std::stoi(raw);
        int total = 0;
        for (const auto& w : split_words(raw)) {
            auto it = kTurkishNumbers.find(w);
            if (it == kTurkishNumbers.end()) {
                if (total) break;
                continue;
            }
            total += it->second;
        }
        if (total) return total;
        return std::nullopt;
    }
    for (const auto& [word, value] : kTurkishNumbers) {
        if (std::regex_search(text, std::regex("\\b" + word + "\\b"))) return value;
    }
    return std::nullopt;
}

}  // namespace

bool is_tone_key(const std::string& value) {
    return std::find(kToneKeys.begin(), kToneKeys.end(), value) != kToneKeys.end();
}

bool is_foam_key(const std::string& value) {
    return std::find(kFoamKeys.begin(), kFoamKeys.end(), value) != kFoamKeys.end();
}

bool is_sugar_key(const std::string& value) {
    return std::find(kSugarKeys.begin(), kSugarKeys.end(), value) != kSugarKeys.end();
}

std::string fold_turkish(const std::string& text) {
    std::string s = text;
    replace_all(s, "İ", "i");
    replace_all(s, "ı", "i");
    replace_all(s, "Ş", "s");
    replace_all(s, "ş", "s");
    replace_all(s, "Ğ", "g");
    replace_all(s, "ğ", "g");
    replace_all(s, "Ü", "u");
    replace_all(s, "ü", "u");
    replace_all(s, "Ö", "o");
    replace_all(s, "ö", "o");
    replace_all(s, "Ç", "c");
    replace_all(s, "ç", "c");
    replace_all(s, "Â", "a");
    replace_all(s, "â", "a");
    replace_all(s, "Î", "i");
    replace_all(s, "î", "i");
    replace_all(s, "Û", "u");
    replace_all(s, "û", "u");
    for (char& c : s) {
        if (c >= 'A' && c <= 'Z') c = char(c - 'A' + 'a');
    }
    std::string out;
    bool last_space = true;
    for (char c : s) {
        bool ok = (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '%';
        if (ok) {
            out.push_back(c);
            last_space = false;
        } else if (!last_space) {
            out.push_back(' ');
            last_space = true;
        }
    }
    size_t a = out.find_first_not_of(' ');
    if (a == std::string::npos) return "";
    size_t b = out.find_last_not_of(' ');
    return out.substr(a, b - a + 1);
}

nlohmann::json Light::snapshot() const {
    return {
        {"key", key},
        {"name", name},
        {"on", on},
        {"brightness", brightness},
        {"tone", tone},
        {"tone_label", tone_label(tone)},
    };
}

std::string Light::describe() const {
    if (!on) return name + " kapalı.";
    return name + " açık · %" + std::to_string(brightness) + " · " + tone_label(tone) + ".";
}

std::string Coffee::name() const {
    if (kind == "turk") return "Türk kahvesi";
    if (kind == "filtre") return "Filtre kahve";
    return "Kahve";
}

nlohmann::json Coffee::snapshot(double now) const {
    std::string status = "bos";
    int remaining = 0;
    if (started_at && !ready) {
        status = "demleniyor";
        double at = ready_at.value_or(now);
        remaining = std::max(0, int(std::lround(at - now)));
    } else if (ready) {
        status = "hazir";
    }
    return {
        {"kind", kind},
        {"name", name()},
        {"cups", cups},
        {"foam", foam},
        {"foam_label", foam_label(foam)},
        {"sugar", sugar},
        {"sugar_label", sugar_label(sugar)},
        {"status", status},
        {"remaining", remaining},
        {"ready", ready},
        {"keep_warm", keep_warm},
    };
}

std::string Coffee::describe(double now) const {
    if (started_at && !ready) {
        double at = ready_at.value_or(now);
        int remaining = std::max(0, int(std::lround(at - now)));
        std::string extra = kind == "filtre" ? " · " + std::to_string(cups) + " fincan" : "";
        return name() + " demleniyor · " + std::to_string(remaining) + " sn" + extra;
    }
    if (ready) {
        std::string detail;
        if (kind == "turk") {
            detail = foam_label(foam) + ", " + ascii_lower(sugar_label(sugar));
        } else {
            detail = std::to_string(cups) + " fincan";
            if (keep_warm) detail += " · sıcak tutuluyor";
        }
        return name() + " hazır · " + detail;
    }
    return name() + " makinesi boş.";
}

SmartHome::SmartHome(Clock clock)
    : clock_(clock ? std::move(clock) : Clock(&monotonic_now)) {
    lights_.emplace("salon", Light{"salon", "Salon ışığı"});
    lights_.emplace("calisma", Light{"calisma", "Çalışma odası ışığı"});
    coffees_.emplace("turk", Coffee{"turk"});
    coffees_.emplace("filtre", Coffee{"filtre", 2});
}

double SmartHome::now_() const {
    return clock_();
}

double SmartHome::now() const {
    return now_();
}

Light& SmartHome::light(const std::string& key) {
    auto it = lights_.find(key);
    if (it != lights_.end()) return it->second;
    return lights_["salon"];
}

Light& SmartHome::set_light(const std::string& key,
                            std::optional<bool> on,
                            std::optional<int> brightness,
                            std::optional<std::string> tone) {
    Light& light = this->light(key);
    if (brightness) {
        light.brightness = std::clamp(*brightness, 0, 100);
        if (!on) light.on = light.brightness > 0;
    }
    if (tone) {
        if (std::find(kToneKeys.begin(), kToneKeys.end(), *tone) != kToneKeys.end()) {
            light.tone = *tone;
        }
    }
    if (on) light.on = *on;
    return light;
}

std::vector<Light> SmartHome::set_all_lights(std::optional<bool> on,
                                             std::optional<int> brightness,
                                             std::optional<std::string> tone) {
    std::vector<Light> result;
    for (const auto& [key, unused] : lights_) {
        (void)unused;
        result.push_back(set_light(key, on, brightness, tone));
    }
    return result;
}

std::tuple<bool, Coffee, double> SmartHome::brew(const std::string& kind,
                                                 std::optional<int> cups,
                                                 std::optional<std::string> foam,
                                                 std::optional<std::string> sugar) {
    std::string k = coffees_.count(kind) ? kind : "turk";
    Coffee& coffee = coffees_[k];
    double now = now_();
    advance(coffee, now);
    if (coffee.started_at && !coffee.ready) {
        return {false, coffee, 0.0};
    }
    double seconds;
    if (k == "filtre") {
        int c = cups.value_or(coffee.cups ? coffee.cups : 2);
        coffee.cups = std::clamp(c, 1, 4);
        seconds = double(kFilterSecondsPerCup) * coffee.cups;
    } else {
        coffee.cups = 1;
        seconds = kTurkBrewSeconds;
        if (foam &&
            std::find(kFoamKeys.begin(), kFoamKeys.end(), *foam) != kFoamKeys.end()) {
            coffee.foam = *foam;
        }
        if (sugar &&
            std::find(kSugarKeys.begin(), kSugarKeys.end(), *sugar) != kSugarKeys.end()) {
            coffee.sugar = *sugar;
        }
    }
    coffee.started_at = now;
    coffee.ready_at = now + seconds;
    coffee.ready = false;
    coffee.brewed_at = std::nullopt;
    return {true, coffee, seconds};
}

Coffee SmartHome::coffee(const std::string& kind) {
    Coffee& coffee = coffees_.count(kind) ? coffees_[kind] : coffees_["turk"];
    advance(coffee, now_());
    return coffee;
}

Coffee SmartHome::cancel_coffee(const std::string& kind) {
    Coffee& coffee = coffees_.count(kind) ? coffees_[kind] : coffees_["turk"];
    coffee.started_at = std::nullopt;
    coffee.ready_at = std::nullopt;
    coffee.ready = false;
    coffee.brewed_at = std::nullopt;
    return coffee;
}

void SmartHome::advance(Coffee& coffee, double now) {
    if (!coffee.started_at || coffee.ready) return;
    if (coffee.ready_at && now >= *coffee.ready_at) {
        coffee.ready = true;
        coffee.started_at = std::nullopt;
        coffee.brewed_at = coffee.ready_at;
    }
}

nlohmann::json SmartHome::snapshot() const {
    double now = now_();
    nlohmann::json lights = nlohmann::json::object();
    for (const auto& [key, light] : lights_) {
        lights[key] = light.snapshot();
    }
    nlohmann::json coffees = nlohmann::json::object();
    for (const auto& [key, coffee] : coffees_) {
        coffees[key] = coffee.snapshot(now);
    }
    return {{"lights", lights}, {"coffees", coffees}, {"now", now}};
}

std::optional<nlohmann::json> home_voice_intent(const std::string& text) {
    std::string normalized = fold_turkish(text);
    if (normalized.empty()) return std::nullopt;
    std::vector<std::string> words = split_words(normalized);
    std::string padded = " " + normalized + " ";
    if (contains(padded, " sonra ") || contains(padded, " ardindan ")) {
        return std::nullopt;
    }

    if (contains_any(normalized, {"ev panel", "akilli ev panel", "akilli evi ac", "evi yonet"})) {
        return nlohmann::json{{"action", "panel"}};
    }

    std::vector<std::string> rooms;
    if (contains(normalized, "salon") || contains(normalized, "oturma odasi")) {
        rooms.push_back("salon");
    }
    if (contains(normalized, "calisma") || contains(normalized, "ofis")) {
        rooms.push_back("calisma");
    }

    bool coffee_request = contains_any(normalized, {"kahve", "kahvem", "fincan"});
    bool light_request = contains_any(normalized, {"isik", "isig", "aydinlat", "lamba"});
    if (!light_request && !rooms.empty() &&
        contains_any(normalized, {"parlak", "yuzde", "%", "sicak", "soguk", "notr"})) {
        light_request = true;
    }
    if (!light_request && !coffee_request) return std::nullopt;

    if (light_request && coffee_request) return std::nullopt;

    if (coffee_request) {
        std::string kind = contains(normalized, "filtre") ? "filtre" : "turk";
        if (any_word_starts_with(words, {"hazir", "durum", "bitti", "tamam"}) ||
            contains(normalized, "ne zaman")) {
            return nlohmann::json{{"action", "coffee_status"}, {"kind", kind}};
        }
        if (any_word_starts_with(words, {"iptal", "durdur", "bosalt"})) {
            return nlohmann::json{{"action", "coffee_cancel"}, {"kind", kind}};
        }
        if (any_word_starts_with(words, {"yap", "demle", "pisir", "hazirla", "koy"})) {
            nlohmann::json intent = {{"action", "coffee_brew"}, {"kind", kind}};
            auto number = number_in(normalized);
            if (kind == "filtre") {
                intent["cups"] = std::clamp(number.value_or(2), 1, 4);
            }
            if (word_in(words, "bol")) intent["foam"] = "bol";
            else if (word_in(words, "az")) intent["foam"] = "az";
            else if (word_in(words, "orta")) intent["foam"] = "orta";
            if (word_in(words, "sade")) {
                intent["sugar"] = "sade";
            } else if (word_in(words, "sekerli") || word_in(words, "seker")) {
                if (word_in(words, "cok")) intent["sugar"] = "cok";
                else if (word_in(words, "az")) intent["sugar"] = "az";
                else intent["sugar"] = "orta";
            }
            return intent;
        }
        if (contains(normalized, "hazir mi") || contains(normalized, "demleniyor mu")) {
            return nlohmann::json{{"action", "coffee_status"}, {"kind", kind}};
        }
        return std::nullopt;
    }

    std::string room;
    if (word_in(words, "tum") || word_in(words, "butun") || word_in(words, "hepsi")) {
        room = "all";
    } else if (rooms.size() > 1) {
        room = "all";
    } else if (!rooms.empty()) {
        room = rooms[0];
    } else {
        room = any_word_starts_with(words, {"isiklar"}) ? "all" : "salon";
    }

    if (contains(normalized, "acik mi") || contains(normalized, "kapali mi") ||
        contains(normalized, "isiklar nasil")) {
        return nlohmann::json{{"action", "light_status"}, {"room", room}};
    }

    std::optional<bool> on;
    bool close = false;
    for (const auto& w : words) {
        if (w.rfind("kapat", 0) == 0 || w == "kapa" || w.rfind("sondur", 0) == 0) {
            on = false;
            close = true;
            break;
        }
    }
    if (!close) {
        for (const auto& w : words) {
            bool excl = w.rfind("acik", 0) == 0 || w.rfind("acaba", 0) == 0 ||
                        w.rfind("acikl", 0) == 0;
            if (w.rfind("ac", 0) == 0 && !excl) {
                on = true;
                break;
            }
        }
    }

    std::optional<int> brightness;
    if (contains_any(normalized, {"parlak", "yuzde", "%"})) {
        brightness = number_in(normalized);
    }
    std::optional<std::string> tone;
    for (const auto& key : kToneKeys) {
        if (word_in(words, key) || (key == "notr" && contains(normalized, "notr"))) {
            tone = key;
        }
    }
    if (!on && !brightness && !tone) return std::nullopt;

    nlohmann::json intent = {{"action", "light"}, {"room", room}};
    intent["on"] = on ? nlohmann::json(*on) : nlohmann::json(nullptr);
    intent["brightness"] =
        brightness ? nlohmann::json(std::clamp(*brightness, 0, 100)) : nlohmann::json(nullptr);
    intent["tone"] = tone ? nlohmann::json(*tone) : nlohmann::json(nullptr);
    return intent;
}

}  // namespace jarvis::home
