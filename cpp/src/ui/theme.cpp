#include <jarvis/ui/theme.hpp>

#include <cctype>
#include <mutex>
#include <optional>
#include <vector>

#include <jarvis/core/settings.hpp>
#include <jarvis/core/util.hpp>

namespace jarvis::ui {

namespace {

Theme make_black() {
    Theme t;
    t.name = "black";
    t.label = "Grafit / Sade";
    t.is_light = false;
    t.background = "#0c0c0d";
    t.background_2 = "#111112";
    t.panel = "#151516";
    t.panel_2 = "#1b1b1d";
    t.panel_hover = "#222224";
    t.input = "#18181a";
    t.red = "#d8c7ac";
    t.red_bright = "#f0e6d2";
    t.red_soft = "#9c8c74";
    t.red_dim = "#574e40";
    t.red_glow = "#fff3dd";
    t.gold = "#c9a97f";
    t.gold_dim = "#6a5943";
    t.text = "#f4f2ee";
    t.text_soft = "#d3d0c9";
    t.muted = "#a29e96";
    t.dim = "#79756d";
    t.faint = "#4c4943";
    t.border = "#262624";
    t.border_red = "#3b352b";
    t.grid = "#111112";
    return t;
}

Theme make_midnight() {
    Theme t;
    t.name = "midnight";
    t.label = "Gece / Sakin";
    t.is_light = false;
    t.background = "#0c0e11";
    t.background_2 = "#101318";
    t.panel = "#151920";
    t.panel_2 = "#1b2028";
    t.panel_hover = "#232a34";
    t.input = "#171c23";
    t.red = "#a9c1d6";
    t.red_bright = "#d3e2f0";
    t.red_soft = "#738ca3";
    t.red_dim = "#3b4c5c";
    t.red_glow = "#e6f2fb";
    t.gold = "#c6b892";
    t.gold_dim = "#625a46";
    t.text = "#f1f4f8";
    t.text_soft = "#cfd6de";
    t.muted = "#98a2ae";
    t.dim = "#6e7885";
    t.faint = "#454c57";
    t.border = "#242a33";
    t.border_red = "#33404e";
    t.grid = "#10141a";
    return t;
}

Theme make_ember() {
    Theme t;
    t.name = "ember";
    t.label = "Kum / Sıcak";
    t.is_light = false;
    t.background = "#0e0c0a";
    t.background_2 = "#12100d";
    t.panel = "#181512";
    t.panel_2 = "#1e1a16";
    t.panel_hover = "#26211c";
    t.input = "#1a1613";
    t.red = "#c9a27a";
    t.red_bright = "#e8c8a3";
    t.red_soft = "#8f6f50";
    t.red_dim = "#4c3a29";
    t.red_glow = "#f1d8b8";
    t.gold = "#bd9e6d";
    t.gold_dim = "#5f4e35";
    t.text = "#f5f0e9";
    t.text_soft = "#d9cfc2";
    t.muted = "#a99c8b";
    t.dim = "#7d7264";
    t.faint = "#4e463c";
    t.border = "#2b2520";
    t.border_red = "#443629";
    t.grid = "#14110e";
    return t;
}

Theme make_forest() {
    Theme t;
    t.name = "forest";
    t.label = "Adaçayı / Doğal";
    t.is_light = false;
    t.background = "#0b0d0c";
    t.background_2 = "#0f1211";
    t.panel = "#141816";
    t.panel_2 = "#191e1b";
    t.panel_hover = "#212722";
    t.input = "#161b18";
    t.red = "#a3bfae";
    t.red_bright = "#cbe2d5";
    t.red_soft = "#718c7b";
    t.red_dim = "#3b4a40";
    t.red_glow = "#e3f2e9";
    t.gold = "#c2b58f";
    t.gold_dim = "#5f5841";
    t.text = "#f1f5f2";
    t.text_soft = "#cfdad2";
    t.muted = "#98a69c";
    t.dim = "#6f7b72";
    t.faint = "#454f48";
    t.border = "#242a26";
    t.border_red = "#33453a";
    t.grid = "#101412";
    return t;
}

Theme make_white() {
    Theme t;
    t.name = "white";
    t.label = "Aydınlık / Temiz";
    t.is_light = true;
    t.background = "#f4f4f1";
    t.background_2 = "#ecece8";
    t.panel = "#ffffff";
    t.panel_2 = "#f7f7f4";
    t.panel_hover = "#efefea";
    t.input = "#ffffff";
    t.red = "#2c2c2e";
    t.red_bright = "#111113";
    t.red_soft = "#6b6b6e";
    t.red_dim = "#d9d9d4";
    t.red_glow = "#4a4a4d";
    t.gold = "#8c7a5b";
    t.gold_dim = "#cfc5b2";
    t.text = "#17171a";
    t.text_soft = "#3c3c40";
    t.muted = "#6e6e73";
    t.dim = "#96969b";
    t.faint = "#c7c7c2";
    t.border = "#e1e1dc";
    t.border_red = "#d1d1cb";
    t.grid = "#eaeae5";
    return t;
}

std::optional<Theme> g_current;

}  // namespace

const Theme& theme_palette(std::string_view name) {
    const std::string key = jarvis::util::normalize_theme(name);
    if (key == "midnight") {
        static const Theme t = make_midnight();
        return t;
    }
    if (key == "ember") {
        static const Theme t = make_ember();
        return t;
    }
    if (key == "forest") {
        static const Theme t = make_forest();
        return t;
    }
    if (key == "white") {
        static const Theme t = make_white();
        return t;
    }
    static const Theme t = make_black();
    return t;
}

const Theme& current_theme() {
    static std::mutex mutex;
    std::lock_guard<std::mutex> lock(mutex);
    if (!g_current.has_value()) {
        jarvis::core::Settings settings;
        g_current = theme_palette(settings.theme());
    }
    return *g_current;
}

void set_current_theme(std::string_view name) {
    static std::mutex mutex;
    std::lock_guard<std::mutex> lock(mutex);
    g_current = theme_palette(name);
}

std::vector<std::string> theme_order() {
    return {"black", "midnight", "ember", "forest", "white"};
}

QColor parse_color(std::string_view hex) {
    std::string value;
    for (char c : hex) {
        if (c == '#') {
            continue;
        }
        if (std::isspace(static_cast<unsigned char>(c))) {
            continue;
        }
        value += c;
    }
    if (value.size() == 3) {
        std::string expanded;
        expanded.reserve(6);
        for (char c : value) {
            expanded += c;
            expanded += c;
        }
        value = expanded;
    }
    auto nibble = [](char c) -> int {
        if (c >= '0' && c <= '9') return c - '0';
        if (c >= 'a' && c <= 'f') return c - 'a' + 10;
        if (c >= 'A' && c <= 'F') return c - 'A' + 10;
        return -1;
    };
    if (value.size() != 6) {
        return QColor(0, 0, 0);
    }
    int r = nibble(value[0]) * 16 + nibble(value[1]);
    int g = nibble(value[2]) * 16 + nibble(value[3]);
    int b = nibble(value[4]) * 16 + nibble(value[5]);
    if (r < 0 || g < 0 || b < 0) {
        return QColor(0, 0, 0);
    }
    return QColor(r, g, b);
}

std::string rgba_css(std::string_view color, double alpha) {
    const QColor c = parse_color(color);
    if (alpha < 0.0) alpha = 0.0;
    if (alpha > 1.0) alpha = 1.0;
    return "rgba(" + std::to_string(c.red()) + ", " + std::to_string(c.green()) + ", " +
           std::to_string(c.blue()) + ", " + std::to_string(alpha) + ")";
}

}  // namespace jarvis::ui
