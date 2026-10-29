#pragma once

#include <QPoint>
#include <QWidget>

#include <functional>
#include <string>

namespace jarvis::core {
class Settings;
}

class QColor;
class QContextMenuEvent;
class QEnterEvent;
class QHideEvent;
class QMouseEvent;
class QPaintEvent;
class QPainter;
class QPushButton;
class QRectF;
class QShowEvent;
class QTimer;

namespace jarvis::ui {

// Masaüstünde kalan kompakt, çerçevesiz Jarvis HUD'ı.
// Python app/ui/floating_hud.py FloatingHud karşılığı.
class FloatingHud : public QWidget {
public:
    explicit FloatingHud(jarvis::core::Settings* settings = nullptr);

    const std::string& state() const { return state_; }

    void set_state(const std::string& state);
    void set_subtitle(const std::string& text, const std::string& sender = "JARVIS");
    void set_camera(const std::string& text = "", int count = 0, bool active = false);
    void set_mic_enabled(bool enabled);
    void set_mic_level(double level);

    void show_for_minimize();
    void hide_for_restore();
    void dismiss();
    void reposition();
    void reset_position();

    std::function<void()> on_restore_requested;

protected:
    void paintEvent(QPaintEvent* event) override;
    void showEvent(QShowEvent* event) override;
    void hideEvent(QHideEvent* event) override;
    void enterEvent(QEnterEvent* event) override;
    void leaveEvent(QEvent* event) override;
    void mousePressEvent(QMouseEvent* event) override;
    void mouseMoveEvent(QMouseEvent* event) override;
    void mouseReleaseEvent(QMouseEvent* event) override;
    void mouseDoubleClickEvent(QMouseEvent* event) override;
    void contextMenuEvent(QContextMenuEvent* event) override;

private:
    void tick();
    void sync_timer();
    int interval() const;
    void move_to_default();
    void ensure_on_screen();
    void snap_to_screen();
    void save_position();

    void paint_surface(QPainter& painter, const QRectF& card, const QColor& color);
    void paint_sweep(QPainter& painter, const QRectF& card, const QColor& color);
    void paint_corner_ticks(QPainter& painter, const QRectF& card, const QColor& color);
    void paint_orb(QPainter& painter, const QRectF& card, const QColor& color);
    void paint_header(QPainter& painter, const QRectF& card, const QColor& color);
    void paint_subtitle(QPainter& painter, const QColor& color);
    void paint_divider(QPainter& painter, const QColor& color);
    void paint_status_chips(QPainter& painter);
    void paint_waveform(QPainter& painter, const QColor& color);
    void paint_footer(QPainter& painter);

    jarvis::core::Settings* settings_ = nullptr;
    std::string state_{"idle"};
    std::string subtitle_;
    std::string subtitle_sender_{"JARVIS"};
    std::string camera_text_;
    int camera_count_ = 0;
    bool camera_active_ = false;
    bool mic_enabled_ = false;
    double mic_level_ = 0.0;
    double phase_ = 0.0;
    double pulse_ = 0.0;
    double spin_ = 0.0;
    bool hover_ = false;
    bool dragging_ = false;
    bool moved_ = false;
    bool animating_ = true;
    bool position_checked_ = false;
    QPoint drag_offset_;
    QPoint press_pos_;
    QTimer* timer_ = nullptr;
    QPushButton* close_button_ = nullptr;
};

}  // namespace jarvis::ui
