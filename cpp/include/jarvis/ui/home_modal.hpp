#pragma once

#include <jarvis/core/home.hpp>
#include <jarvis/ui/hud_modal.hpp>

#include <map>
#include <string>

class QComboBox;
class QLabel;
class QSlider;
class QTimer;

namespace jarvis::ui {

class HudButton;

// Akıllı ev paneli: ışık ve kahve simülasyonu.
class HomeModal : public HudModal {
public:
    HomeModal(QWidget* parent, jarvis::home::SmartHome& home);
    void refresh();

private:
    struct LightWidgets {
        QLabel* state = nullptr;
        QLabel* brightness = nullptr;
        QSlider* slider = nullptr;
        std::map<std::string, HudButton*> tones;
    };
    struct CoffeeWidgets {
        QLabel* state = nullptr;
        HudButton* brew = nullptr;
        QComboBox* foam = nullptr;
        QComboBox* sugar = nullptr;
        QComboBox* cups = nullptr;
    };

    QWidget* build_light_card(const std::string& key);
    QWidget* build_turk_card();
    QWidget* build_filter_card();
    void on_brightness(const std::string& key, int value);
    void toggle_light(const std::string& key);
    void set_tone(const std::string& key, const std::string& tone);
    void all_lights_off();
    void brew(const std::string& kind);
    void refresh_now();

    jarvis::home::SmartHome& home_;
    bool refreshing_ = false;
    std::map<std::string, LightWidgets> light_widgets_;
    std::map<std::string, CoffeeWidgets> coffee_widgets_;
    QTimer* timer_ = nullptr;
};

}  // namespace jarvis::ui
