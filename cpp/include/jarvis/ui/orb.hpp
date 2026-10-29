#pragma once

#include <string>
#include <utility>
#include <vector>

#include <QColor>
#include <QPointF>
#include <QRectF>
#include <QTimer>
#include <QWidget>

class QPainter;

namespace jarvis::ui {

// Duruma göre vurgu rengi; Python orb.py state_color() karşılığı.
QColor state_color(const std::string& state, bool secondary = false);

// Ortadaki cam küre; arayüzün tek fütüristik öğesi (Python orb.py OrbWidget).
class OrbWidget : public QWidget {
public:
    explicit OrbWidget(QWidget* parent = nullptr);

    void set_animating(bool enabled);
    void set_state(const std::string& state);
    void set_input_level(double level);

    const std::string& state() const { return state_; }

protected:
    void paintEvent(QPaintEvent* event) override;
    void hideEvent(QHideEvent* event) override;
    void showEvent(QShowEvent* event) override;

private:
    struct Sparkle {
        double angle;
        double distance;
        double size;
        double brightness;
        double offset;
    };

    int interval() const;
    void tick();
    std::pair<QColor, QColor> accents() const;

    void paint_glow(QPainter& p, const QPointF& center, double radius,
                    const QColor& accent, double breath);
    void paint_orbit(QPainter& p, const QPointF& center, double radius,
                     const QColor& accent, const QColor& secondary);
    void paint_level_ring(QPainter& p, const QPointF& center, double radius,
                          const QColor& accent);
    void paint_sphere(QPainter& p, const QRectF& sphere, const QColor& primary,
                      const QColor& secondary, double breath);
    void paint_burst(QPainter& p, const QPointF& center, double radius);

    std::string state_{"idle"};
    std::string status_{"Hazır"};
    double phase_{0.0};
    double spin_{0.0};
    double burst_{0.0};
    QColor burst_color_;
    double input_level_{0.0};
    bool animating_{true};
    std::vector<Sparkle> sparkles_;
    QTimer timer_;
};

}  // namespace jarvis::ui
