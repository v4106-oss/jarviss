#include <jarvis/ui/shell_parts.hpp>

#include <QEnterEvent>
#include <QFontMetrics>
#include <QHBoxLayout>
#include <QLabel>
#include <QMouseEvent>
#include <QPainter>
#include <QPen>
#include <QStackedWidget>
#include <QStyle>
#include <QVBoxLayout>

#include <algorithm>
#include <cmath>
#include <cstdio>

#include <jarvis/ui/hud_widgets.hpp>
#include <jarvis/ui/theme.hpp>

namespace jarvis::ui {

namespace {

constexpr double kPi = 3.14159265358979323846;

QColor mix_color(const QColor& base, const QColor& target, double amount) {
    const double t = std::clamp(amount, 0.0, 1.0);
    return QColor(int(base.red() + (target.red() - base.red()) * t),
                  int(base.green() + (target.green() - base.green()) * t),
                  int(base.blue() + (target.blue() - base.blue()) * t),
                  int(base.alpha() + (target.alpha() - base.alpha()) * t));
}

QPainterPath rounded_rect(const QRectF& rect, double radius) {
    QPainterPath path;
    path.addRoundedRect(rect, radius, radius);
    return path;
}

QPainterPath arc_path(const QRectF& rect, double start_deg, double span_deg) {
    QPainterPath path;
    path.arcMoveTo(rect, start_deg);
    path.arcTo(rect, start_deg, span_deg);
    return path;
}

QString qss(const std::string& text) { return QString::fromStdString(text); }

}  // namespace

QPainterPath glyph_path(const std::string& key) {
    QPainterPath path;
    std::string k = key;
    std::transform(k.begin(), k.end(), k.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    if (k == "chat") {
        path.addPath(rounded_rect(QRectF(3, 4, 18, 13.5), 4));
        path.moveTo(8.5, 17.5);
        path.lineTo(7.2, 21.2);
        path.lineTo(12.6, 17.5);
    } else if (k == "spark") {
        path.moveTo(12, 2.2);
        path.lineTo(14.3, 9.7);
        path.lineTo(21.8, 12);
        path.lineTo(14.3, 14.3);
        path.lineTo(12, 21.8);
        path.lineTo(9.7, 14.3);
        path.lineTo(2.2, 12);
        path.lineTo(9.7, 9.7);
        path.closeSubpath();
    } else if (k == "search") {
        path.addEllipse(QPointF(10.5, 10.5), 5.6, 5.6);
        path.moveTo(14.7, 14.7);
        path.lineTo(20.5, 20.5);
    } else if (k == "mic") {
        path.addPath(rounded_rect(QRectF(9, 2.4, 6, 11.2), 3));
        path.moveTo(5.4, 11.2);
        path.cubicTo(5.4, 15, 8.2, 17.4, 12, 17.4);
        path.cubicTo(15.8, 17.4, 18.6, 15, 18.6, 11.2);
        path.moveTo(12, 17.4);
        path.lineTo(12, 21.2);
        path.moveTo(8.4, 21.2);
        path.lineTo(15.6, 21.2);
    } else if (k == "camera") {
        path.addPath(rounded_rect(QRectF(2.8, 6.8, 18.4, 12.4), 3.2));
        path.moveTo(8.2, 6.8);
        path.lineTo(9.8, 4.2);
        path.lineTo(14.2, 4.2);
        path.lineTo(15.8, 6.8);
        path.addEllipse(QPointF(12, 13), 3.8, 3.8);
    } else if (k == "map") {
        path.moveTo(12, 2.4);
        path.cubicTo(7.5, 2.4, 4, 5.9, 4, 10.3);
        path.cubicTo(4, 16, 12, 21.8, 12, 21.8);
        path.cubicTo(12, 21.8, 20, 16, 20, 10.3);
        path.cubicTo(20, 5.9, 16.5, 2.4, 12, 2.4);
        path.closeSubpath();
        path.addEllipse(QPointF(12, 10), 2.6, 2.6);
    } else if (k == "book") {
        path.moveTo(4, 4.6);
        path.cubicTo(7.2, 3.4, 10.2, 3.8, 12, 5.8);
        path.cubicTo(13.8, 3.8, 16.8, 3.4, 20, 4.6);
        path.lineTo(20, 19);
        path.cubicTo(16.8, 18, 13.8, 18.2, 12, 20.1);
        path.cubicTo(10.2, 18.2, 7.2, 18, 4, 19);
        path.closeSubpath();
        path.moveTo(12, 5.8);
        path.lineTo(12, 20.1);
    } else if (k == "clock") {
        path.addEllipse(QPointF(12, 12), 9, 9);
        path.moveTo(12, 6.6);
        path.lineTo(12, 12);
        path.lineTo(16.2, 14.2);
    } else if (k == "screen") {
        path.addPath(rounded_rect(QRectF(2.8, 4, 18.4, 12.4), 2.6));
        path.moveTo(9.2, 20.2);
        path.lineTo(14.8, 20.2);
        path.moveTo(12, 16.4);
        path.lineTo(12, 20.2);
    } else if (k == "settings") {
        path.addEllipse(QPointF(12, 12), 3.1, 3.1);
        for (int index = 0; index < 8; ++index) {
            const double angle = index * 45.0 * kPi / 180.0;
            path.moveTo(12 + std::cos(angle) * 7.2, 12 + std::sin(angle) * 7.2);
            path.lineTo(12 + std::cos(angle) * 9.4, 12 + std::sin(angle) * 9.4);
        }
    } else if (k == "bell") {
        path.moveTo(6.2, 16.4);
        path.cubicTo(7.4, 14.4, 7.2, 12.2, 7.2, 10.2);
        path.cubicTo(7.2, 6.6, 9.4, 4.2, 12, 4.2);
        path.cubicTo(14.6, 4.2, 16.8, 6.6, 16.8, 10.2);
        path.cubicTo(16.8, 12.2, 16.6, 14.4, 17.8, 16.4);
        path.closeSubpath();
        path.moveTo(10.1, 19.2);
        path.cubicTo(10.6, 20.6, 13.4, 20.6, 13.9, 19.2);
    } else if (k == "globe") {
        path.addEllipse(QPointF(12, 12), 9, 9);
        path.addEllipse(QPointF(12, 12), 4, 9);
        path.moveTo(3.2, 12);
        path.lineTo(20.8, 12);
        path.moveTo(4.8, 7.2);
        path.lineTo(19.2, 7.2);
        path.moveTo(4.8, 16.8);
        path.lineTo(19.2, 16.8);
    } else if (k == "browser") {
        path.addPath(rounded_rect(QRectF(2.8, 3.4, 18.4, 17.2), 3.0));
        path.moveTo(2.8, 8.4);
        path.lineTo(21.2, 8.4);
        path.addEllipse(QPointF(12, 14.6), 3.6, 3.6);
        path.addEllipse(QPointF(12, 14.6), 1.4, 3.6);
        path.moveTo(8.4, 14.6);
        path.lineTo(15.6, 14.6);
    } else if (k == "plus") {
        path.moveTo(12, 4.4);
        path.lineTo(12, 19.6);
        path.moveTo(4.4, 12);
        path.lineTo(19.6, 12);
    } else if (k == "send") {
        path.moveTo(3.2, 11.2);
        path.lineTo(20.8, 3.4);
        path.lineTo(14.2, 20.8);
        path.lineTo(11.1, 13.2);
        path.closeSubpath();
        path.moveTo(11.1, 13.2);
        path.lineTo(20.8, 3.4);
    } else if (k == "stop") {
        path.addPath(rounded_rect(QRectF(7, 7, 10, 10), 2.2));
    } else if (k == "pause") {
        path.addPath(rounded_rect(QRectF(7.4, 6, 3.4, 12), 1.4));
        path.addPath(rounded_rect(QRectF(13.2, 6, 3.4, 12), 1.4));
    } else if (k == "play") {
        path.moveTo(8.4, 5.8);
        path.lineTo(18.4, 12);
        path.lineTo(8.4, 18.2);
        path.closeSubpath();
    } else if (k == "power") {
        path.addPath(arc_path(QRectF(3.6, 3.6, 16.8, 16.8), 118, 304));
        path.moveTo(12, 2.4);
        path.lineTo(12, 11.2);
    } else if (k == "expand") {
        path.moveTo(4.2, 9.2);
        path.lineTo(4.2, 4.2);
        path.lineTo(9.2, 4.2);
        path.moveTo(14.8, 4.2);
        path.lineTo(19.8, 4.2);
        path.lineTo(19.8, 9.2);
        path.moveTo(4.2, 14.8);
        path.lineTo(4.2, 19.8);
        path.lineTo(9.2, 19.8);
        path.moveTo(14.8, 19.8);
        path.lineTo(19.8, 19.8);
        path.lineTo(19.8, 14.8);
    } else if (k == "collapse") {
        path.moveTo(9.2, 4.2);
        path.lineTo(9.2, 9.2);
        path.lineTo(4.2, 9.2);
        path.moveTo(14.8, 4.2);
        path.lineTo(14.8, 9.2);
        path.lineTo(19.8, 9.2);
        path.moveTo(9.2, 19.8);
        path.lineTo(9.2, 14.8);
        path.lineTo(4.2, 14.8);
        path.moveTo(14.8, 19.8);
        path.lineTo(14.8, 14.8);
        path.lineTo(19.8, 14.8);
    } else if (k == "copy") {
        path.addPath(rounded_rect(QRectF(8.4, 8.4, 11.4, 11.4), 2.4));
        path.moveTo(15.6, 8.4);
        path.lineTo(15.6, 6.6);
        path.cubicTo(15.6, 5.4, 14.8, 4.6, 13.6, 4.6);
        path.lineTo(6.4, 4.6);
        path.cubicTo(5.2, 4.6, 4.4, 5.4, 4.4, 6.6);
        path.lineTo(4.4, 13.8);
        path.cubicTo(4.4, 15, 5.2, 15.8, 6.4, 15.8);
        path.lineTo(8.4, 15.8);
    } else if (k == "check") {
        path.moveTo(4.2, 12.6);
        path.lineTo(9.4, 17.8);
        path.lineTo(19.8, 6.4);
    } else if (k == "grid") {
        path.addPath(rounded_rect(QRectF(4, 4, 6.6, 6.6), 1.8));
        path.addPath(rounded_rect(QRectF(13.4, 4, 6.6, 6.6), 1.8));
        path.addPath(rounded_rect(QRectF(4, 13.4, 6.6, 6.6), 1.8));
        path.addPath(rounded_rect(QRectF(13.4, 13.4, 6.6, 6.6), 1.8));
    } else if (k == "layers") {
        path.moveTo(12, 3.2);
        path.lineTo(21, 7.8);
        path.lineTo(12, 12.4);
        path.lineTo(3, 7.8);
        path.closeSubpath();
        path.moveTo(3.8, 12.2);
        path.lineTo(12, 16.5);
        path.lineTo(20.2, 12.2);
        path.moveTo(3.8, 16.2);
        path.lineTo(12, 20.5);
        path.lineTo(20.2, 16.2);
    } else if (k == "user") {
        path.addEllipse(QPointF(12, 8.2), 3.6, 3.6);
        path.moveTo(4.6, 20.2);
        path.cubicTo(5.4, 15.8, 8.2, 13.6, 12, 13.6);
        path.cubicTo(15.8, 13.6, 18.6, 15.8, 19.4, 20.2);
    } else if (k == "menu") {
        path.moveTo(4, 6.4);
        path.lineTo(20, 6.4);
        path.moveTo(4, 12);
        path.lineTo(20, 12);
        path.moveTo(4, 17.6);
        path.lineTo(20, 17.6);
    } else if (k == "arrow") {
        path.moveTo(4.2, 12);
        path.lineTo(19.2, 12);
        path.moveTo(13, 5.8);
        path.lineTo(19.2, 12);
        path.lineTo(13, 18.2);
    } else {
        path.addEllipse(QPointF(12, 12), 7.4, 7.4);
    }
    return path;
}

void draw_glyph(QPainter& painter, const std::string& key, const QRectF& rect,
                const QColor& color, double width) {
    painter.save();
    painter.setRenderHint(QPainter::Antialiasing, true);
    const double scale = std::min(rect.width(), rect.height()) / 24.0;
    painter.translate(rect.x(), rect.y());
    painter.scale(scale, scale);
    QPen pen(color, std::max(0.8, width / std::max(scale, 0.001)));
    pen.setCapStyle(Qt::RoundCap);
    pen.setJoinStyle(Qt::RoundJoin);
    painter.setPen(pen);
    painter.setBrush(Qt::NoBrush);
    painter.drawPath(glyph_path(key));
    painter.restore();
}

// ---------------------------------------------------------------------------
// GlyphIcon
// ---------------------------------------------------------------------------
GlyphIcon::GlyphIcon(const std::string& key, int size, const std::string& color, QWidget* parent)
    : QWidget(parent), key_(key) {
    color_ = color.empty() ? parse_color(current_theme().text_soft) : parse_color(color);
    setFixedSize(size, size);
    setAttribute(Qt::WA_TransparentForMouseEvents, true);
}

void GlyphIcon::set_glyph(const std::string& key) {
    key_ = key;
    update();
}

void GlyphIcon::set_color(const std::string& color) {
    color_ = parse_color(color);
    update();
}

void GlyphIcon::paintEvent(QPaintEvent* event) {
    Q_UNUSED(event);
    QPainter painter(this);
    draw_glyph(painter, key_, QRectF(1.4, 1.4, width() - 2.8, height() - 2.8), color_, 1.7);
}

// ---------------------------------------------------------------------------
// GlyphButton
// ---------------------------------------------------------------------------
GlyphButton::GlyphButton(const std::string& key, const std::string& tooltip, int size,
                         bool checkable, bool accent, QWidget* parent)
    : QWidget(parent), key_(key), checkable_(checkable), accent_(accent), size_(size) {
    setCursor(Qt::PointingHandCursor);
    setFixedSize(size, size);
    setToolTip(QString::fromStdString(tooltip));
    setAccessibleName(QString::fromStdString(tooltip.empty() ? key : tooltip));
    timer_.setInterval(30);
    QObject::connect(&timer_, &QTimer::timeout, this, [this] { tick(); });
}

void GlyphButton::set_glyph(const std::string& key) {
    key_ = key;
    update();
}

void GlyphButton::set_active(bool active) {
    if (checkable_) {
        set_checked(active);
    }
    active_ = active;
    ensure_anim();
    update();
}

bool GlyphButton::is_active() const { return active_ || (checkable_ && checked_); }

void GlyphButton::set_accent(bool accent) {
    accent_ = accent;
    update();
}

void GlyphButton::set_checked(bool checked) {
    checked_ = checked;
    update();
}

void GlyphButton::pulse() {
    pulse_value_ = 0.0;
    pulsing_ = true;
    ensure_anim();
}

void GlyphButton::ensure_anim() {
    if (!timer_.isActive()) {
        timer_.start();
    }
}

void GlyphButton::tick() {
    const double hover_t = hover_ ? 1.0 : 0.0;
    const double active_t = active_ ? 1.0 : 0.0;
    hover_value_ += (hover_t - hover_value_) * 0.35;
    active_value_ += (active_t - active_value_) * 0.3;
    if (pulsing_) {
        pulse_value_ += (1.0 - pulse_value_) * 0.2 + 0.02;
        if (pulse_value_ >= 0.98) {
            pulse_value_ = 0.0;
            pulsing_ = false;
        }
    }
    const bool settled = std::fabs(hover_value_ - hover_t) < 0.01 &&
                         std::fabs(active_value_ - active_t) < 0.01 && !pulsing_;
    if (settled) {
        hover_value_ = hover_t;
        active_value_ = active_t;
        timer_.stop();
    }
    update();
}

void GlyphButton::enterEvent(QEnterEvent* event) {
    hover_ = true;
    ensure_anim();
    QWidget::enterEvent(event);
}

void GlyphButton::leaveEvent(QEvent* event) {
    hover_ = false;
    ensure_anim();
    QWidget::leaveEvent(event);
}

void GlyphButton::mouseReleaseEvent(QMouseEvent* event) {
    if (event->button() == Qt::LeftButton && rect().contains(event->position().toPoint())) {
        if (checkable_) {
            checked_ = !checked_;
        }
        if (onClick) {
            onClick();
        }
    }
    QWidget::mouseReleaseEvent(event);
}

void GlyphButton::paintEvent(QPaintEvent* event) {
    Q_UNUSED(event);
    QPainter painter(this);
    painter.setRenderHint(QPainter::Antialiasing, true);
    const QRectF rect = QRectF(this->rect()).adjusted(1, 1, -1, -1);
    const double hover = std::clamp(hover_value_, 0.0, 1.0);
    const double active = std::clamp(active_value_, 0.0, 1.0);
    const Theme& theme = current_theme();
    const QColor red = parse_color(theme.red);
    const QColor red_bright = parse_color(theme.is_light ? theme.red : theme.red_bright);
    const QColor transparent(0, 0, 0, 0);

    QColor fill;
    QColor border;
    if (accent_) {
        fill = red;
        fill.setAlpha(int((theme.is_light ? 16 : 26) + 12 * hover + 8 * active));
        border = red;
        border.setAlpha(int(80 + 20 * hover + 25 * active));
    } else {
        QColor ghost = parse_color(theme.text);
        ghost.setAlpha(theme.is_light ? 18 : 14);
        const QColor hover_fill = mix_color(transparent, ghost, hover);
        QColor active_fill = red;
        active_fill.setAlpha(theme.is_light ? 20 : 34);
        fill = mix_color(hover_fill, active_fill, active);
        QColor ghost_border = parse_color(theme.text);
        ghost_border.setAlpha(38);
        QColor active_border = red;
        active_border.setAlpha(105);
        border = mix_color(mix_color(transparent, ghost_border, hover), active_border, active);
    }

    painter.setPen(QPen(border, 1.0));
    painter.setBrush(fill);
    const double radius = std::min(rect.width(), rect.height()) / 2.0;
    painter.drawRoundedRect(rect, radius, radius);

    const double pulse = std::clamp(pulse_value_, 0.0, 1.0);
    if (pulse > 0.01) {
        QColor ring = red_bright;
        ring.setAlpha(int(150 * (1.0 - pulse)));
        painter.setPen(QPen(ring, 1.4));
        painter.setBrush(Qt::NoBrush);
        painter.drawEllipse(rect.center(), radius * (0.42 + 0.5 * pulse),
                            radius * (0.42 + 0.5 * pulse));
    }

    const double pad = size_ * 0.28;
    QColor color;
    if (!isEnabled()) {
        color = parse_color(theme.faint);
    } else if (accent_) {
        color = red_bright;
    } else {
        color = mix_color(parse_color(theme.muted), parse_color(theme.text_soft), hover);
        color = mix_color(color, red_bright, active);
    }
    draw_glyph(painter, key_, QRectF(pad, pad, size_ - pad * 2, size_ - pad * 2), color, 1.65);

    if (active > 0.02 && (key_ == "mic" || key_ == "camera" || key_ == "screen")) {
        QColor dot = red_bright;
        dot.setAlpha(int(255 * active));
        painter.setPen(Qt::NoPen);
        painter.setBrush(dot);
        painter.drawEllipse(QPointF(rect.right() - 4.5, rect.top() + 4.5), 2.2, 2.2);
    }
}

// ---------------------------------------------------------------------------
// NavButton
// ---------------------------------------------------------------------------
NavButton::NavButton(const std::string& key, const std::string& label, const std::string& tooltip,
                     QWidget* parent)
    : QWidget(parent), key_(key), label_(label) {
    setCursor(Qt::PointingHandCursor);
    setFixedSize(62, 52);
    setToolTip(QString::fromStdString(tooltip.empty() ? label : tooltip));
    setAccessibleName(QString::fromStdString(tooltip.empty() ? label : tooltip));
    timer_.setInterval(30);
    QObject::connect(&timer_, &QTimer::timeout, this, [this] { tick(); });
}

void NavButton::set_active(bool active) {
    active_ = active;
    ensure_anim();
    update();
}

void NavButton::ensure_anim() {
    if (!timer_.isActive()) {
        timer_.start();
    }
}

void NavButton::tick() {
    const double hover_t = hover_ ? 1.0 : 0.0;
    const double active_t = active_ ? 1.0 : 0.0;
    const double press_t = press_ ? 1.0 : 0.0;
    hover_value_ += (hover_t - hover_value_) * 0.35;
    active_value_ += (active_t - active_value_) * 0.3;
    press_value_ += (press_t - press_value_) * 0.4;
    const bool settled = std::fabs(hover_value_ - hover_t) < 0.01 &&
                         std::fabs(active_value_ - active_t) < 0.01 &&
                         std::fabs(press_value_ - press_t) < 0.01;
    if (settled) {
        hover_value_ = hover_t;
        active_value_ = active_t;
        press_value_ = press_t;
        timer_.stop();
    }
    update();
}

void NavButton::enterEvent(QEnterEvent* event) {
    hover_ = true;
    ensure_anim();
    QWidget::enterEvent(event);
}

void NavButton::leaveEvent(QEvent* event) {
    hover_ = false;
    ensure_anim();
    QWidget::leaveEvent(event);
}

void NavButton::mousePressEvent(QMouseEvent* event) {
    press_ = true;
    ensure_anim();
    QWidget::mousePressEvent(event);
}

void NavButton::mouseReleaseEvent(QMouseEvent* event) {
    press_ = false;
    ensure_anim();
    if (event->button() == Qt::LeftButton && rect().contains(event->position().toPoint()) &&
        onClick) {
        onClick();
    }
    QWidget::mouseReleaseEvent(event);
}

void NavButton::paintEvent(QPaintEvent* event) {
    Q_UNUSED(event);
    QPainter painter(this);
    painter.setRenderHint(QPainter::Antialiasing, true);
    const QRectF rect = QRectF(this->rect()).adjusted(3, 2, -3, -2);
    const double hover = std::clamp(hover_value_, 0.0, 1.0);
    const double active = std::clamp(active_value_, 0.0, 1.0);
    const double press = std::clamp(press_value_, 0.0, 1.0);
    const Theme& theme = current_theme();
    const QColor red = parse_color(theme.red);
    const QColor red_bright = parse_color(theme.is_light ? theme.red : theme.red_bright);
    const QColor transparent(0, 0, 0, 0);
    QColor ghost = parse_color(theme.text);
    ghost.setAlpha(theme.is_light ? 14 : 10);
    QColor active_fill = red;
    active_fill.setAlpha(theme.is_light ? 12 : 18);
    QColor press_fill = red_bright;
    press_fill.setAlpha(theme.is_light ? 9 : 14);
    QColor fill = mix_color(mix_color(transparent, ghost, hover), active_fill, active);
    fill = mix_color(fill, press_fill, press);
    painter.setPen(Qt::NoPen);
    painter.setBrush(fill);
    const double squeeze = 1.5 * press;
    painter.drawRoundedRect(rect.adjusted(squeeze, squeeze, -squeeze, -squeeze), 12, 12);

    QColor color;
    if (!isEnabled()) {
        color = parse_color(theme.faint);
    } else {
        color = mix_color(parse_color(theme.muted), parse_color(theme.text_soft), hover);
        color = mix_color(color, red_bright, active);
        color = mix_color(color, parse_color(theme.text), 0.35 * press);
    }
    QRectF icon_rect(rect.center().x() - 9, rect.top() + 7, 18, 18);
    icon_rect.adjust(press * 0.8, press * 0.8, -press * 0.8, -press * 0.8);
    draw_glyph(painter, key_, icon_rect, color, 1.55);
    painter.setFont(hud_font(6, QFont::Weight::Medium, 0.2));
    painter.setPen(color);
    painter.drawText(QRectF(rect.left(), rect.top() + 29, rect.width(), 14),
                     Qt::AlignHCenter | Qt::AlignVCenter, QString::fromStdString(label_));
}

// ---------------------------------------------------------------------------
// BrandMark
// ---------------------------------------------------------------------------
BrandMark::BrandMark(int size, QWidget* parent) : QWidget(parent) { setFixedSize(size, size); }

void BrandMark::set_animating(bool enabled) { Q_UNUSED(enabled); }

void BrandMark::paintEvent(QPaintEvent* event) {
    Q_UNUSED(event);
    QPainter painter(this);
    painter.setRenderHint(QPainter::Antialiasing, true);
    const QRectF rect = QRectF(this->rect()).adjusted(0.5, 0.5, -0.5, -0.5);
    const Theme& theme = current_theme();
    QColor border = parse_color(theme.text);
    border.setAlpha(20);
    painter.setPen(QPen(border, 1.0));
    painter.setBrush(parse_color(theme.panel_2));
    painter.drawRoundedRect(rect, 11, 11);

    const QPointF center = rect.center();
    QRectF ring(0, 0, rect.width() * 0.42, rect.height() * 0.42);
    ring.moveCenter(center);
    QColor accent = parse_color(theme.is_light ? theme.red : theme.red_bright);
    accent.setAlpha(190);
    painter.setBrush(Qt::NoBrush);
    painter.setPen(QPen(accent, 1.5));
    painter.drawEllipse(ring);
    painter.setPen(Qt::NoPen);
    painter.setBrush(parse_color(theme.red));
    painter.drawEllipse(center, 2.5, 2.5);
}

// ---------------------------------------------------------------------------
// PulseDot
// ---------------------------------------------------------------------------
PulseDot::PulseDot(const std::string& color, int size, QWidget* parent) : QWidget(parent) {
    color_ = color.empty() ? parse_color(current_theme().red) : parse_color(color);
    setFixedSize(size, size);
    setAttribute(Qt::WA_TransparentForMouseEvents, true);
    timer_.setInterval(60);
    QObject::connect(&timer_, &QTimer::timeout, this, [this] { tick(); });
    timer_.start();
}

void PulseDot::set_animating(bool enabled) {
    if (enabled && isVisible()) {
        timer_.start();
    } else {
        timer_.stop();
    }
}

void PulseDot::tick() {
    phase_ = std::fmod(phase_ + 0.08, 2.0 * kPi);
    if (burst_ > 0.0) {
        burst_ = std::max(0.0, burst_ - 0.09);
    }
    update();
}

void PulseDot::set_color(const std::string& color) {
    const QColor fresh = parse_color(color);
    if (fresh != color_) {
        burst_ = 1.0;
    }
    color_ = fresh;
    update();
}

void PulseDot::set_active(bool active) {
    active_ = active;
    update();
}

void PulseDot::paintEvent(QPaintEvent* event) {
    Q_UNUSED(event);
    QPainter painter(this);
    painter.setRenderHint(QPainter::Antialiasing, true);
    const QPointF center(width() / 2.0, height() / 2.0);
    const double base = std::min(width(), height());
    const double pulse = 0.5 + 0.5 * std::sin(phase_);
    if (burst_ > 0.01) {
        QColor ring = color_;
        ring.setAlpha(int(95 * burst_));
        painter.setPen(QPen(ring, 1.2));
        painter.setBrush(Qt::NoBrush);
        const double radius = base * (0.30 + 0.20 * (1.0 - burst_));
        painter.drawEllipse(center, radius, radius);
    }
    QColor halo = color_;
    halo.setAlpha(int((46 + 46 * pulse + 60 * burst_) * (active_ ? 1.0 : 0.0) +
                      (active_ ? 0 : 24)));
    painter.setPen(Qt::NoPen);
    painter.setBrush(halo);
    painter.drawEllipse(center, base * 0.48, base * 0.48);
    QColor core = color_;
    core.setAlpha(active_ ? 240 : 120);
    painter.setBrush(core);
    painter.drawEllipse(center, base * 0.20, base * 0.20);
}

// ---------------------------------------------------------------------------
// TextChip
// ---------------------------------------------------------------------------
TextChip::TextChip(const std::string& text, const std::string& tooltip, bool dot, QWidget* parent)
    : QPushButton(parent), label_(text), dot_(dot) {
    setCursor(Qt::PointingHandCursor);
    setFont(hud_font(7, QFont::Weight::Medium, 0.2));
    setToolTip(QString::fromStdString(tooltip));
    setMinimumHeight(26);
    const Theme& theme = current_theme();
    setStyleSheet(qss(
        "QPushButton{background:transparent;color:" + theme.muted + ";border:1px solid " +
        theme.border + ";border-radius:13px;padding:4px 11px;}" +
        "QPushButton:hover{color:" + theme.text_soft + ";border-color:" + rgba_css(theme.text, 0.16) +
        ";background:" + rgba_css(theme.text, 0.035) + ";}" +
        "QPushButton[active=\"true\"]{color:" + (theme.is_light ? theme.text : theme.red_bright) +
        ";border-color:" + rgba_css(theme.red, 0.34) + ";background:" +
        rgba_css(theme.red, theme.is_light ? 0.07 : 0.10) + ";}"));
    set_active(false);
    render();
}

void TextChip::render() {
    setText(QString::fromStdString((dot_ ? "●  " : "") + label_));
}

void TextChip::set_label(const std::string& text) {
    label_ = text;
    render();
}

void TextChip::set_active(bool active) {
    active_ = active;
    setProperty("active", active ? "true" : "false");
    style()->unpolish(this);
    style()->polish(this);
    update();
}

bool TextChip::is_active() const { return active_; }

// ---------------------------------------------------------------------------
// MicLevelMeter
// ---------------------------------------------------------------------------
MicLevelMeter::MicLevelMeter(QWidget* parent) : QWidget(parent) {
    const Theme& theme = current_theme();
    accent_ = parse_color(theme.is_light ? "#0e9d8f" : "#2fd6c3");
    setFixedSize(58, 22);
    setToolTip(QString::fromUtf8("Mikrofona ulaşan ses seviyesi; konuşunca çubuklar oynar."));
    setAttribute(Qt::WA_TransparentForMouseEvents, true);
    timer_.setInterval(33);
    QObject::connect(&timer_, &QTimer::timeout, this, [this] { tick(); });
}

void MicLevelMeter::set_enabled(bool enabled) {
    enabled_ = enabled;
    if (!enabled_) {
        level_ = 0.0;
    }
    ensure_timer();
    update();
}

void MicLevelMeter::set_level(double level) {
    const double value = std::clamp(level, 0.0, 1.0);
    level_ = enabled_ ? value : 0.0;
    ensure_timer();
    update();
}

void MicLevelMeter::pulse() {
    heard_ = 1.0;
    ensure_timer();
    update();
}

void MicLevelMeter::ensure_timer() {
    if (!timer_.isActive()) {
        timer_.start();
    }
}

void MicLevelMeter::tick() {
    shown_ += (level_ - shown_) * 0.5;
    if (heard_ > 0.0) {
        heard_ = std::max(0.0, heard_ - 0.07);
    }
    update();
    if (shown_ < 0.012 && heard_ <= 0.0) {
        shown_ = 0.0;
        timer_.stop();
    }
}

void MicLevelMeter::paintEvent(QPaintEvent* event) {
    Q_UNUSED(event);
    constexpr int k_bars = 9;
    QPainter painter(this);
    painter.setRenderHint(QPainter::Antialiasing, true);
    const double w = width();
    const double h = height();
    const double bar_width = 3.4;
    const double gap = (w - k_bars * bar_width) / (k_bars - 1);
    const Theme& theme = current_theme();
    QColor base = parse_color(theme.faint);
    base.setAlpha(theme.is_light ? 110 : 80);
    for (int index = 0; index < k_bars; ++index) {
        const double envelope = std::pow(std::sin(kPi * (index + 0.5) / k_bars), 0.6);
        const bool lit = shown_ >= (index + 0.5) / k_bars * 0.92;
        double bar_height = 3.0 + (h - 4.0) * 0.86 * envelope * (0.22 + 0.78 * shown_);
        if (heard_ > 0.0 && lit) {
            bar_height = std::max(bar_height, (h - 4.0) * envelope);
        }
        QColor color = (enabled_ && lit) ? accent_ : base;
        if (heard_ > 0.0) {
            const int boost = int(150 * heard_);
            color = QColor(std::min(255, color.red() + boost), std::min(255, color.green() + boost),
                           std::min(255, color.blue() + boost));
        }
        const double x = index * (bar_width + gap);
        painter.setPen(Qt::NoPen);
        painter.setBrush(color);
        painter.drawRoundedRect(QRectF(x, (h - bar_height) / 2.0, bar_width, bar_height),
                                bar_width / 2.0, bar_width / 2.0);
    }
}

// ---------------------------------------------------------------------------
// CommandPill
// ---------------------------------------------------------------------------
CommandPill::CommandPill(QWidget* parent) : QFrame(parent) {
    setCursor(Qt::PointingHandCursor);
    setFixedHeight(40);
    setMinimumWidth(240);
    setMaximumWidth(560);
    setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Fixed);
    const Theme& theme = current_theme();

    auto* layout = new QHBoxLayout(this);
    layout->setContentsMargins(13, 0, 9, 0);
    layout->setSpacing(9);
    icon_ = new GlyphIcon("search", 16, theme.muted);
    layout->addWidget(icon_);
    hint_ = hud_label("Bir komut yaz veya ara…", 9, theme.dim, QFont::Weight::Medium, 0.2);
    layout->addWidget(hint_, 1);
    auto* badge = new QFrame();
    badge->setStyleSheet(qss("background:" + rgba_css(theme.text, 0.04) + ";border:1px solid " +
                             theme.border + ";border-radius:7px;"));
    auto* badge_layout = new QHBoxLayout(badge);
    badge_layout->setContentsMargins(7, 3, 7, 3);
    badge_layout->addWidget(hud_label("Ctrl K", 7, theme.dim, QFont::Weight::Medium, 0.1));
    layout->addWidget(badge);

    timer_.setInterval(30);
    QObject::connect(&timer_, &QTimer::timeout, this, [this] { tick(); });
}

void CommandPill::set_hint(const std::string& text) {
    hint_->setText(QString::fromStdString(text));
}

void CommandPill::ensure_anim() {
    if (!timer_.isActive()) {
        timer_.start();
    }
}

void CommandPill::tick() {
    const double target = hover_ ? 1.0 : 0.0;
    hover_value_ += (target - hover_value_) * 0.35;
    if (std::fabs(hover_value_ - target) < 0.01) {
        hover_value_ = target;
        timer_.stop();
    }
    update();
}

void CommandPill::enterEvent(QEnterEvent* event) {
    hover_ = true;
    ensure_anim();
    QFrame::enterEvent(event);
}

void CommandPill::leaveEvent(QEvent* event) {
    hover_ = false;
    ensure_anim();
    QFrame::leaveEvent(event);
}

void CommandPill::mouseReleaseEvent(QMouseEvent* event) {
    if (event->button() == Qt::LeftButton && rect().contains(event->position().toPoint()) && onClick) {
        onClick();
    }
    QFrame::mouseReleaseEvent(event);
}

void CommandPill::paintEvent(QPaintEvent* event) {
    Q_UNUSED(event);
    QPainter painter(this);
    painter.setRenderHint(QPainter::Antialiasing, true);
    const QRectF rect = QRectF(this->rect()).adjusted(0.5, 0.5, -0.5, -0.5);
    const double hover = std::clamp(hover_value_, 0.0, 1.0);
    const Theme& theme = current_theme();
    const QColor base = mix_color(parse_color(theme.panel_2), parse_color(theme.panel_hover), hover);
    painter.setBrush(base);
    QColor border = parse_color(theme.text);
    border.setAlpha(int(26 + 28 * hover));
    painter.setPen(QPen(border, 1.0));
    painter.drawRoundedRect(rect, 12, 12);
}

// ---------------------------------------------------------------------------
// ComposerFrame
// ---------------------------------------------------------------------------
ComposerFrame::ComposerFrame(QWidget* parent) : QFrame(parent) {
    timer_.setInterval(30);
    QObject::connect(&timer_, &QTimer::timeout, this, [this] { tick(); });
}

void ComposerFrame::track(QWidget* widget) { widget->installEventFilter(this); }

bool ComposerFrame::eventFilter(QObject* watched, QEvent* event) {
    if (event->type() == QEvent::FocusIn) {
        focus_value_ = 1.0;
        ensure_anim();
    } else if (event->type() == QEvent::FocusOut) {
        ensure_anim();
        focus_value_ = 0.0;
    }
    return QFrame::eventFilter(watched, event);
}

void ComposerFrame::ensure_anim() {
    if (!timer_.isActive()) {
        timer_.start();
    }
}

void ComposerFrame::tick() {
    const double target = hasFocus() || (parentWidget() && parentWidget()->isActiveWindow())
                              ? focus_value_
                              : 0.0;
    Q_UNUSED(target);
    // focus_value_ 1'e yükselir (FocusIn) veya 0'a iner (FocusOut); hedefi saklamak
    // yerine üstel yaklaşımla yumuşatılır.
    const double current = focus_value_;
    const double goal = (current > 0.5) ? 1.0 : 0.0;
    focus_value_ += (goal - focus_value_) * 0.35;
    if (std::fabs(focus_value_ - goal) < 0.01) {
        focus_value_ = goal;
        timer_.stop();
    }
    update();
}

void ComposerFrame::paintEvent(QPaintEvent* event) {
    QFrame::paintEvent(event);
    if (focus_value_ <= 0.01) {
        return;
    }
    const double color = std::clamp(focus_value_, 0.0, 1.0);
    QPainter painter(this);
    painter.setRenderHint(QPainter::Antialiasing, true);
    const QRectF rect = QRectF(this->rect()).adjusted(1.2, 1.2, -1.2, -1.2);
    painter.setBrush(Qt::NoBrush);
    QColor glow = parse_color(current_theme().red);
    glow.setAlpha(int(22 * color));
    painter.setPen(QPen(glow, 4.0));
    painter.drawRoundedRect(rect.adjusted(0.4, 0.4, -0.4, -0.4), 15, 15);
    QColor accent = parse_color(current_theme().red);
    accent.setAlpha(int(150 * color));
    painter.setPen(QPen(accent, 1.1));
    painter.drawRoundedRect(rect, 15, 15);
}

// ---------------------------------------------------------------------------
// SegmentedControl / SegmentedStack
// ---------------------------------------------------------------------------
SegmentedControl::SegmentedControl(QWidget* parent) : QFrame(parent) {
    const Theme& theme = current_theme();
    setObjectName("segmentBar");
    setStyleSheet(qss("QFrame#segmentBar{background:" + rgba_css(theme.text, 0.025) +
                      ";border:1px solid " + theme.border + ";border-radius:9px;}"));
    row_ = new QHBoxLayout(this);
    row_->setContentsMargins(3, 3, 3, 3);
    row_->setSpacing(2);
}

int SegmentedControl::add_segment(const std::string& label) {
    const int index = int(buttons_.size());
    auto* button = new QPushButton(QString::fromStdString(label));
    button->setCheckable(true);
    button->setAutoExclusive(true);
    button->setCursor(Qt::PointingHandCursor);
    button->setFont(hud_font(7, QFont::Weight::Medium, 0.2));
    const Theme& theme = current_theme();
    button->setStyleSheet(qss(
        "QPushButton{background:transparent;color:" + theme.muted +
        ";border:1px solid transparent;border-radius:7px;padding:5px 9px;}" +
        "QPushButton:hover{color:" + theme.text_soft + ";background:" + rgba_css(theme.text, 0.04) +
        ";}" +
        "QPushButton:checked{color:" + theme.text + ";background:" +
        rgba_css(theme.red, theme.is_light ? 0.07 : 0.10) + ";border-color:" +
        rgba_css(theme.red, 0.22) + ";}"));
    QObject::connect(button, &QPushButton::clicked, this,
                     [this, index] { set_current(index); });
    row_->addWidget(button);
    buttons_.push_back(button);
    if (current_ < 0) {
        set_current(0);
    }
    return index;
}

void SegmentedControl::set_current(int index) {
    if (buttons_.empty()) {
        return;
    }
    index = std::clamp(index, 0, int(buttons_.size()) - 1);
    current_ = index;
    for (int position = 0; position < int(buttons_.size()); ++position) {
        buttons_[position]->setChecked(position == index);
    }
    if (on_current_changed) {
        on_current_changed(index);
    }
}

SegmentedStack::SegmentedStack(const std::vector<std::string>& labels, QWidget* parent)
    : QWidget(parent) {
    auto* layout = new QVBoxLayout(this);
    layout->setContentsMargins(0, 0, 0, 0);
    layout->setSpacing(8);
    control_ = new SegmentedControl();
    stack_ = new QStackedWidget();
    layout->addWidget(control_);
    layout->addWidget(stack_, 1);
    control_->on_current_changed = [this](int index) {
        if (index >= 0 && index < stack_->count()) {
            stack_->setCurrentIndex(index);
        }
        if (on_current_changed) {
            on_current_changed(index);
        }
    };
    for (const auto& label : labels) {
        add_page(new QWidget(), label);
    }
}

int SegmentedStack::add_page(QWidget* widget, const std::string& label) {
    const int index = control_->add_segment(label);
    stack_->addWidget(widget);
    return index;
}

void SegmentedStack::set_current(int index) { control_->set_current(index); }

// ---------------------------------------------------------------------------
// HudCard
// ---------------------------------------------------------------------------
HudCard::HudCard(const std::string& title, const std::string& trailing, const std::string& glyph,
                 int padding, bool clickable, QWidget* parent)
    : QFrame(parent), clickable_(clickable) {
    const Theme& theme = current_theme();
    setObjectName("hudCard");
    const std::string hover = clickable
                                  ? "QFrame#hudCard:hover{border-color:" + rgba_css(theme.text, 0.14) +
                                        ";background:" + rgba_css(theme.panel_hover, 0.72) + ";}"
                                  : "";
    setStyleSheet(qss("QFrame#hudCard{background:" + rgba_css(theme.panel, 0.50) +
                      ";border:1px solid " + rgba_css(theme.text, 0.06) + ";border-radius:16px;}" +
                      hover));
    if (clickable_) {
        setCursor(Qt::PointingHandCursor);
    }
    auto* outer = new QVBoxLayout(this);
    outer->setContentsMargins(padding, padding - 1, padding, padding);
    outer->setSpacing(8);

    auto* header = new QHBoxLayout();
    header->setContentsMargins(0, 0, 0, 0);
    header->setSpacing(7);
    if (!glyph.empty()) {
        icon_ = new GlyphIcon(glyph, 15, theme.red_soft);
        header->addWidget(icon_);
    }
    auto* title_label = hud_label(title, 8, theme.text_soft, QFont::Weight::Medium, 0.2);
    title_label->setSizePolicy(QSizePolicy::Ignored, QSizePolicy::Preferred);
    header->addWidget(title_label);
    header->addStretch(1);
    auto* trailing_label = hud_label(trailing, 7, theme.dim, QFont::Weight::Medium, 0.2);
    trailing_ = trailing_label;
    header->addWidget(trailing_label);
    if (!title.empty() || !trailing.empty() || !glyph.empty()) {
        outer->addLayout(header);
    }
    body_ = new QVBoxLayout();
    body_->setContentsMargins(0, 0, 0, 0);
    body_->setSpacing(6);
    outer->addLayout(body_);
}

void HudCard::set_trailing(const std::string& text) {
    if (trailing_ != nullptr) {
        trailing_->setText(QString::fromStdString(text));
    }
}

void HudCard::set_glyph(const std::string& key) {
    if (icon_ != nullptr) {
        icon_->set_glyph(key);
    }
}

void HudCard::mouseReleaseEvent(QMouseEvent* event) {
    if (clickable_ && event->button() == Qt::LeftButton &&
        rect().contains(event->position().toPoint()) && onClick) {
        onClick();
    }
    QFrame::mouseReleaseEvent(event);
}

// ---------------------------------------------------------------------------
// StatusRow / SectionLabel
// ---------------------------------------------------------------------------
StatusRow::StatusRow(const std::string& label, const std::string& value, QWidget* parent)
    : QWidget(parent) {
    auto* layout = new QHBoxLayout(this);
    layout->setContentsMargins(0, 0, 0, 0);
    layout->setSpacing(8);
    const Theme& theme = current_theme();
    layout->addWidget(hud_label(label, 7, theme.dim, QFont::Weight::Medium, 0.2));
    layout->addStretch(1);
    value_ = hud_label(value, 7, theme.text_soft, QFont::Weight::Medium, 0.2);
    value_->setAlignment(Qt::AlignRight | Qt::AlignVCenter);
    layout->addWidget(value_);
}

void StatusRow::set_value(const std::string& text) { value_->setText(QString::fromStdString(text)); }

SectionLabel::SectionLabel(const std::string& title, const std::string& trailing, QWidget* parent)
    : QWidget(parent) {
    auto* layout = new QHBoxLayout(this);
    layout->setContentsMargins(1, 0, 1, 0);
    layout->setSpacing(8);
    const Theme& theme = current_theme();
    layout->addWidget(hud_label(title, 7, theme.muted, QFont::Weight::Medium, 0.3));
    auto* line = new QFrame();
    line->setFixedHeight(1);
    line->setStyleSheet(qss("background:" + rgba_css(theme.text, 0.05) + ";border:none;"));
    layout->addWidget(line, 1);
    layout->addWidget(hud_label(trailing, 6, theme.faint, QFont::Weight::Medium, 0.2));
}

}  // namespace jarvis::ui
