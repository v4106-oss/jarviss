#pragma once

#include <QWidget>

class QPushButton;

namespace jarvis::ui {

// Sol kenar çubuğu; Python app/ui/sidebar.py karşılığı.
class Sidebar : public QWidget {
public:
    explicit Sidebar(QWidget* parent = nullptr);

    QPushButton* new_chat_button() const { return new_chat_; }
    QPushButton* settings_button() const { return settings_; }
    QPushButton* browser_button() const { return browser_; }
    QPushButton* weather_button() const { return weather_; }
    QPushButton* camera_button() const { return camera_; }
    QPushButton* book_button() const { return book_; }
    QPushButton* home_button() const { return home_; }
    QPushButton* diagnostics_button() const { return diagnostics_; }

private:
    QPushButton* new_chat_ = nullptr;
    QPushButton* settings_ = nullptr;
    QPushButton* browser_ = nullptr;
    QPushButton* weather_ = nullptr;
    QPushButton* camera_ = nullptr;
    QPushButton* book_ = nullptr;
    QPushButton* home_ = nullptr;
    QPushButton* diagnostics_ = nullptr;
};

}  // namespace jarvis::ui
