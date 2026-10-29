#pragma once

// Akıllı ev simülasyonu: ışıklar ve kahve makineleri; bulut veya ağ kullanmaz.
// Python app/core/home_tools.py dosyasının C++20 karşılığı.

#include <functional>
#include <map>
#include <optional>
#include <string>

#include <nlohmann/json.hpp>

namespace jarvis::home {

inline constexpr int kTurkBrewSeconds = 75;
inline constexpr int kFilterSecondsPerCup = 40;

// Türkçe metni aksansız, küçük harfli ve yalnızca [a-z0-9%] karakterli hâle getirir.
std::string fold_turkish(const std::string& text);

// Python'daki TONES/FOAMS/SUGARS sözlüklerinin anahtarları. Araç argümanları
// bunlara karşı doğrulanır; bilinmeyen değer ışık/kahve ayarını değiştirmez.
bool is_tone_key(const std::string& value);
bool is_foam_key(const std::string& value);
bool is_sugar_key(const std::string& value);

struct Light {
    std::string key;
    std::string name;
    bool on = false;
    int brightness = 70;
    std::string tone = "notr";

    nlohmann::json snapshot() const;
    std::string describe() const;
};

struct Coffee {
    std::string kind;
    int cups = 1;
    std::string foam = "orta";
    std::string sugar = "orta";
    std::optional<double> started_at;
    std::optional<double> ready_at;
    bool ready = false;
    std::optional<double> brewed_at;
    bool keep_warm = true;

    std::string name() const;
    nlohmann::json snapshot(double now) const;
    std::string describe(double now) const;
};

class SmartHome {
public:
    using Clock = std::function<double()>;

    explicit SmartHome(Clock clock = nullptr);

    double now() const;
    Light& light(const std::string& key);
    Light& set_light(const std::string& key,
                     std::optional<bool> on = std::nullopt,
                     std::optional<int> brightness = std::nullopt,
                     std::optional<std::string> tone = std::nullopt);
    std::vector<Light> set_all_lights(std::optional<bool> on = std::nullopt,
                                      std::optional<int> brightness = std::nullopt,
                                      std::optional<std::string> tone = std::nullopt);

    // Dönen üçlü: (başladı mı, kahve, saniye cinsinden süre).
    std::tuple<bool, Coffee, double> brew(const std::string& kind,
                                          std::optional<int> cups = std::nullopt,
                                          std::optional<std::string> foam = std::nullopt,
                                          std::optional<std::string> sugar = std::nullopt);
    Coffee coffee(const std::string& kind);
    Coffee cancel_coffee(const std::string& kind);
    nlohmann::json snapshot() const;

private:
    Clock clock_;
    std::map<std::string, Light> lights_;
    std::map<std::string, Coffee> coffees_;

    double now_() const;
    void advance(Coffee& coffee, double now);
};

// Akıllı ev sesli komutlarını yerel sınıflandırır; belirsiz metinde nullopt döner.
// Dönen JSON Python intent sözlüğüyle birebir uyumludur ({"action": "...", ...}).
std::optional<nlohmann::json> home_voice_intent(const std::string& text);

}  // namespace jarvis::home
