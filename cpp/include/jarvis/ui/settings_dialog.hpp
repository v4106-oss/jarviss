#pragma once

#include <QFrame>
#include <QWidget>

#include <functional>
#include <string>
#include <vector>

#include <jarvis/ui/hud_modal.hpp>
#include <jarvis/ui/theme.hpp>

class QComboBox;
class QLineEdit;
class QTabWidget;

namespace jarvis::core {
class Settings;
}

namespace jarvis::ui {

// Seçilebilir tema kartı; mini arayüzü doğrudan çizer
// (Python app/ui/settings_dialog.py ThemePreview karşılığı).
class ThemePreview : public QFrame {
public:
    ThemePreview(const Theme& theme, bool selected, QWidget* parent = nullptr);

    std::string key() const { return theme_->name; }
    void set_selected(bool selected);
    std::function<void()> onClick;

protected:
    void enterEvent(QEnterEvent* event) override;
    void leaveEvent(QEvent* event) override;
    void mouseReleaseEvent(QMouseEvent* event) override;
    void paintEvent(QPaintEvent* event) override;

private:
    const Theme* theme_ = nullptr;
    bool selected_ = false;
    bool hover_ = false;
};

// Ayarlar penceresi: tema seçici + bağlantı sekmesi.
// Python app/ui/settings_dialog.py SettingsDialog karşılığı (odaklı alt küme).
class SettingsDialog : public HudModal {
public:
    SettingsDialog(QWidget* parent, jarvis::core::Settings& settings);

    bool theme_changed() const { return theme_changed_; }
    std::function<void()> on_theme_changed;

private:
    void build_themes_tab(QTabWidget* tabs);
    void build_connection_tab(QTabWidget* tabs, jarvis::core::Settings& settings);
    void apply_theme(const std::string& key, jarvis::core::Settings& settings);
    void save_connection(jarvis::core::Settings& settings);

    jarvis::core::Settings* settings_ = nullptr;
    std::string selected_theme_;
    bool theme_changed_ = false;
    std::vector<ThemePreview*> theme_cards_;
    QComboBox* provider_ = nullptr;
    QLineEdit* api_key_ = nullptr;
    QLineEdit* model_ = nullptr;
    QLineEdit* ollama_url_ = nullptr;
    QLineEdit* ollama_model_ = nullptr;
    QLineEdit* tokenharbor_key_ = nullptr;
    QLineEdit* tokenharbor_model_ = nullptr;
};

}  // namespace jarvis::ui
