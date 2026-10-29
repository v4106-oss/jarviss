#include <jarvis/ui/orb.hpp>

#include <algorithm>
#include <cmath>
#include <random>

#include <QPainter>
#include <QPainterPath>
#include <QPen>
#include <QRadialGradient>

#include <jarvis/ui/theme.hpp>

namespace jarvis::ui {

namespace {

constexpr double kTau = 6.28318530717958647692;
constexpr double kPi = 3.14159265358979323846;

struct Accent {
    const char* primary;
    const char* secondary;
};

const Accent& accent_for(const std::string& state, bool light) {
    // Python STATE_ACCENTS (koyu) / LIGHT_STATE_ACCENTS (açık).
    static const Accent dark_idle{"#a06a78", "#c98d9b"};
    static const Accent dark_listening{"#2fd6c3", "#8ff7ec"};
    static const Accent dark_thinking{"#ffc32e", "#ffe08a"};
    static const Accent dark_speaking{"#ff6b5b", "#ffb0a6"};
    static const Accent light_idle{"#8f5a67", "#b87f8c"};
    static const Accent light_listening{"#0e9d8f", "#3fc4b6"};
    static const Accent light_thinking{"#c08a00", "#d9a520"};
    static const Accent light_speaking{"#c94a3c", "#dd7568"};

    if (light) {
        if (state == "listening") return light_listening;
        if (state == "thinking") return light_thinking;
        if (state == "speaking") return light_speaking;
        return light_idle;
    }
    if (state == "listening") return dark_listening;
    if (state == "thinking") return dark_thinking;
    if (state == "speaking") return dark_speaking;
    return dark_idle;
}

QColor with_alpha(const QColor& color, int alpha) {
    QColor copy = color;
    copy.setAlpha(std::clamp(alpha, 0, 255));
    return copy;
}

QColor mix_colors(const QColor& a, const QColor& b, double t) {
    t = std::clamp(t, 0.0, 1.0);
    return QColor(static_cast<int>(std::lround(a.red() + (b.red() - a.red()) * t)),
                  static_cast<int>(std::lround(a.green() + (b.green() - a.green()) * t)),
                  static_cast<int>(std::lround(a.blue() + (b.blue() - a.blue()) * t)));
}

}  // namespace

QColor state_color(const std::string& state, bool secondary) {
    const Accent& a = accent_for(state, current_theme().is_light);
    return parse_color(secondary ? a.secondary : a.primary);
}

OrbWidget::OrbWidget(QWidget* parent) : QWidget(parent) {
    setMinimumSize(220, 220);
    setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Expanding);
    burst_color_ = state_color("idle");

    std::mt19937 generator(29);
    std::uniform_real_distribution<double> angle(0.0, kTau);
    std::uniform_real_distribution<double> distance(0.12, 0.88);
    std::uniform_real_distribution<double> size(0.6, 1.7);
    std::uniform_real_distribution<double> brightness(0.25, 1.0);
    std::uniform_real_distribution<double> offset(0.0, kTau);
    sparkles_.reserve(14);
    for (int i = 0; i < 14; ++i) {
        sparkles_.push_back(Sparkle{angle(generator), distance(generator), size(generator),
                                    brightness(generator), offset(generator)});
    }

    connect(&timer_, &QTimer::timeout, this, [this] { tick(); });
    timer_.start(interval());
}

int OrbWidget::interval() const {
    return (state_ == "thinking" || state_ == "listening" || state_ == "speaking") ? 33 : 66;
}

void OrbWidget::set_animating(bool enabled) {
    animating_ = enabled;
    if (animating_ && isVisible()) {
        timer_.start(interval());
    } else {
        timer_.stop();
    }
}

void OrbWidget::hideEvent(QHideEvent* event) {
    timer_.stop();
    QWidget::hideEvent(event);
}

void OrbWidget::showEvent(QShowEvent* event) {
    QWidget::showEvent(event);
    if (animating_) {
        timer_.start(interval());
    }
}

void OrbWidget::set_state(const std::string& state) {
    const bool known = (state == "idle" || state == "listening" || state == "thinking" ||
                        state == "speaking");
    if (!known) {
        return;
    }
    if (state != state_) {
        burst_ = 1.0;
        burst_color_ = state_color(state);
        if (state != "listening") {
            input_level_ = 0.0;
        }
        update();
    }
    state_ = state;
    if (animating_ && isVisible()) {
        timer_.start(interval());
    }
    update();
}

