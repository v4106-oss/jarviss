#include <jarvis/ui/hud_widgets.hpp>

#include <jarvis/ui/theme.hpp>

#include <QBrush>
#include <QEvent>
#include <QHBoxLayout>
#include <QLinearGradient>
#include <QMouseEvent>
#include <QPainter>
#include <QPainterPath>
#include <QPen>
#include <QRadialGradient>
#include <QSizePolicy>
#include <QTimer>
#include <QVBoxLayout>

#include <algorithm>
#include <cctype>
#include <cmath>
#include <random>

namespace jarvis::ui {

namespace {

constexpr double kPi = 3.14159265358979323846;

std::mt19937& rng() {
    static std::mt19937 gen(29u);
    return gen;
}

double rand_uniform(double lo, double hi) {
    std::uniform_real_distribution<double> dist(lo, hi);
    return dist(rng());
}

QPen round_pen(const QColor& color, double width) {
    QPen pen(color, width);
    pen.setCapStyle(Qt::PenCapStyle::RoundCap);
    return pen;
}

QColor theme_color(const std::string& hex) {
    return parse_color(hex);
}

}  // namespace

QFont hud_font(int size, QFont::Weight weight, double spacing, const std::string& family,
               bool italic) {
    const Theme& theme = current_theme();
    QFont font(QString::fromStdString(family.empty() ? theme.font : family), size);
    font.setWeight(weight);
    double clamped = std::clamp(static_cast<double>(spacing) * 0.45, 0.0, 1.4);
    font.setLetterSpacing(QFont::SpacingType::AbsoluteSpacing, clamped);
    font.setItalic(italic);
    return font;
}

QLabel* hud_label(const std::string& text, int size, const std::string& color,
                  QFont::Weight weight, double spacing, bool upper,
                  const std::string& family) {
    const Theme& theme = current_theme();
    std::string rendered = upper ? text : text;
    if (upper) {
        std::string out;
        out.reserve(text.size());
        for (char c : text) {
            out.push_back(static_cast<char>(std::toupper(static_cast<unsigned char>(c))));
        }
        rendered = out;
    }
    auto* label = new QLabel(QString::fromStdString(rendered));
    label->setFont(hud_font(size, weight, spacing, family));
    std::string c = color.empty() ? theme.muted : color;
    label->setStyleSheet(
        QStringLiteral("color:%1;background:transparent;border:none;")
            .arg(QString::fromStdString(c)));
    label->setTextFormat(Qt::TextFormat::PlainText);
    return label;
}

void set_label_color(QLabel* label, const std::string& color) {
    if (label != nullptr) {
        label->setStyleSheet(QStringLiteral("color:%1;background:transparent;border:none;")
                                 .arg(QString::fromStdString(color)));
    }
}

Divider::Divider(QWidget* parent) : QFrame(parent) {
    const Theme& theme = current_theme();
    setFixedHeight(1);
    setStyleSheet(
        QStringLiteral("background:%1;border:none;").arg(QString::fromStdString(theme.border)));
}

HudButton::HudButton(const QString& text, bool accent, bool small, QWidget* parent)
    : QPushButton(text, parent) {
    const Theme& theme = current_theme();
    setCursor(Qt::CursorShape::PointingHandCursor);
    setFont(hud_font(small ? 9 : 10, QFont::Weight::DemiBold, 1.6));
    setMinimumHeight(small ? 26 : 32);
    std::string accent_bg = accent ? rgba_css(theme.red, 0.14) : "transparent";
    std::string accent_fg = accent ? theme.red_bright : theme.text_soft;
    std::string accent_bd = accent ? theme.red_dim : theme.border;
    std::string padding = small ? "5px 10px" : "7px 16px";
    setStyleSheet(
        QStringLiteral(
            "QPushButton{background:%1;color:%2;border:1px solid %3;border-radius:5px;padding:%4;}"
            "QPushButton:hover{border-color:%5;color:%6;background:%7;}"
            "QPushButton:checked{color:%6;border-color:%5;background:%8;}"
            "QPushButton:pressed{background:%9;}"
            "QPushButton:disabled{color:%10;border-color:%3;background:transparent;}")
            .arg(QString::fromStdString(accent_bg),
                 QString::fromStdString(accent_fg),
                 QString::fromStdString(accent_bd),
                 QString::fromStdString(padding),
                 QString::fromStdString(theme.red),
                 QString::fromStdString(theme.red_bright),
                 QString::fromStdString(rgba_css(theme.red, 0.10)),
                 QString::fromStdString(rgba_css(theme.red, 0.24)),
                 QString::fromStdString(rgba_css(theme.red, 0.22)),
                 QString::fromStdString(theme.faint)));
}

ClickableFrame::ClickableFrame(QWidget* parent) : QFrame(parent) {
    setCursor(Qt::CursorShape::PointingHandCursor);
    setStyleSheet("background:transparent;border:none;");
}

void ClickableFrame::mouseReleaseEvent(QMouseEvent* event) {
    if (event->button() == Qt::MouseButton::LeftButton &&
        rect().contains(event->position().toPoint()) && onClick) {
        onClick();
    }
    QFrame::mouseReleaseEvent(event);
}

Panel::Panel(int padding, QWidget* parent) : QFrame(parent) {
    const Theme& theme = current_theme();
    setObjectName("hudPanel");
    setStyleSheet(QStringLiteral("QFrame#hudPanel{background:%1;border:1px solid %2;border-radius:16px;}")
                      .arg(QString::fromStdString(theme.panel),
                           QString::fromStdString(theme.border)));
    body_ = new QVBoxLayout(this);
    body_->setContentsMargins(padding, padding, padding, padding);
    body_->setSpacing(10);
}

SectionTitle::SectionTitle(const std::string& title, const std::string& trailing,
                           QWidget* parent)
    : QWidget(parent) {
    const Theme& theme = current_theme();
    auto* layout = new QHBoxLayout(this);
    layout->setContentsMargins(0, 0, 0, 0);
    layout->setSpacing(8);
    auto* title_label = hud_label(title, 9, theme.text_soft, QFont::Weight::Medium, 0.3);
    auto* trailing_label = hud_label(trailing, 8, theme.dim, QFont::Weight::Medium, 0.2);
    for (QLabel* label : {title_label, trailing_label}) {
        label->setSizePolicy(QSizePolicy::Policy::Ignored, QSizePolicy::Policy::Preferred);
    }
    layout->addWidget(title_label);
    layout->addStretch();
    layout->addWidget(trailing_label);
}

RingGauge::RingGauge(double value, const std::string& caption, const std::string& unit,
                     double diameter, bool show_value, QWidget* parent)
    : QWidget(parent),
      value_(std::clamp(value, 0.0, 100.0)),
      caption_(caption),
      unit_(unit),
      show_value_(show_value) {
    setFixedSize(static_cast<int>(diameter), static_cast<int>(diameter));
    setSizePolicy(QSizePolicy::Policy::Fixed, QSizePolicy::Policy::Fixed);
}

void RingGauge::set_value(double value) {
    value = std::clamp(value, 0.0, 100.0);
    if (std::abs(value - value_) > 0.15) {
        value_ = value;
        update();
    }
}

void RingGauge::paintEvent(QPaintEvent* /*event*/) {
    const Theme& theme = current_theme();
    QPainter painter(this);
    painter.setRenderHint(QPainter::RenderHint::Antialiasing);
    double side = std::min(width(), height());
    double margin = 6.0;
    QRectF rect(margin, margin, side - margin * 2, side - margin * 2);
    QPointF center = rect.center();

    QRectF arc_rect = rect.adjusted(3, 3, -3, -3);
    QColor track = theme_color(theme.text);
    track.setAlpha(26);
    painter.setPen(round_pen(track, 2.4));
    painter.drawArc(arc_rect, 225 * 16, -270 * 16);

    if (value_ > 0.4) {
        QColor glow = theme_color(theme.red);
        glow.setAlpha(40);
        painter.setPen(round_pen(glow, 5.5));
        painter.drawArc(arc_rect, 225 * 16, static_cast<int>(-270 * 16 * value_ / 100.0));
        painter.setPen(round_pen(theme_color(theme.red), 2.4));
        painter.drawArc(arc_rect, 225 * 16, static_cast<int>(-270 * 16 * value_ / 100.0));
    }

    if (show_value_) {
        painter.setPen(theme_color(theme.text));
        QFont font(QString::fromStdString(theme.font), 13);
        font.setWeight(QFont::Weight::Medium);
        painter.setFont(font);
        QString value_text = unit_ == "%"
                                 ? QStringLiteral("%%1").arg(static_cast<int>(std::lround(value_)))
                                 : QString::number(static_cast<int>(std::lround(value_)));
        painter.drawText(QRectF(0, center.y() - 15, width(), 20),
                         Qt::AlignmentFlag::AlignHCenter, value_text);
        painter.setPen(theme_color(theme.dim));
        painter.setFont(QFont(QString::fromStdString(theme.font), 7));
        painter.drawText(QRectF(0, center.y() + 5, width(), 14),
                         Qt::AlignmentFlag::AlignHCenter,
                         QString::fromStdString(caption_).toUpper());
    }
}

ClockDisc::ClockDisc(QWidget* parent) : QWidget(parent) {
    setFixedSize(220, 220);
    setCursor(Qt::CursorShape::PointingHandCursor);
}

void ClockDisc::set_time(int hour, int minute, int second) {
    QString text = QStringLiteral("%1:%2").arg(hour, 2, 10, QLatin1Char('0'))
                       .arg(minute, 2, 10, QLatin1Char('0'));
    if (text.toStdString() != time_) {
        time_ = text.toStdString();
    }
    seconds_ = second;
    day_percent_ = (hour * 3600 + minute * 60 + second) / 86400.0;
    update();
}

void ClockDisc::paintEvent(QPaintEvent* /*event*/) {
    const Theme& theme = current_theme();
    QPainter painter(this);
    painter.setRenderHint(QPainter::RenderHint::Antialiasing);
    QPointF center(width() / 2.0, height() / 2.0);
    double scale = std::min(width(), height()) / 220.0;
    double radius = 94.0 * scale;

    painter.setPen(Qt::PenStyle::NoPen);
    painter.setBrush(QColor(255, 255, 255, 8));
    painter.drawEllipse(center, radius, radius);
    painter.setBrush(Qt::BrushStyle::NoBrush);
    painter.setPen(QPen(QColor(255, 255, 255, 20), 1.0));
    painter.drawEllipse(center, radius, radius);

    for (int index = 0; index < 72; ++index) {
        double angle = (index * 5 - 90) * kPi / 180.0;
        double inner;
        QColor color;
        if (index % 6 == 0) {
            inner = radius - 8;
            color = QColor(255, 255, 255, 75);
        } else {
            inner = radius - 4;
            color = QColor(255, 255, 255, 28);
        }
        painter.setPen(QPen(color, 1.0));
        painter.drawLine(QPointF(center.x() + std::cos(angle) * inner,
                                 center.y() + std::sin(angle) * inner),
                         QPointF(center.x() + std::cos(angle) * radius,
                                 center.y() + std::sin(angle) * radius));
    }

    QRectF day_rect(center.x() - radius + 10, center.y() - radius + 10, (radius - 10) * 2,
                    (radius - 10) * 2);
    painter.setPen(round_pen(QColor(201, 164, 104, 55), 1.6));
    painter.drawArc(day_rect, 90 * 16, -static_cast<int>(360 * 16 * day_percent_));
    QColor glow = theme_color(theme.red);
    glow.setAlpha(55);
    painter.setPen(round_pen(glow, 5.0));
    painter.drawArc(day_rect, 90 * 16, -static_cast<int>(360 * 16 * day_percent_));
    painter.setPen(round_pen(theme_color(theme.red), 1.4));
    painter.drawArc(day_rect, 90 * 16, -static_cast<int>(360 * 16 * day_percent_));

    painter.setPen(theme_color(theme.text));
    QFont font(QString::fromStdString(theme.font),
               std::max(15, static_cast<int>(std::lround(26 * scale))));
    font.setWeight(QFont::Weight::DemiBold);
    font.setLetterSpacing(QFont::SpacingType::AbsoluteSpacing, 1.5 * scale);
    painter.setFont(font);
    painter.drawText(QRectF(0, center.y() - 28 * scale, width(), 44 * scale),
                     Qt::AlignmentFlag::AlignHCenter, QString::fromStdString(time_));
    painter.setFont(hud_font(std::max(5, static_cast<int>(std::lround(6 * scale))),
                             QFont::Weight::DemiBold, 2.4 * scale));
    painter.setPen(theme_color(theme.dim));
    painter.drawText(QRectF(0, center.y() + 14 * scale, width(), 14 * scale),
                     Qt::AlignmentFlag::AlignHCenter, QStringLiteral("YEREL SAAT"));
    painter.setFont(hud_font(std::max(5, static_cast<int>(std::lround(6 * scale))),
                             QFont::Weight::Medium, 1.4 * scale));
    painter.setPen(theme_color(theme.faint));
    painter.drawText(
        QRectF(0, center.y() + 30 * scale, width(), 14 * scale),
        Qt::AlignmentFlag::AlignHCenter,
        QStringLiteral("GÜNÜN %%1'İ").arg(static_cast<int>(day_percent_ * 100)));
}

void ClockDisc::mouseReleaseEvent(QMouseEvent* event) {
    if (event->button() == Qt::MouseButton::LeftButton && onClick) {
        onClick();
    }
    QWidget::mouseReleaseEvent(event);
}

StatusEmblem::StatusEmblem(QWidget* parent) : QWidget(parent) {
    setFixedSize(84, 84);
    setCursor(Qt::CursorShape::PointingHandCursor);
}

void StatusEmblem::set_status(const std::string& status) {
    if (status != status_) {
        status_ = status;
        update();
    }
}

void StatusEmblem::start() {
    if (timer_ == nullptr) {
        timer_ = new QTimer(this);
        connect(timer_, &QTimer::timeout, this, [this] { tick(); });
    }
    if (animating_) {
        timer_->start(60);
    }
}

void StatusEmblem::set_animating(bool enabled) {
    animating_ = enabled;
    if (timer_ == nullptr) {
        return;
    }
    if (animating_) {
        timer_->start(60);
    } else {
        timer_->stop();
    }
}

void StatusEmblem::tick() {
    elapsed_ = std::fmod(elapsed_ + 0.06, kModeSeconds * 3);
    int mode = static_cast<int>(elapsed_ / kModeSeconds) % 3;
    if (mode != last_mode_) {
        last_mode_ = mode;
        if (mode == 1) {
            blip_angle_ = rand_uniform(14.0, 346.0);
        }
    }
    update();
}

void StatusEmblem::paint_mode(QPainter& painter, const QPointF& center, double radius,
                              int mode, double local, double fade) {
    const Theme& theme = current_theme();
    if (mode == 0) {
        double breath = 0.5 + 0.5 * std::sin(local * kPi * 2.4);
        double ring_radius = radius - 5.0 + 3.5 * breath;
        QColor ring = theme_color(theme.red_bright);
        ring.setAlpha(static_cast<int>((22 + 58 * breath) * fade));
        painter.setPen(round_pen(ring, 1.2 + 0.9 * breath));
        QRectF rect(center.x() - ring_radius, center.y() - ring_radius, ring_radius * 2,
                    ring_radius * 2);
        painter.drawArc(rect, static_cast<int>(40 + local * 180) * 16 % 5760,
                        static_cast<int>(96 * 16));
        double second_radius = ring_radius + 4.0;
        QColor second = theme_color(theme.gold_dim);
        second.setAlpha(static_cast<int>(66 * fade));
        painter.setPen(round_pen(second, 1.0));
        painter.drawArc(QRectF(center.x() - second_radius, center.y() - second_radius,
                               second_radius * 2, second_radius * 2),
                        static_cast<int>(230 - local * 200) * 16 % 5760,
                        static_cast<int>(48 * 16));
        return;
    }

    if (mode == 1) {
        double sweep = local * 360.0 * 1.35;
        double ring_radius = radius - 2.0;
        QRectF rect(center.x() - ring_radius, center.y() - ring_radius, ring_radius * 2,
                    ring_radius * 2);
        for (int index = 0; index < 12; ++index) {
            int alpha = static_cast<int>((96 - index * 7.5) * fade);
            if (alpha <= 0) {
                continue;
            }
            QColor streak = theme_color(theme.red_bright);
            streak.setAlpha(alpha);
            painter.setPen(QPen(streak, index < 2 ? 2.0 : 1.1));
            painter.drawArc(rect, static_cast<int>(std::fmod(sweep - index * 6.0, 360.0)) * 16,
                            static_cast<int>(6 * 16));
        }
        QColor needle = theme_color(theme.red_bright);
        needle.setAlpha(static_cast<int>(90 * fade));
        painter.setPen(QPen(needle, 1.0));
        double angle = sweep * kPi / 180.0;
        painter.drawLine(
            QPointF(center.x() + std::cos(angle) * 9, center.y() + std::sin(angle) * 9),
            QPointF(center.x() + std::cos(angle) * (ring_radius - 1),
                    center.y() + std::sin(angle) * (ring_radius - 1)));

        double delta = std::abs(std::fmod(std::fmod(sweep - blip_angle_, 360.0) + 180.0,
                                          360.0) -
                                180.0);
        if (delta < 100) {
            QColor blip(255, 222, 222);
            blip.setAlpha(static_cast<int>((1.0 - delta / 100) * 198 * fade));
            double cycle = std::floor(elapsed_ / kModeSeconds);
            double blip_distance = 15 + std::fmod(cycle * 37, 11);
            painter.setPen(Qt::PenStyle::NoPen);
            painter.setBrush(blip);
            double ba = blip_angle_ * kPi / 180.0;
            painter.drawEllipse(
                QPointF(center.x() + std::cos(ba) * blip_distance,
                        center.y() + std::sin(ba) * blip_distance),
                2.0, 2.0);
            painter.setBrush(Qt::BrushStyle::NoBrush);
        }
        return;
    }

    for (double offset : {0.0, 0.34, 0.68}) {
        double frac = std::fmod(local + offset, 1.0);
        int alpha = static_cast<int>((1.0 - frac) * 100 * fade);
        if (alpha <= 0) {
            continue;
        }
        QColor ring = theme_color(theme.red_bright);
        ring.setAlpha(alpha);
        painter.setPen(QPen(ring, 1.3 - 0.5 * frac));
        double ring_radius = 7.0 + frac * (radius - 6.0);
        painter.drawEllipse(QRectF(center.x() - ring_radius, center.y() - ring_radius,
                                   ring_radius * 2, ring_radius * 2));
    }
    double pulse = 0.5 + 0.5 * std::sin(local * kPi * 2);
    QColor core = theme_color(theme.red);
    core.setAlpha(static_cast<int>((48 + 68 * pulse) * fade));
    painter.setPen(Qt::PenStyle::NoPen);
    painter.setBrush(core);
    painter.drawEllipse(center, 2.6 + 1.6 * pulse, 2.6 + 1.6 * pulse);
    painter.setBrush(Qt::BrushStyle::NoBrush);
}

void StatusEmblem::paintEvent(QPaintEvent* /*event*/) {
    const Theme& theme = current_theme();
    QPainter painter(this);
    painter.setRenderHint(QPainter::RenderHint::Antialiasing);
    QPointF center(width() / 2.0, height() / 2.0);
    double radius = 31.0;
    painter.setPen(QPen(QColor(255, 255, 255, 24), 1.0));
    painter.setBrush(Qt::BrushStyle::NoBrush);
    painter.drawEllipse(center, radius, radius);

    painter.setPen(QPen(theme_color(theme.gold_dim), 1.0, Qt::PenStyle::DashLine));
    painter.drawEllipse(center, radius + 6, radius + 6);

    for (int index = 0; index < 24; ++index) {
        double angle = index * 15 * kPi / 180.0;
        double inner = radius + 3;
        double outer = radius + (index % 6 ? 6 : 9);
        painter.setPen(QPen(theme_color(theme.faint), 1.0));
        painter.drawLine(QPointF(center.x() + std::cos(angle) * inner,
                                 center.y() + std::sin(angle) * inner),
                         QPointF(center.x() + std::cos(angle) * outer,
                                 center.y() + std::sin(angle) * outer));
    }

    int mode = static_cast<int>(elapsed_ / kModeSeconds) % 3;
    double local = std::fmod(elapsed_, kModeSeconds) / kModeSeconds;
    double fade = std::clamp(std::min(local / 0.14, (1.0 - local) / 0.14), 0.0, 1.0);
    if (fade > 0.01) {
        painter.save();
        paint_mode(painter, center, radius, mode, local, fade);
        painter.restore();
    }

    painter.setPen(QPen(theme_color(theme.text_soft), 1.2));
    for (double offset : {-7.0, 0.0, 7.0}) {
        painter.drawLine(QPointF(center.x() - 8, center.y() + offset),
                         QPointF(center.x() + 8, center.y() + offset));
    }

    painter.setFont(hud_font(7, QFont::Weight::DemiBold, 1.6));
    painter.setPen(theme_color(theme.muted));
    painter.drawText(QRectF(0, height() - 16, width(), 14), Qt::AlignmentFlag::AlignHCenter,
                     QString::fromStdString(status_));
}

void StatusEmblem::mouseReleaseEvent(QMouseEvent* event) {
    if (event->button() == Qt::MouseButton::LeftButton && onClick) {
        onClick();
    }
    QWidget::mouseReleaseEvent(event);
}

SparkBars::SparkBars(const std::string& color, QWidget* parent) : QWidget(parent) {
    const Theme& theme = current_theme();
    color_ = theme_color(color.empty() ? theme.gold : color);
    setFixedHeight(12);
    values_.assign(28, 0.18);
}

void SparkBars::push(double value) {
    values_.push_back(std::clamp(static_cast<double>(value) / 100.0, 0.06, 1.0));
    if (values_.size() > 28) {
        values_.erase(values_.begin());
    }
    update();
}

void SparkBars::paintEvent(QPaintEvent* /*event*/) {
    QPainter painter(this);
    painter.setRenderHint(QPainter::RenderHint::Antialiasing);
    double w = width();
    int count = static_cast<int>(values_.size());
    double gap = 2.0;
    double bar_width = std::max(1.0, (w - gap * (count - 1)) / count);
    for (int index = 0; index < count; ++index) {
        double value = values_[index];
        double h = std::max(1.5, height() * value);
        QColor color = color_;
        color.setAlpha(50 + static_cast<int>(160 * index / count));
        painter.fillRect(
            QRectF(index * (bar_width + gap), height() - h, bar_width, h), color);
    }
}

WeatherIcon::WeatherIcon(const std::string& kind, int size, QWidget* parent)
    : QWidget(parent), kind_(kind) {
    setFixedSize(size, size);
}

void WeatherIcon::set_kind(const std::string& kind) {
    kind_ = kind;
    update();
}

void WeatherIcon::paintEvent(QPaintEvent* /*event*/) {
    const Theme& theme = current_theme();
    QPainter painter(this);
    painter.setRenderHint(QPainter::RenderHint::Antialiasing);
    double side = std::min(width(), height());
    double scale = side / 36.0;
    painter.scale(scale, scale);
    QColor color = theme_color(theme.gold);
    QPen pen = round_pen(color, 1.4);
    painter.setPen(pen);
    painter.setBrush(Qt::BrushStyle::NoBrush);

    if (kind_ == "clear" || kind_ == "partly") {
        painter.drawEllipse(QPointF(18, 15), 6.2, 6.2);
        for (int index = 0; index < 8; ++index) {
            double angle = index * 45 * kPi / 180.0;
            painter.drawLine(QPointF(18 + std::cos(angle) * 9.5, 15 + std::sin(angle) * 9.5),
                             QPointF(18 + std::cos(angle) * 12.5,
                                     15 + std::sin(angle) * 12.5));
        }
        if (kind_ == "clear") {
            return;
        }
    }

    if (kind_ == "cloud" || kind_ == "partly" || kind_ == "fog" || kind_ == "rain" ||
        kind_ == "snow" || kind_ == "storm") {
        QPainterPath path;
        path.moveTo(8, 24);
        path.cubicTo(4, 24, 3, 19, 7, 17);
        path.cubicTo(8, 11, 17, 10, 19, 15);
        path.cubicTo(25, 12, 30, 16, 28, 21);
        path.cubicTo(31, 22, 31, 26, 27, 26);
        path.lineTo(9, 26);
        path.cubicTo(7, 26, 6, 25, 8, 24);
        painter.drawPath(path);
    }

    if (kind_ == "rain" || kind_ == "storm") {
        QColor stroke = theme_color(kind_ == "storm" ? theme.red_soft : theme.gold);
        painter.setPen(QPen(stroke, 1.3));
        for (double x : {11.0, 17.0, 23.0}) {
            painter.drawLine(QPointF(x, 29), QPointF(x - 2, 33));
        }
    }
    if (kind_ == "storm") {
        painter.setPen(QPen(theme_color(theme.red_bright), 1.6));
        QPainterPath path;
        path.moveTo(19, 24);
        path.lineTo(15, 30);
        path.lineTo(19, 30);
        path.lineTo(16, 35);
        painter.drawPath(path);
    }
    if (kind_ == "snow") {
        painter.setPen(QPen(color, 1.1));
        for (double x : {11.0, 18.0, 25.0}) {
            painter.drawLine(QPointF(x - 2, 31), QPointF(x + 2, 31));
            painter.drawLine(QPointF(x, 29), QPointF(x, 33));
        }
    }
    if (kind_ == "fog") {
        painter.setPen(QPen(theme_color(theme.muted), 1.1));
        for (int y : {29, 32, 35}) {
            painter.drawLine(QPointF(8 + (y - 29) * 2, y), QPointF(28, y));
        }
    }
}

TempRangeBar::TempRangeBar(QWidget* parent) : QWidget(parent) {
    setFixedHeight(14);
}

void TempRangeBar::set_range(double low, double high, double current) {
    low_ = low;
    high_ = high;
    current_ = current;
    update();
}

void TempRangeBar::paintEvent(QPaintEvent* /*event*/) {
    const Theme& theme = current_theme();
    QPainter painter(this);
    painter.setRenderHint(QPainter::RenderHint::Antialiasing);
    double y = height() / 2.0;
    painter.setPen(QPen(theme_color(theme.faint), 1.0));
    painter.drawLine(QPointF(0, y), QPointF(width(), y));
    double span = std::max(1.0, high_ - low_);
    double pos = std::clamp((current_ - low_) / span, 0.0, 1.0);
    double x = 4 + pos * (width() - 8);
    painter.setPen(Qt::PenStyle::NoPen);
    painter.setBrush(theme_color(theme.gold));
    painter.drawEllipse(QPointF(x, y), 3.4, 3.4);
    QColor glow = theme_color(theme.gold);
    glow.setAlpha(45);
    painter.setBrush(glow);
    painter.drawEllipse(QPointF(x, y), 7.0, 7.0);
}

TempCurve::TempCurve(QWidget* parent) : QWidget(parent) {
    setMinimumHeight(150);
}

void TempCurve::set_hours(const std::vector<HourPoint>& hours) {
    hours_ = hours;
    update();
}

void TempCurve::paintEvent(QPaintEvent* /*event*/) {
    const Theme& theme = current_theme();
    QPainter painter(this);
    painter.setRenderHint(QPainter::RenderHint::Antialiasing);
    if (hours_.empty()) {
        painter.setPen(theme_color(theme.dim));
        painter.setFont(hud_font(9));
        painter.drawText(rect(), Qt::AlignmentFlag::AlignCenter,
                         QStringLiteral("Saatlik veri bekleniyor…"));
        return;
    }

    double left = 26.0;
    double right = 26.0;
    double top = 30.0;
    double bottom = 54.0;
    double w = std::max(10.0, width() - left - right);
    double h = std::max(10.0, height() - top - bottom);

    double low = hours_[0].temp;
    double high = hours_[0].temp;
    for (const auto& hour : hours_) {
        low = std::min(low, hour.temp);
        high = std::max(high, hour.temp);
    }
    low -= 2;
    high += 2;
    double span = std::max(1.0, high - low);

    painter.setPen(QPen(QColor(255, 255, 255, 12), 1, Qt::PenStyle::DashLine));
    for (double fraction : {0.0, 0.5, 1.0}) {
        double y = top + h * fraction;
        painter.drawLine(QPointF(left, y), QPointF(left + w, y));
    }

    std::vector<QPointF> points;
    double step = w / std::max(1, static_cast<int>(hours_.size()) - 1);
    for (size_t index = 0; index < hours_.size(); ++index) {
        double x = left + index * step;
        double y = top + h * (1 - (hours_[index].temp - low) / span);
        points.emplace_back(x, y);
    }

    QPainterPath fill;
    fill.moveTo(points.front().x(), top + h);
    for (const auto& point : points) {
        fill.lineTo(point);
    }
    fill.lineTo(points.back().x(), top + h);
    fill.closeSubpath();
    QLinearGradient gradient(0, top, 0, top + h);
    gradient.setColorAt(0.0, QColor(232, 57, 79, 55));
    gradient.setColorAt(1.0, QColor(232, 57, 79, 0));
    painter.fillPath(fill, QBrush(gradient));

    QPainterPath line;
    line.moveTo(points.front());
    for (size_t index = 1; index < points.size(); ++index) {
        const QPointF& prev = points[index - 1];
        const QPointF& cur = points[index];
        double mid = (prev.x() + cur.x()) / 2;
        line.cubicTo(QPointF(mid, prev.y()), QPointF(mid, cur.y()), cur);
    }
    painter.setPen(round_pen(QColor(232, 57, 79, 55), 5.0));
    painter.drawPath(line);
    painter.setPen(round_pen(theme_color(theme.red), 1.6));
    painter.drawPath(line);

    for (size_t index = 0; index < points.size(); ++index) {
        const QPointF& point = points[index];
        const HourPoint& hour = hours_[index];
        painter.setPen(Qt::PenStyle::NoPen);
        painter.setBrush(theme_color(theme.background));
        painter.drawEllipse(point, 3.6, 3.6);
        painter.setBrush(theme_color(theme.text));
        painter.drawEllipse(point, 2.1, 2.1);

        painter.setFont(hud_font(7, QFont::Weight::DemiBold, 0.8));
        painter.setPen(theme_color(theme.text_soft));
        painter.drawText(QRectF(point.x() - 28, point.y() - 22, 56, 14),
                         Qt::AlignmentFlag::AlignHCenter,
                         QStringLiteral("%1°").arg(static_cast<int>(std::lround(hour.temp))));
        if (hour.precip) {
            double bar_height = std::max(2.0, (hour.precip / 100.0) * 16.0);
            painter.setPen(Qt::PenStyle::NoPen);
            painter.setBrush(theme_color(theme.gold_dim));
            painter.drawRect(QRectF(point.x() - 5, top + h + 25 - bar_height, 10, bar_height));
            painter.setPen(theme_color(theme.gold));
            painter.setFont(hud_font(6));
            painter.drawText(QRectF(point.x() - 24, top + h + 6, 48, 12),
                             Qt::AlignmentFlag::AlignHCenter,
                             QStringLiteral("%%1").arg(hour.precip));
        }
        painter.setPen(theme_color(theme.dim));
        painter.setFont(hud_font(6, QFont::Weight::Medium, 0.6));
        painter.drawText(QRectF(point.x() - 30, height() - 20, 60, 14),
                         Qt::AlignmentFlag::AlignHCenter,
                         QString::fromStdString(hour.label));
    }
}

MiniMap::MiniMap(QWidget* parent) : QWidget(parent) {
    setCursor(Qt::CursorShape::PointingHandCursor);
    setMinimumHeight(130);
    setSizePolicy(QSizePolicy::Policy::Expanding, QSizePolicy::Policy::Preferred);
    timer_ = new QTimer(this);
    connect(timer_, &QTimer::timeout, this, [this] { tick(); });
    timer_->start(220);
}

void MiniMap::set_animating(bool enabled) {
    animating_ = enabled;
    if (animating_ && isVisible()) {
        timer_->start(220);
    } else {
        timer_->stop();
    }
}

void MiniMap::set_home(const HomeLocation& home) {
    home_ = home;
    update();
}

void MiniMap::tick() {
    phase_ = std::fmod(phase_ + 0.04, 1.0);
    update();
}

void MiniMap::paintEvent(QPaintEvent* /*event*/) {
    const Theme& theme = current_theme();
    QPainter painter(this);
    painter.setRenderHint(QPainter::RenderHint::Antialiasing);
    QRectF rect(this->rect());
    QLinearGradient gradient(0, 0, rect.width(), rect.height());
    if (theme.is_light) {
        gradient.setColorAt(0, QColor("#f7f7f9"));
        gradient.setColorAt(1, QColor("#ececef"));
    } else {
        gradient.setColorAt(0, QColor("#0d0507"));
        gradient.setColorAt(1, QColor("#12070a"));
    }
    QColor water_fill = theme.is_light ? QColor("#e2e2e8") : QColor("#050507");
    painter.fillRect(rect, gradient);

    QColor contour = theme_color(theme.red);
    contour.setAlpha(30);
    QColor grid = theme_color(theme.red);
    grid.setAlpha(22);
    QColor water_edge = theme_color(theme.red);
    water_edge.setAlpha(55);
    QColor gold = theme_color(theme.gold);
    gold.setAlpha(55);
    QColor frame = theme.is_light ? theme_color(theme.border) : QColor(255, 255, 255, 22);

    painter.save();
    painter.setClipRect(rect);
    painter.setPen(QPen(contour, 1.0));
    for (int index = 0; index < 7; ++index) {
        QPainterPath path;
        double y_base = rect.height() * (0.18 + index * 0.11);
        path.moveTo(0, y_base);
        for (int step = 0; step < 11; ++step) {
            double x = rect.width() * step / 10.0;
            double y = y_base + std::sin(step * 1.2 + index) * 8;
            path.lineTo(x, y);
        }
        painter.drawPath(path);
    }

    painter.setPen(QPen(grid, 1.0, Qt::PenStyle::DashLine));
    for (int index = 0; index < 5; ++index) {
        double x = rect.width() * (0.15 + index * 0.18);
        painter.drawLine(QPointF(x, 0), QPointF(x + 30, rect.height()));
    }

    QPainterPath water;
    water.moveTo(rect.width() * 0.66, 0);
    water.cubicTo(rect.width() * 0.58, rect.height() * 0.35, rect.width() * 0.83,
                  rect.height() * 0.6, rect.width() * 0.75, rect.height());
    water.lineTo(rect.width(), rect.height());
    water.lineTo(rect.width(), 0);
    water.closeSubpath();
    painter.fillPath(water, water_fill);
    painter.setPen(QPen(water_edge, 1.2));
    painter.drawPath(water);

    painter.setPen(QPen(gold, 1.0));
    painter.drawLine(QPointF(0, rect.height() * 0.62), QPointF(rect.width(), rect.height() * 0.5));
    painter.drawLine(QPointF(rect.width() * 0.2, 0), QPointF(rect.width() * 0.35, rect.height()));

    QPointF point(rect.width() * 0.68, rect.height() * 0.44);
    double pulse = 0.5 + 0.5 * std::sin(phase_ * kPi * 2);
    QColor pulse_color = theme_color(theme.red_bright);
    pulse_color.setAlpha(static_cast<int>(70 + 80 * pulse));
    painter.setPen(QPen(pulse_color, 1.0));
    painter.setBrush(Qt::BrushStyle::NoBrush);
    painter.drawEllipse(point, 7 + pulse * 6, 7 + pulse * 6);
    painter.setPen(QPen(theme_color(theme.red_bright), 1.4));
    painter.drawEllipse(point, 3.2, 3.2);
    painter.setPen(theme_color(theme.text));
    painter.setFont(hud_font(7, QFont::Weight::DemiBold, 1.2));
    QString home_name = QString::fromStdString(home_.name).toUpper().left(18);
    painter.drawText(QRectF(point.x() + 8, point.y() - 10, rect.width() - point.x() - 12, 14),
                     Qt::AlignmentFlag::AlignLeft, home_name);
    painter.setPen(theme_color(theme.muted));
    painter.setFont(hud_font(6, QFont::Weight::Medium, 1.0));
    painter.drawText(QRectF(8, rect.height() - 18, 140, 12), Qt::AlignmentFlag::AlignLeft,
                     QString::fromStdString(home_.region).toUpper().left(16));
    painter.drawText(QRectF(rect.width() - 150, rect.height() - 18, 142, 12),
                     Qt::AlignmentFlag::AlignRight,
                     QStringLiteral("%1° · %2°")
                         .arg(home_.lat, 0, 'f', 3)
                         .arg(home_.lon, 0, 'f', 3));

    painter.setPen(QPen(frame, 1.0));
    painter.setBrush(Qt::BrushStyle::NoBrush);
    painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5));
    painter.restore();
}

