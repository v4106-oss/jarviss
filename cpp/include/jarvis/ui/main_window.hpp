#pragma once

#include <optional>
#include <string>
#include <vector>

#include <QThread>
#include <QString>
#include <QWidget>

#include <jarvis/core/home.hpp>
#include <jarvis/core/settings.hpp>

class QLabel;
class QLineEdit;
class QPushButton;

namespace jarvis::ui {

class TitleBar;
class Sidebar;
class OrbWidget;
class HudChat;
class ChatEntry;
class FloatingHud;
class NewsStage;
class BrowserModal;
class AgentRunner;
class MicService;
class SpeechService;
struct NewsItem;

// Uygulamanın ana penceresi; Python app/ui/main_window.py iskeletinin karşılığı.
class MainWindow : public QWidget {
public:
    explicit MainWindow(QWidget* parent = nullptr);
    ~MainWindow() override;

    void set_orb_state(const std::string& state);
    void set_orb_input_level(double level);

    void append_user_message(const QString& text);
    void append_assistant_message(const QString& text);
    void clear_chat();

    void show_news_items(const std::vector<NewsItem>& items);

protected:
    void paintEvent(QPaintEvent* event) override;
    void resizeEvent(QResizeEvent* event) override;
    void changeEvent(QEvent* event) override;
    void closeEvent(QCloseEvent* event) override;

private:
    void update_mask();
    void send_current_text();
    void show_settings();
    void show_browser();
    void open_in_browser(const QString& url);
    void show_weather();
    void show_camera();
    void show_book();
    void show_home();
    void show_diagnostics();
    void connect_agent();
    void connect_speech();

    // --- mikrofon sesli sohbet (Python _start_listening/_stop_voice_session) ---
    void ensure_mic_service();
    void connect_mic();
    void toggle_mic();
    void start_listening();
    void stop_voice_session();
    void apply_mic_style();
    // Ayarlar kapandığında ses/mikrofon hattını yeniden kurar.
    void apply_audio_settings();

    TitleBar* titlebar_ = nullptr;
    Sidebar* sidebar_ = nullptr;
    OrbWidget* orb_ = nullptr;
    HudChat* chat_ = nullptr;
    QLineEdit* composer_ = nullptr;
    QPushButton* stop_button_ = nullptr;
    QPushButton* mic_button_ = nullptr;
    QLabel* mic_status_ = nullptr;
    FloatingHud* hud_ = nullptr;
    NewsStage* news_ = nullptr;
    BrowserModal* browser_ = nullptr;
    AgentRunner* runner_ = nullptr;
    SpeechService* speech_ = nullptr;
    // Mikrofon tek bir iş parçacığında yaşar; oturumlar arası yeniden kullanılır.
    QThread mic_thread_;
    MicService* mic_ = nullptr;
    bool mic_enabled_ = false;
    bool closing_ = false;
    std::string mic_device_active_;
    // Koşu sırasındaki canlı baloncuk ve onun birikmiş metni.
    ChatEntry* pending_ = nullptr;
    std::string streamed_;
    // Bu sorunun sesli yanıt sınırı; null olunca tüm yanıt okunur.
    std::optional<int> voice_limit_;
    jarvis::core::Settings settings_;
    jarvis::home::SmartHome home_;
};

}  // namespace jarvis::ui