void OrbWidget::set_input_level(double level) {
    double value = std::clamp(level, 0.0, 1.0);
    const double rate = (value > input_level_) ? 0.62 : 0.24;
    input_level_ += (value - input_level_) * rate;
    update();
}

std::pair<QColor, QColor> OrbWidget::accents() const {
    return {state_color(state_), state_color(state_, true)};
}

void OrbWidget::tick() {
    double speed = (state_ == "idle") ? 0.007 : 0.02;
    if (state_ == "thinking") {
        speed = 0.028;
    }
    phase_ = std::fmod(phase_ + speed, kTau);
    const double spin_speed = (state_ == "thinking" || state_ == "listening") ? 0.05 : 0.028;
    spin_ = std::fmod(spin_ + spin_speed, kTau);
    if (burst_ > 0.0) {
        burst_ = std::max(0.0, burst_ - (interval() / 640.0));
    }
    update();
}

void OrbWidget::paintEvent(QPaintEvent* event) {
    Q_UNUSED(event);
    const double width = this->width();
    const double height = this->height();
    if (width <= 0 || height <= 0) {
        return;
    }

    QPainter painter(this);
    painter.setRenderHint(QPainter::Antialiasing, true);
    const QPointF center(width / 2.0, height / 2.0);
    const double base = std::min(width, height);
    const double breath = 0.5 + 0.5 * std::sin(phase_ * 1.5);
    const double level = (state_ == "listening") ? input_level_ : 0.0;

    double radius = base * 0.30;
    if (state_ == "listening" || state_ == "speaking") {
        radius *= 1.0 + 0.02 * breath;
    } else {
        radius *= 1.0 + 0.008 * breath;
    }
    radius *= 1.0 + 0.15 * level;

    const auto [primary, secondary] = accents();
    paint_glow(painter, center, radius, primary, breath);
    paint_orbit(painter, center, radius, primary, secondary);
    paint_level_ring(painter, center, radius, secondary);
    const QRectF sphere(center.x() - radius, center.y() - radius, radius * 2.0, radius * 2.0);
    paint_sphere(painter, sphere, primary, secondary, breath);
    paint_burst(painter, center, radius);
}

void OrbWidget::paint_glow(QPainter& painter, const QPointF& center, double radius,
                           const QColor& accent, double breath) {
    double strength = 1.0;
    if (state_ == "listening") strength = 1.75;
    else if (state_ == "thinking") strength = 1.45;
    else if (state_ == "speaking") strength = 1.6;

    const double level = (state_ == "listening") ? input_level_ : 0.0;
    strength *= 1.0 + 2.0 * level;
    const double reach = radius * (1.55 + 0.1 * breath);

    QRadialGradient halo(center, reach);
    const int base_alpha = current_theme().is_light ? 24 : 46;
    halo.setColorAt(0.0, with_alpha(accent, std::min(200, static_cast<int>(base_alpha * strength))));
    halo.setColorAt(0.5, with_alpha(accent, std::min(120, static_cast<int>(base_alpha * 0.42 * strength))));
    halo.setColorAt(1.0, with_alpha(accent, 0));
    painter.setPen(Qt::NoPen);
    painter.setBrush(halo);
    painter.drawEllipse(center, reach, reach);
}

