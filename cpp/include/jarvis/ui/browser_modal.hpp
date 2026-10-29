#pragma once

#include <jarvis/ui/hud_modal.hpp>

#include <functional>
#include <string>

class QLabel;
class QLineEdit;
class QWebEngineView;

namespace jarvis::ui {

class HudButton;

// Uygulama içi tarayıcı; harita ile aynı tek QWebEngineProfile'ı kullanır.
class BrowserModal : public HudModal {
public:
    BrowserModal(QWidget* parent, int width = 1180, int height = 760);

    void open_url(const std::string& raw);
    void focus_url();
    static std::string normalize_url(const std::string& raw);

    std::function<void()> on_map_requested;

private:
    void navigate();
    void on_title(const QString& title);
    void on_url(const QUrl& url);
    void on_load_finished(bool ok);
    void update_nav();

    QWebEngineView* view_ = nullptr;
    QLineEdit* address_ = nullptr;
    QLabel* status_label_ = nullptr;
    HudButton* back_button_ = nullptr;
    HudButton* forward_button_ = nullptr;
};

}  // namespace jarvis::ui
