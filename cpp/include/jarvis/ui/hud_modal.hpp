#pragma once

#include <QFrame>
#include <QVBoxLayout>
#include <QWidget>

#include <functional>
#include <string>

class QGraphicsOpacityEffect;
class QLabel;
class QParallelAnimationGroup;
class QPropertyAnimation;
class QPushButton;
class QScrollArea;
class QVariantAnimation;

namespace jarvis::ui {

// Ana pencerenin üstünü karartan, kartı ortada tutan modal taban sınıfı.
class HudModal : public QWidget {
public:
    HudModal(QWidget* parent, const std::string& title, const std::string& subtitle = "",
             int width = 820, int preferred_height = 0, int padding = 26, bool fade = true,
             bool scrollable = true, bool persistent = false);

    void set_subtitle(const std::string& text);
    void close_modal();
    void reopen();

    // Python'daki `closed` sinyalinin karşılığı.
    std::function<void()> on_closed;
    std::function<void()> on_open_settings_requested;

    QFrame* card_ = nullptr;
    QWidget* body_ = nullptr;
    QVBoxLayout* content_ = nullptr;

protected:
    void showEvent(QShowEvent* event) override;
    void resizeEvent(QResizeEvent* event) override;
    void paintEvent(QPaintEvent* event) override;
    void mousePressEvent(QMouseEvent* event) override;
    void keyPressEvent(QKeyEvent* event) override;
    void closeEvent(QCloseEvent* event) override;

private:
    class Veil;
    void apply_geometry();
    void set_backdrop(double value);
    void set_entrance_offset(double value);
    void start_entrance();
    void start_backdrop_enter();
    void set_veil(double value);
    void start_veil_enter();
    void finish_close_animation();
    static void stop_animation(QObject* animation);
    int headerHeightHint() const;

    int preferred_width_;
    int preferred_height_;
    int padding_;
    bool closed_ = false;
    bool persistent_ = false;
    double backdrop_ = 1.0;
    double entrance_value_ = 0.0;
    double veil_alpha_ = 0.0;
    bool skip_backdrop_fade_ = false;

    QVBoxLayout* outer_layout_ = nullptr;
    QLabel* title_label_ = nullptr;
    QLabel* subtitle_label_ = nullptr;
    QPushButton* close_button_ = nullptr;
    QScrollArea* scroll_ = nullptr;
    QGraphicsOpacityEffect* effect_ = nullptr;
    QPropertyAnimation* fade_animation_ = nullptr;
    QVariantAnimation* backdrop_animation_ = nullptr;
    QParallelAnimationGroup* close_animation_ = nullptr;
    QVariantAnimation* entrance_animation_ = nullptr;
    QVariantAnimation* veil_animation_ = nullptr;
    Veil* veil_ = nullptr;
    qint64 opened_at_ = 0;
};

// İç kart yardımcısı.
QFrame* hud_card(int width = 0, int height = 0, int padding = 12);

// İstatistik kartı yardımcısı.
QFrame* stat_card(const std::string& title, const std::string& value,
                  const std::string& note = "", double percent = -1.0);

}  // namespace jarvis::ui