void OrbWidget::paint_orbit(QPainter& painter, const QPointF& center, double radius,
                            const QColor& accent, const QColor& secondary) {
    const double orbit_radius = radius * 1.30;
    const double tilt = -18.0 * kPi / 180.0;
    const double squash = 0.34;
    const QRectF ring(-orbit_radius, -orbit_radius, orbit_radius * 2.0, orbit_radius * 2.0);
    const Theme& theme = current_theme();

    painter.save();
    painter.translate(center);
    painter.rotate(-18.0);
    painter.scale(1.0, squash);
    painter.setBrush(Qt::NoBrush);
    const QColor faint = with_alpha(parse_color(theme.text), theme.is_light ? 34 : 18);
    painter.setPen(QPen(faint, 1.0));
    painter.drawEllipse(ring);

    const int start = static_cast<int>(std::fmod(spin_ * 180.0 / kPi, 360.0) * 16.0);
    const double level = (state_ == "listening") ? input_level_ : 0.0;
    const int span = static_cast<int>((64.0 + 60.0 * level) * 16.0);
    const int soft_alpha = theme.is_light ? static_cast<int>(34.0 + 60.0 * level)
                                          : static_cast<int>(58.0 + 110.0 * level);
    painter.setPen(QPen(with_alpha(accent, soft_alpha), 4.4 + 2.2 * level));
    painter.drawArc(ring, start, span);

    const int bright_alpha = theme.is_light ? static_cast<int>(140.0 + 50.0 * level)
                                            : static_cast<int>(190.0 + 55.0 * level);
    painter.setPen(QPen(with_alpha(secondary, bright_alpha), 1.8 + 0.8 * level));
    painter.drawArc(ring, start, span);
    painter.restore();

    const double x = std::cos(spin_) * orbit_radius;
    const double y = std::sin(spin_) * orbit_radius * squash;
    const QPointF point(center.x() + x * std::cos(tilt) - y * std::sin(tilt),
                        center.y() + x * std::sin(tilt) + y * std::cos(tilt));

    const double dot_size = std::max(7.0, radius * 0.09);
    QRadialGradient dot_glow(point, dot_size);
    dot_glow.setColorAt(0.0, with_alpha(secondary, theme.is_light ? 80 : 120));
    dot_glow.setColorAt(1.0, with_alpha(secondary, 0));
    painter.setPen(Qt::NoPen);
    painter.setBrush(dot_glow);
    painter.drawEllipse(point, dot_size, dot_size);

    painter.setBrush(with_alpha(secondary, theme.is_light ? 200 : 235));
    const double dot_radius = std::max(1.6, radius * 0.018);
    painter.drawEllipse(point, dot_radius, dot_radius);
}

void OrbWidget::paint_level_ring(QPainter& painter, const QPointF& center, double radius,
                                 const QColor& accent) {
    const double level = (state_ == "listening") ? input_level_ : 0.0;
    if (level <= 0.015) {
        return;
    }
    painter.save();
    painter.setBrush(Qt::NoBrush);
    const double ring_radius = radius * (1.12 + 0.14 * level);
    const QColor glow = with_alpha(accent, static_cast<int>(70.0 + 90.0 * level));
    painter.setPen(QPen(glow, 2.0 + 2.4 * level));
    const int start = static_cast<int>(std::fmod(-spin_ * 1.6 * 180.0 / kPi, 360.0) * 16.0);
    painter.drawArc(QRectF(-ring_radius, -ring_radius, ring_radius * 2.0, ring_radius * 2.0),
                    start, static_cast<int>(96.0 * 16.0));
    painter.setPen(QPen(with_alpha(accent, static_cast<int>(30.0 + 40.0 * level)), 1.0));
    painter.drawEllipse(center, ring_radius, ring_radius);
    painter.restore();
}

