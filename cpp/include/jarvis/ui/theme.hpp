#pragma once

#include <string>
#include <string_view>
#include <vector>

#include <QColor>

namespace jarvis::ui {

// Python'daki app/theme/themes.py palet sözlüklerinin birebir karşılığı.
struct Theme {
    std::string name;
    std::string label;
    bool is_light = false;
    std::string background;
    std::string background_2;
    std::string panel;
    std::string panel_2;
    std::string panel_hover;
    std::string input;
    std::string red;
    std::string red_bright;
    std::string red_soft;
    std::string red_dim;
    std::string red_glow;
    std::string gold;
    std::string gold_dim;
    std::string text;
    std::string text_soft;
    std::string muted;
    std::string dim;
    std::string faint;
    std::string border;
    std::string border_red;
    std::string grid;
    std::string font{"Segoe UI"};
    std::string mono{"Consolas"};
    int window_radius = 18;
};

// Kanonik 5 temadan birini döner; bilinmeyen isim -> "black".
// İsim Python normalize_theme ile uyumludur ("siyah", "gece", "beyaz" vb. takma adlar).
const Theme& theme_palette(std::string_view name);

// Şu anki tema. İlk çağrıda settings.json'dan okunur ve önbelleklenir
// (Python'daki `from app.theme import ultron as U` karşılığı).
const Theme& current_theme();

// Temayı çalışma zamanında değiştirir (Ayarlar → Temalar için).
void set_current_theme(std::string_view name);

// Kanonik temaların sıralı ad listesi (Ayarlar → Temalar sekmesi için).
std::vector<std::string> theme_order();

// "#RRGGBB" (ve "#RGB") biçimini QColor'a çevirir; geçersizse siyah döner.
QColor parse_color(std::string_view hex);

// "#RRGGBB" rengini rgba(r,g,b,a) CSS'ine çevirir (Python rgba_css karşılığı).
std::string rgba_css(std::string_view color, double alpha);

}  // namespace jarvis::ui
