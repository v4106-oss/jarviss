#pragma once

#include <jarvis/ui/hud_modal.hpp>
#include <jarvis/ui/hud_widgets.hpp>

#include <string>
#include <vector>

namespace jarvis::ui {

// Hava durumu görünümü için ekran veri modeli.
struct DayForecast {
    std::string label;
    std::string condition = "cloud";
    double low = 12.0;
    double high = 18.0;
};

struct WeatherData {
    std::string location = "Derince";
    std::string region = "Kocaeli";
    std::string condition = "cloud";
    std::string condition_label = "Parçalı bulutlu";
    double temp = 16.0;
    double feels_like = 15.0;
    double low = 13.0;
    double high = 18.0;
    int humidity = 62;
    double wind = 12.0;
    int uv = 3;
    double rain = 0.4;
    std::vector<HourPoint> hourly;
    std::vector<DayForecast> days;
};

// Hava durumu paneli; Python app/ui/hud_weather.py karşılığı (odaklı alt küme).
// Veri, ajan servisinden doldurulur; bu widget yalnızca görünümü çizer.
class WeatherModal : public HudModal {
public:
    WeatherModal(QWidget* parent, const WeatherData& data);

private:
    WeatherData data_;
};

}  // namespace jarvis::ui