void MiniMap::mouseReleaseEvent(QMouseEvent* event) {
    if (event->button() == Qt::MouseButton::LeftButton && onClick) {
        onClick();
    }
    QWidget::mouseReleaseEvent(event);
}

void MiniMap::showEvent(QShowEvent* event) {
    QWidget::showEvent(event);
    if (animating_) {
        timer_->start(220);
    }
}

void MiniMap::hideEvent(QHideEvent* event) {
    timer_->stop();
    QWidget::hideEvent(event);
}

MetricCard::MetricCard(const std::string& title, const std::string& value,
                       const std::string& note, bool meter, QWidget* parent)
    : QFrame(parent) {
    const Theme& theme = current_theme();
    setObjectName("metricCard");
    setStyleSheet(QStringLiteral("QFrame#metricCard{background:%1;border:1px solid %2;border-radius:8px;}")
                      .arg(QString::fromStdString(theme.panel_2),
                           QString::fromStdString(theme.border)));
    auto* layout = new QVBoxLayout(this);
    layout->setContentsMargins(12, 10, 12, 10);
    layout->setSpacing(4);
    layout->addWidget(hud_label(title, 7, theme.dim, QFont::Weight::DemiBold, 1.8, true));
    value_ = hud_label(value, 15, theme.text, QFont::Weight::DemiBold, 0.8);
    note_ = hud_label(note, 7, theme.muted, QFont::Weight::Medium, 0.8);
    layout->addWidget(value_);
    if (meter) {
        meter_ = new QFrame();
        meter_->setFixedHeight(2);
        meter_->setStyleSheet(
            QStringLiteral("background:%1;border:none;").arg(QString::fromStdString(theme.red_dim)));
        layout->addWidget(meter_);
    }
    layout->addWidget(note_);
    layout->addStretch();
}

