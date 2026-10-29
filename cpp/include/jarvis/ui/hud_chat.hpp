#pragma once

#include <QString>
#include <QWidget>

#include <functional>
#include <string>
#include <unordered_map>

class QLabel;
class QScrollArea;
class QTimer;
class QVBoxLayout;

namespace jarvis::ui {

class ChatEntry;
class GlyphButton;

// Python app/ui/hud_chat.py render_markdown() karşılığı: AI yanıtını QLabel'in
// anladığı güvenli zengin metne çevirir.
std::string render_markdown(const std::string& text);

// Tek bir mesaj baloncuğu; akış sırasında hafif, bitince zengin metin.
class ChatEntry : public QWidget {
public:
    ChatEntry(const std::string& text, bool is_user, QWidget* parent = nullptr);

    void set_text(const std::string& text, bool rich = false);
    void set_sender(const std::string& text);
    void set_streaming(bool streaming);
    void set_expanded(bool expanded);
    bool is_streaming() const { return streaming_; }

protected:
    void enterEvent(QEnterEvent* event) override;
    void leaveEvent(QEvent* event) override;

private:
    void refresh_text();
    void blink();
    void copy_to_clipboard();

    bool is_user_ = false;
    std::string raw_;
    bool rich_ = false;
    bool streaming_ = false;
    bool caret_on_ = true;
    bool expanded_ = false;
    QWidget* avatar_ = nullptr;
    QWidget* bubble_ = nullptr;
    QLabel* sender_ = nullptr;
    QLabel* label_ = nullptr;
    GlyphButton* copy_button_ = nullptr;
    QTimer* caret_timer_ = nullptr;
};

// Kaydırılabilir sohbet günlüğü; baloncuklar, gün ayraçları ve boş durum önerileri.
class HudChat : public QWidget {
public:
    explicit HudChat(QWidget* parent = nullptr);

    ChatEntry* add_message(const std::string& text, bool is_user = false);
    void update_message(ChatEntry* entry, const std::string& text, bool rich = false);
    void clear();
    int count() const;
    void set_expanded(bool expanded);
    void scroll_to_bottom();

    std::function<void(const QString&)> on_suggested;

private:
    void flush_pending();
    void insert_before_stretch(QWidget* widget);

    QScrollArea* scroll_ = nullptr;
    QWidget* container_ = nullptr;
    QVBoxLayout* layout_ = nullptr;
    QWidget* empty_ = nullptr;
    bool expanded_ = false;
    std::string last_day_;
    std::unordered_map<ChatEntry*, std::string> pending_;
    QTimer* flush_timer_ = nullptr;
};

}  // namespace jarvis::ui