void OrbWidget::paint_sphere(QPainter& painter, const QRectF& sphere, const QColor& primary,
                             const QColor& secondary, double breath) {
    QPainterPath path;
    path.addEllipse(sphere);
    const Theme& theme = current_theme();

    painter.save();
    painter.setClipPath(path);
    const QPointF light(sphere.center().x() - sphere.width() * 0.18,
                        sphere.center().y() - sphere.height() * 0.22);

    QRadialGradient body(light, sphere.width() * 0.95);
    if (theme.is_light) {
        body.setColorAt(0.0, QColor(255, 255, 255, 255));
        body.setColorAt(0.55, parse_color(theme.panel_2));
        body.setColorAt(1.0, mix_colors(parse_color(theme.background_2), primary, 0.14));
    } else {
        body.setColorAt(0.0, mix_colors(parse_color(theme.panel_2), primary, 0.34));
        body.setColorAt(0.46, parse_color(theme.panel_2));
        body.setColorAt(1.0, mix_colors(parse_color(theme.background), primary, 0.10));
    }
    painter.setPen(Qt::NoPen);
    painter.setBrush(body);
    painter.drawEllipse(sphere);

    QRadialGradient inner(QPointF(sphere.center().x(), sphere.center().y() + sphere.height() * 0.16),
                          sphere.width() * 0.62);
    const double level = (state_ == "listening") ? input_level_ : 0.0;
    double inner_alpha = (state_ != "idle") ? (62.0 + 16.0 * breath) : (26.0 + 8.0 * breath);
    inner_alpha *= 1.0 + 0.9 * level;
    const int ia = static_cast<int>(inner_alpha);
    inner.setColorAt(0.0, with_alpha(secondary, std::min(200, ia)));
    inner.setColorAt(0.62, with_alpha(secondary, static_cast<int>(ia * 0.32)));
    inner.setColorAt(1.0, with_alpha(secondary, 0));
    painter.setBrush(inner);
    painter.drawEllipse(sphere);

    const QPointF highlight_center(sphere.center().x() - sphere.width() * 0.26,
                                   sphere.center().y() - sphere.height() * 0.30);
    const double highlight_radius = sphere.width() * 0.34;
    QRadialGradient highlight(highlight_center, highlight_radius);
    if (theme.is_light) {
        highlight.setColorAt(0.0, QColor(255, 255, 255, 235));
        highlight.setColorAt(0.5, QColor(255, 255, 255, 90));
    } else {
        highlight.setColorAt(0.0, QColor(255, 255, 255, 112));
        highlight.setColorAt(0.5, QColor(255, 255, 255, 40));
    }
    highlight.setColorAt(1.0, QColor(255, 255, 255, 0));
    painter.setBrush(highlight);
    painter.drawEllipse(highlight_center, highlight_radius, highlight_radius);

    painter.setPen(Qt::NoPen);
    for (const Sparkle& s : sparkles_) {
        const double sparkle_angle = s.angle + spin_ * (0.4 + s.distance * 0.5);
        const double reach = sphere.width() * 0.38 * s.distance;
        const QPointF sparkle(sphere.center().x() + std::cos(sparkle_angle) * reach,
                              sphere.center().y() + std::sin(sparkle_angle) * reach * 0.85);
        const double twinkle = 0.5 + 0.5 * std::sin(phase_ * 2.0 + s.offset);
        const double alpha = (16.0 + 44.0 * s.brightness) * (0.4 + 0.6 * twinkle);
        const QColor sparkle_color =
            theme.is_light ? with_alpha(primary, static_cast<int>(alpha * 0.65))
                           : with_alpha(mix_colors(primary, QColor(255, 255, 255), 0.30),
                                        static_cast<int>(alpha));
        painter.setBrush(sparkle_color);
        painter.drawEllipse(sparkle, s.size, s.size);
    }
    painter.restore();

    const double level2 = (state_ == "listening") ? input_level_ : 0.0;
    const int rim_alpha = theme.is_light ? static_cast<int>(58.0 + 40.0 * level2)
                                         : static_cast<int>(92.0 + 80.0 * level2);
    painter.setBrush(Qt::NoBrush);
    painter.setPen(QPen(with_alpha(primary, rim_alpha), 1.4 + 0.8 * level2));
    painter.drawEllipse(sphere);

    const QRectF glint = sphere.adjusted(sphere.width() * 0.07, sphere.height() * 0.07,
                                         -sphere.width() * 0.07, -sphere.height() * 0.07);
    painter.setPen(QPen(QColor(255, 255, 255, theme.is_light ? 130 : 66), 1.5));
    painter.drawArc(glint, static_cast<int>(96.0 * 16.0), static_cast<int>(74.0 * 16.0));
}

void OrbWidget::paint_burst(QPainter& painter, const QPointF& center, double radius) {
    if (burst_ <= 0.02) {
        return;
    }
    const double travel = 1.0 - burst_;
    painter.setBrush(Qt::NoBrush);
    struct Ring {
        double factor;
        double weight;
    };
    for (const Ring& r : {Ring{0.0, 1.0}, Ring{0.14, 0.55}}) {
        const double phase = std::clamp(travel - r.factor, 0.0, 1.0);
        const int alpha = static_cast<int>(132.0 * burst_ * (1.0 - phase * 0.7) * r.weight);
        painter.setPen(QPen(with_alpha(burst_color_, alpha), 1.5 + 0.9 * burst_));
        const double radius_now = radius * (1.02 + 0.30 * phase);
        painter.drawEllipse(center, radius_now, radius_now);
    }
}

}  // namespace jarvis::ui