void MetricCard::set(const std::string& value, const std::string& note, double percent) {
    value_->setText(QString::fromStdString(value));
    note_->setText(QString::fromStdString(note));
    if (meter_ != nullptr && percent >= 0.0) {
        int w = std::max(2, static_cast<int>(width() * std::clamp(percent, 0.0, 100.0) / 100.0));
        meter_->setFixedWidth(w);
    }
}

MeterBar::MeterBar(int value, int height, QWidget* parent) : QProgressBar(parent) {
    const Theme& theme = current_theme();
    setRange(0, 100);
    setValue(std::clamp(value, 0, 100));
    setTextVisible(false);
    setFixedHeight(height);
    setStyleSheet(QStringLiteral("QProgressBar{background:%1;border:none;border-radius:%2px;}"
                                 "QProgressBar::chunk{background:%3;border-radius:%2px;}")
                      .arg(QString::fromStdString(theme.faint))
                      .arg(height / 2)
                      .arg(QString::fromStdString(theme.red)));
}

void MeterBar::set_value(double value) {
    setValue(static_cast<int>(std::clamp(value, 0.0, 100.0)));
}

EmptyState::EmptyState(const std::string& text, QWidget* parent) : QLabel(parent) {
    const Theme& theme = current_theme();
    setFont(hud_font(9, QFont::Weight::Medium, 0.6));
    setStyleSheet(
        QStringLiteral("color:%1;background:transparent;border:none;").arg(QString::fromStdString(theme.dim)));
    setWordWrap(true);
    setTextFormat(Qt::TextFormat::PlainText);
    setText(QString::fromStdString(text));
}

