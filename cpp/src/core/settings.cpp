#include <jarvis/core/settings.hpp>

#include <algorithm>
#include <array>
#include <climits>
#include <cstdlib>
#include <fstream>
#include <set>
#include <stdexcept>

#include <jarvis/core/util.hpp>

namespace jarvis::core {
namespace {

std::string env_or(const char* name, const std::string& fallback) {
    if (const char* value = std::getenv(name)) {
        if (*value) return value;
    }
    return fallback;
}

long long to_ll(const nlohmann::json& value, long long fallback) {
    if (value.is_number_integer()) return value.get<long long>();
    if (value.is_number_unsigned()) {
        const unsigned long long u = value.get<unsigned long long>();
        if (u > static_cast<unsigned long long>(LLONG_MAX)) return fallback;
        return static_cast<long long>(u);
    }
    if (value.is_number_float()) return static_cast<long long>(value.get<double>());
    if (value.is_string()) {
        try {
            return std::stoll(value.get<std::string>());
        } catch (...) {
            return fallback;
        }
    }
    return fallback;
}

bool parse_double(const nlohmann::json& value, double& out) {
    if (value.is_number()) {
        out = value.get<double>();
        return true;
    }
    if (value.is_string()) {
        try {
            out = std::stod(value.get<std::string>());
            return true;
        } catch (...) {
            return false;
        }
    }
    return false;
}

bool to_bool(const nlohmann::json& data, const char* key, bool fallback) {
    const auto it = data.find(key);
    if (it == data.end() || it->is_null()) return fallback;
    if (it->is_boolean()) return it->get<bool>();
    if (it->is_number()) return it->get<double>() != 0.0;
    if (it->is_string()) return !it->get<std::string>().empty();
    return fallback;
}

std::string to_str(const nlohmann::json& data, const char* key, const std::string& fallback) {
    const auto it = data.find(key);
    if (it == data.end() || it->is_null()) return fallback;
    if (it->is_string()) return it->get<std::string>();
    if (it->is_number_integer()) return std::to_string(it->get<long long>());
    if (it->is_number_unsigned()) return std::to_string(it->get<unsigned long long>());
    if (it->is_number_float()) return std::to_string(it->get<double>());
    return fallback;
}

bool is_provider(std::string_view value) {
    return value == "gemini" || value == "ollama" || value == "tokenharbor";
}

constexpr std::array<std::string_view, 4> kVoiceStyles = {"jarvis", "natural", "warm", "male"};

std::string normalize_voice_style(std::string_view value) {
    const std::string folded = util::to_lower_ascii(util::trim(value));
    for (const auto style : kVoiceStyles) {
        if (folded == style) return std::string(style);
    }
    return std::string(kDefaultVoiceStyle);
}

std::filesystem::path default_settings_path() {
    std::filesystem::path root;
    if (const char* local = std::getenv("LOCALAPPDATA"); local && *local) {
        root = std::filesystem::path(local);
    } else if (const char* profile = std::getenv("USERPROFILE"); profile && *profile) {
        root = std::filesystem::path(profile);
    } else {
        root = std::filesystem::temp_directory_path();
    }
    return root / "Jarvis" / "settings.json";
}

}  // namespace

Settings::Settings() : Settings(default_settings_path()) {}

Settings::Settings(std::filesystem::path file_path) : path_(std::move(file_path)) {
    load();
}

void Settings::load() {
    data_ = nlohmann::json::object();
    if (!std::filesystem::exists(path_)) return;
    std::ifstream in(path_);
    if (!in) return;
    try {
        in >> data_;
        if (!data_.is_object()) data_ = nlohmann::json::object();
    } catch (...) {
        data_ = nlohmann::json::object();
    }
}

void Settings::write() const {
    std::error_code ec;
    std::filesystem::create_directories(path_.parent_path(), ec);
    std::ofstream out(path_);
    if (!out) return;
    out << data_.dump(2, ' ', false, nlohmann::json::error_handler_t::ignore);
}

std::string Settings::model() const {
    const std::string value = util::trim(to_str(data_, "gemini_model", std::string(kDefaultModel)));
    return value.empty() ? std::string(kDefaultModel) : value;
}

std::string Settings::api_key() const {
    std::string key = env_or("GEMINI_API_KEY", "");
    if (key.empty()) key = to_str(data_, "gemini_api_key", "");
    return util::trim(key);
}

std::string Settings::provider() const {
    const std::string value = to_str(data_, "ai_provider", "");
    return is_provider(value) ? value : "gemini";
}

std::string Settings::provider_label() const {
    const std::string value = provider();
    if (value == "ollama") return "Ollama";
    if (value == "tokenharbor") return "Token Harbor";
    return "Gemini";
}

std::string Settings::active_model() const {
    const std::string value = provider();
    if (value == "tokenharbor") return tokenharbor_model();
    if (value == "ollama") return ollama_model();
    return model();
}

std::string Settings::ollama_url() const {
    std::string value = util::trim(to_str(data_, "ollama_url", std::string(kDefaultOllamaUrl)));
    while (!value.empty() && value.back() == '/') value.pop_back();
    return value.empty() ? std::string(kDefaultOllamaUrl) : value;
}

std::string Settings::ollama_model() const {
    const std::string value =
        util::trim(to_str(data_, "ollama_model", std::string(kDefaultOllamaModel)));
    return value.empty() ? std::string(kDefaultOllamaModel) : value;
}

std::string Settings::tokenharbor_url() const {
    std::string value =
        util::trim(to_str(data_, "tokenharbor_url", std::string(kDefaultTokenHarborUrl)));
    while (!value.empty() && value.back() == '/') value.pop_back();
    return value.empty() ? std::string(kDefaultTokenHarborUrl) : value;
}

std::string Settings::tokenharbor_model() const {
    return util::trim(to_str(data_, "tokenharbor_model", ""));
}

std::string Settings::tokenharbor_api_key() const {
    std::string key = env_or("TOKENHARBOR_API_KEY", "");
    if (key.empty()) key = to_str(data_, "tokenharbor_api_key", "");
    return util::trim(key);
}

std::filesystem::path Settings::data_dir() const {
    return path_.parent_path();
}

std::string Settings::theme() const {
    return util::normalize_theme(to_str(data_, "theme", ""));
}

void Settings::set_theme(std::string name) {
    data_["theme"] = util::normalize_theme(name);
    write();
}

std::string Settings::access_mode() const {
    const std::string value = to_str(data_, "access_mode", "");
    return (value == "approval" || value == "step" || value == "full") ? value : "approval";
}

bool Settings::strict_after_web() const {
    return to_bool(data_, "strict_after_web", true);
}

void Settings::set_access(std::string mode, bool strict_after_web) {
    data_["access_mode"] =
        (mode == "approval" || mode == "step" || mode == "full") ? mode : "approval";
    data_["strict_after_web"] = strict_after_web;
    write();
}

bool Settings::screen_context() const {
    return to_bool(data_, "screen_context", false);
}

void Settings::set_screen_context(bool enabled) {
    data_["screen_context"] = enabled;
    write();
}

bool Settings::voice_replies() const {
    return to_bool(data_, "voice_replies", true);
}

void Settings::set_voice_replies(bool enabled) {
    data_["voice_replies"] = enabled;
    write();
}

bool Settings::companion_enabled() const {
    return to_bool(data_, "companion_enabled", true);
}

int Settings::companion_interval() const {
    const auto it = data_.find("companion_interval");
    const int value = it == data_.end() ? 90 : static_cast<int>(to_ll(*it, 90));
    return util::clamp_int(value, 15, 480);
}

std::string Settings::companion_quiet_start() const {
    return util::normalize_clock_text(to_str(data_, "companion_quiet_start", ""), "23:00");
}

std::string Settings::companion_quiet_end() const {
    return util::normalize_clock_text(to_str(data_, "companion_quiet_end", ""), "08:00");
}

bool Settings::companion_hydration() const {
    return to_bool(data_, "companion_hydration", true);
}

bool Settings::companion_speak() const {
    return to_bool(data_, "companion_speak", false);
}

void Settings::set_companion(std::optional<bool> enabled,
                             std::optional<int> interval,
                             std::optional<std::string> quiet_start,
                             std::optional<std::string> quiet_end,
                             std::optional<bool> hydration,
                             std::optional<bool> speak) {
    if (enabled) data_["companion_enabled"] = *enabled;
    if (interval) data_["companion_interval"] = util::clamp_int(*interval, 15, 480);
    if (quiet_start) {
        data_["companion_quiet_start"] = util::normalize_clock_text(*quiet_start, "23:00");
    }
    if (quiet_end) {
        data_["companion_quiet_end"] = util::normalize_clock_text(*quiet_end, "08:00");
    }
    if (hydration) data_["companion_hydration"] = *hydration;
    if (speak) data_["companion_speak"] = *speak;
    write();
}

std::string Settings::mic_device() const {
    return util::trim(to_str(data_, "mic_device", ""));
}

std::string Settings::stt_api_key() const {
    std::string key = env_or("JARVIS_STT_KEY", "");
    if (key.empty()) key = to_str(data_, "stt_api_key", "");
    return util::trim(key);
}

std::string Settings::speaker_device() const {
    return util::trim(to_str(data_, "speaker_device", ""));
}

int Settings::camera_index() const {
    const auto it = data_.find("camera_index");
    const int value = it == data_.end() ? 0 : static_cast<int>(to_ll(*it, 0));
    return util::clamp_int(value, 0, 16);
}

std::string Settings::voice_style() const {
    return normalize_voice_style(to_str(data_, "voice_style", ""));
}

int Settings::voice_rate() const {
    const auto it = data_.find("voice_rate");
    const int value = it == data_.end() ? -3 : static_cast<int>(to_ll(*it, -3));
    return util::clamp_int(value, -20, 20);
}

int Settings::voice_volume() const {
    const auto it = data_.find("voice_volume");
    const int value = it == data_.end() ? 90 : static_cast<int>(to_ll(*it, 90));
    return util::clamp_int(value, 0, 100);
}

bool Settings::voice_headphones() const {
    return to_bool(data_, "voice_headphones", true);
}

void Settings::set_audio(std::string mic_device,
                         std::string speaker_device,
                         int camera_index,
                         std::string voice_style,
                         int voice_rate,
                         int voice_volume,
                         bool headphones) {
    data_["mic_device"] = util::trim(mic_device);
    data_["speaker_device"] = util::trim(speaker_device);
    data_["camera_index"] = util::clamp_int(camera_index, 0, 16);
    data_["voice_style"] = normalize_voice_style(voice_style);
    data_["voice_rate"] = util::clamp_int(voice_rate, -20, 20);
    data_["voice_volume"] = util::clamp_int(voice_volume, 0, 100);
    data_["voice_headphones"] = headphones;
    write();
}

int Settings::limit(std::string_view key) const {
    const auto it = data_.find(key);
    if (it == data_.end()) return 0;
    const long long value = to_ll(*it, 0);
    return value < 0 ? 0 : static_cast<int>(value);
}

int Settings::agent_step_limit() const {
    return limit("agent_step_limit");
}

int Settings::agent_seconds_limit() const {
    return limit("agent_seconds_limit");
}

int Settings::agent_loop_limit() const {
    return limit("agent_loop_limit");
}

void Settings::set_agent_limits(int step, int seconds, int loop) {
    data_["agent_step_limit"] = std::max(0, step);
    data_["agent_seconds_limit"] = std::max(0, seconds);
    data_["agent_loop_limit"] = std::max(0, loop);
    write();
}

std::vector<std::string> Settings::extra_roots() const {
    std::vector<std::string> roots;
    const auto it = data_.find("extra_roots");
    if (it == data_.end() || !it->is_array()) return roots;
    for (const auto& entry : *it) {
        if (!entry.is_string()) continue;
        std::string path = util::trim(entry.get<std::string>());
        if (!path.empty()) roots.push_back(std::move(path));
    }
    return roots;
}

void Settings::add_root(std::string path) {
    std::vector<std::string> roots = extra_roots();
    if (std::find(roots.begin(), roots.end(), path) == roots.end()) {
        roots.push_back(path);
        data_["extra_roots"] = roots;
        write();
    }
}

void Settings::remove_root(std::string path) {
    std::vector<std::string> roots = extra_roots();
    roots.erase(std::remove(roots.begin(), roots.end(), path), roots.end());
    data_["extra_roots"] = roots;
    write();
}

void Settings::set_roots(const std::vector<std::string>& roots) {
    std::vector<std::string> cleaned;
    std::set<std::string> seen;
    for (const auto& raw : roots) {
        std::string path = util::trim(raw);
        if (path.empty() || !seen.insert(path).second) continue;
        cleaned.push_back(std::move(path));
    }
    data_["extra_roots"] = cleaned;
    write();
}

std::optional<std::pair<int, int>> Settings::floating_hud_position() const {
    const auto it = data_.find("floating_hud_position");
    if (it == data_.end() || !it->is_object()) return std::nullopt;
    const auto x = it->find("x");
    const auto y = it->find("y");
    if (x == it->end() || y == it->end()) return std::nullopt;
    return std::make_pair(static_cast<int>(to_ll(*x, 0)), static_cast<int>(to_ll(*y, 0)));
}

void Settings::save_floating_hud_position(int x, int y) {
    data_["floating_hud_position"] = {{"x", x}, {"y", y}};
    write();
}

void Settings::clear_floating_hud_position() {
    if (data_.find("floating_hud_position") != data_.end()) {
        data_.erase("floating_hud_position");
        write();
    }
}

bool Settings::home_is_set() const {
    const auto it = data_.find("home_location");
    if (it == data_.end() || !it->is_object()) return false;
    const auto lat_it = it->find("lat");
    const auto lon_it = it->find("lon");
    if (lat_it == it->end() || lon_it == it->end()) return false;
    double lat = 0.0;
    double lon = 0.0;
    if (!parse_double(*lat_it, lat) || !parse_double(*lon_it, lon)) return false;
    return lat >= -90.0 && lat <= 90.0 && lon >= -180.0 && lon <= 180.0;
}

HomeLocation Settings::home_location() const {
    HomeLocation home;
    const auto it = data_.find("home_location");
    if (it == data_.end() || !it->is_object()) return home;
    const auto lat_it = it->find("lat");
    const auto lon_it = it->find("lon");
    if (lat_it == it->end() || lon_it == it->end()) return home;
    double lat = 0.0;
    double lon = 0.0;
    if (!parse_double(*lat_it, lat) || !parse_double(*lon_it, lon)) return home;
    if (lat < -90.0 || lat > 90.0 || lon < -180.0 || lon > 180.0) return home;
    home.lat = lat;
    home.lon = lon;
    const std::string name = util::trim(to_str(*it, "name", ""));
    const std::string region = util::trim(to_str(*it, "region", ""));
    home.name = name.empty() ? "Evim" : name;
    home.region = region.empty() ? "Kocaeli" : region;
    return home;
}

HomeLocation Settings::set_home(double lat,
                                double lon,
                                std::string name,
                                std::string region) {
    if (lat < -90.0 || lat > 90.0 || lon < -180.0 || lon > 180.0) {
        throw std::invalid_argument("Ev konumu için geçerli enlem/boylam gerekli.");
    }
    std::string n = util::trim(name);
    std::string r = util::trim(region);
    if (n.size() > 80) n.resize(80);
    if (r.size() > 80) r.resize(80);
    if (n.empty()) n = "Evim";
    if (r.empty()) r = "Kocaeli";

    HomeLocation home{n, r, lat, lon};
    data_["home_location"] = {{"name", n}, {"region", r}, {"lat", lat}, {"lon", lon}};
    write();
    return home;
}

void Settings::save(std::string api_key,
                    std::string model,
                    std::string provider,
                    std::string ollama_url,
                    std::string ollama_model,
                    std::string tokenharbor_api_key,
                    std::string tokenharbor_model,
                    std::string tokenharbor_url) {
    data_["gemini_api_key"] = util::trim(api_key);
    const std::string m = util::trim(model);
    data_["gemini_model"] = m.empty() ? std::string(kDefaultModel) : m;
    data_["ai_provider"] = is_provider(provider) ? provider : "gemini";

    std::string ou = util::trim(ollama_url);
    while (!ou.empty() && ou.back() == '/') ou.pop_back();
    data_["ollama_url"] = ou.empty() ? std::string(kDefaultOllamaUrl) : ou;

    const std::string om = util::trim(ollama_model);
    data_["ollama_model"] = om.empty() ? std::string(kDefaultOllamaModel) : om;

    data_["tokenharbor_api_key"] = util::trim(tokenharbor_api_key);
    data_["tokenharbor_model"] = util::trim(tokenharbor_model);

    std::string tu = util::trim(tokenharbor_url);
    while (!tu.empty() && tu.back() == '/') tu.pop_back();
    data_["tokenharbor_url"] = tu.empty() ? std::string(kDefaultTokenHarborUrl) : tu;
    write();
}

void Settings::set_model(std::string model) {
    const std::string value = util::trim(model);
    data_["gemini_model"] = value.empty() ? std::string(kDefaultModel) : value;
    write();
}

}  // namespace jarvis::core