ScanLine::ScanLine(QWidget* parent) : QWidget(parent) {
    setFixedHeight(3);
    setAttribute(Qt::WidgetAttribute::WA_TransparentForMouseEvents, true);
    wait_ = rand_uniform(1.0, 2.6);
    timer_ = new QTimer(this);
    timer_->setInterval(40);
    connect(timer_, &QTimer::timeout, this, [this] { advance(); });
}

void ScanLine::set_animating(bool enabled) {
    animating_ = enabled;
    if (animating_ && isVisible()) {
        timer_->start();
    } else {
        timer_->stop();
    }
}

void ScanLine::showEvent(QShowEvent* event) {
    QWidget::showEvent(event);
    if (animating_) {
        timer_->start();
    }
}

void ScanLine::hideEvent(QHideEvent* event) {
    timer_->stop();
    QWidget::hideEvent(event);
}

double ScanLine::head_position(double progress) const {
    if (direction_ >= 0) {
        return progress * width();
    }
    return (1.0 - progress) * width();
}

void ScanLine::advance() {
    if (window() != nullptr && window()->isMinimized()) {
        return;
    }
    constexpr double step = 0.04;
    if (!active_) {
        wait_ -= step;
        if (ghost_life_ > 0.0) {
            ghost_life_ = std::max(0.0, ghost_life_ - step * 0.22);
        }
        if (wait_ <= 0.0) {
            active_ = true;
            progress_ = 0.0;
            direction_ = rand_uniform(0.0, 1.0) < 0.5 ? 1 : -1;
            speed_ = rand_uniform(0.010, 0.022);
        }
        update();
        return;
    }
    progress_ += speed_;
    if (progress_ >= 1.0) {
        progress_ = 1.0;
        active_ = false;
        ghost_x_ = head_position(1.0);
        ghost_life_ = 1.0;
        wait_ = rand_uniform(2.6, 6.0);
    }
    update();
}

void ScanLine::paintEvent(QPaintEvent* /*event*/) {
    const Theme& theme = current_theme();
    QPainter painter(this);
    painter.setRenderHint(QPainter::RenderHint::Antialiasing);
    double mid = height() / 2.0;
    painter.setPen(QPen(theme_color(theme.border), 1));
    painter.drawLine(0, static_cast<int>(mid), width(), static_cast<int>(mid));

    if (ghost_life_ > 0.0 && ghost_x_ >= 0.0) {
        QColor ghost = theme_color(theme.red_bright);
        ghost.setAlpha(static_cast<int>(58 * ghost_life_));
        painter.setPen(QPen(ghost, 1.4));
        painter.drawPoint(
            QPointF(std::clamp(ghost_x_, 1.0, static_cast<double>(width() - 1)), mid));
    }

    if (!active_) {
        return;
    }

    double strength = std::min({1.0, progress_ * 12.0, (1.0 - progress_) * 12.0});
    if (strength <= 0.01) {
        return;
    }

    double head = head_position(progress_);
    double span = std::max(70.0, width() * 0.14);
    QLinearGradient gradient(head - span, 0, head + span, 0);
    QColor start = theme_color(theme.red_bright);
    start.setAlpha(0);
    QColor glow = theme_color(theme.red_bright);
    glow.setAlpha(static_cast<int>(214 * strength));
    QColor fade = theme_color(theme.red_bright);
    fade.setAlpha(0);
    gradient.setColorAt(0.0, start);
    gradient.setColorAt(0.5, glow);
    gradient.setColorAt(1.0, fade);
    painter.setPen(QPen(QBrush(gradient), 1.6));
    painter.drawLine(QPointF(head - span, mid), QPointF(head + span, mid));

    QColor dot = theme_color(theme.red_bright);
    dot.setAlpha(static_cast<int>(232 * strength));
    painter.setPen(Qt::PenStyle::NoPen);
    painter.setBrush(dot);
    painter.drawEllipse(QPointF(head, mid), 1.6, 1.6);
}

}  // namespace jarvis::ui
