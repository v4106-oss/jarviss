#include <jarvis/ui/floating_hud.hpp>

#include <QAction>
#include <QColor>
#include <QContextMenuEvent>
#include <QDateTime>
#include <QFont>
#include <QFontMetrics>
#include <QGuiApplication>
#include <QLinearGradient>
#include <QMenu>
#include <QMouseEvent>
#include <QPainter>
#include <QPainterPath>
#include <QPen>
#include <QPushButton>
#include <QRadialGradient>
#include <QScreen>
#include <QTimer>

#include <algorithm>
#include <cmath>
#include <string>

#include <jarvis/core/settings.hpp>
#include <jarvis/ui/orb.hpp>
#include <jarvis/ui/theme.hpp>

namespace jarvis::ui {

namespace {

constexpr double kPi = 3.14159265358979323846;
constexpr int kCardWidth = 384;
constexpr int kCardHeight = 186;
constexpr int kEdgeMargin = 24;
constexpr int kSnapDistance = 22;
constexpr int kActiveIntervalMs = 33;
constexpr int kIdleIntervalMs = 83;
constexpr int kMaxSubtitle = 240;

constexpr double kSurfaceInset = 5.0;
constexpr double kSurfaceRadius = 20.0;
constexpr double kContentLeft = 22.0;
constexpr double kOrbX = 41.0;
constexpr double kOrbY = 45.0;
constexpr double kTextX = 80.0;
constexpr double kSubY = 76.0;
constexpr double kDividerY = 102.0;
constexpr double kChipY = 110.0;
constexpr double kChipHeight = 23.0;
constexpr double kWaveY = 153.0;

QColor mix_color(const QColor& base, const QColor& target, double amount) {
    const double t = std::clamp(amount, 0.0, 1.0);
    return QColor(int(base.red() + (target.red() - base.red()) * t),
                  int(base.green() + (target.green() - base.green()) * t),
                  int(base.blue() + (target.blue() - base.blue()) * t));
}

QColor tint(const QColor& color, int alpha) {
    QColor out = color;
    out.setAlpha(std::clamp(alpha, 0, 255));
    return out;
}

std::string compact_text(const std::string& value, std::size_t limit) {
    std::string text;
    bool in_space = false;
    for (const char c : value) {
        if (c == ' ' || c == '\t' || c == '\n' || c == '\r') {
            if (!in_space && !text.empty()) text += ' ';
            in_space = true;
        } else {
            text += c;
            in_space = false;
        }
    }
    if (text.size() > limit) {
        text = text.substr(0, std::max<std::size_t>(1, limit - 3));
        while (!text.empty() && text.back() == ' ') text.pop_back();
        text += "...";
    }
    return text;
}

struct StateInfo {
    const char* label;
    std::string hint;
};

const std::string& state_label(const std::string& state) {
    static const std::string kIdle = "HAZIR";
    static const std::string kListening = "DİNLİYOR";
    static const std::string kThinking = "DÜŞÜNÜYOR";
    static const std::string kSpeaking = "KONUŞUYOR";
    static const std::string kSleeping = "UYKU MODU";
    static const std::string kError = "HATA";
    if (state == "listening") return kListening;
    if (state == "thinking") return kThinking;
    if (state == "speaking") return kSpeaking;
    if (state == "sleeping") return kSleeping;
    if (state == "error") return kError;
    return kIdle;
}

const std::string& state_hint(const std::string& state) {
    static const std::string kIdle = "SİSTEM HAZIR · KOMUT BEKLİYOR";
    static const std::string kListening = "SENİ DİNLİYORUM…";
    static const std::string kThinking = "GÖREV ADIM ADIM YÜRÜTÜLÜYOR…";
    static const std::string kSpeaking = "SESLİ YANIT VERİYORUM…";
    static const std::string kSleeping = "UYKU MODUNDA · SESSİZCE BEKLİYORUM";
    static const std::string kError = "BİR HATA OLUŞTU · AYRINTI ANA PENCEREDE";
    if (state == "listening") return kListening;
    if (state == "thinking") return kThinking;
    if (state == "speaking") return kSpeaking;
    if (state == "sleeping") return kSleeping;
    if (state == "error") return kError;
    return kIdle;
}

bool is_active_state(const std::string& state) {
    return state == "listening" || state == "thinking" || state == "speaking" || state == "error";
}

}  // namespace

FloatingHud::FloatingHud(jarvis::core::Settings* settings) : settings_(settings) {
    setWindowTitle(QString::fromUtf8("Jarvis HUD"));
    setAccessibleName(QString::fromUtf8("Jarvis yüzen HUD"));
    setWindowFlags(Qt::Tool | Qt::FramelessWindowHint | Qt::WindowStaysOnTopHint |
                   Qt::NoDropShadowWindowHint);
    setAttribute(Qt::WA_TranslucentBackground, true);
    setAttribute(Qt::WA_ShowWithoutActivating, true);
    setAttribute(Qt::WA_QuitOnClose, false);
    setFocusPolicy(Qt::NoFocus);
    setMouseTracking(true);
    setFixedSize(kCardWidth, kCardHeight);
    setWindowOpacity(0.985);
    setCursor(Qt::OpenHandCursor);
    setToolTip(QString::fromUtf8("Çift tıkla ana pencereye dön · sürükleyerek taşı"));

    const Theme& theme = current_theme();
    close_button_ = new QPushButton(QString::fromUtf8("×"), this);
    close_button_->setFixedSize(22, 22);
    close_button_->move(kCardWidth - 40, 14);
    close_button_->setCursor(Qt::PointingHandCursor);
    close_button_->setFocusPolicy(Qt::NoFocus);
    close_button_->setToolTip(QString::fromUtf8("HUD'ı gizle"));
    close_button_->setAccessibleName(QString::fromUtf8("HUD'ı gizle"));
    close_button_->setStyleSheet(
        QStringLiteral("QPushButton{background:%1;color:%2;border:1px solid %3;border-radius:11px;"
                       "padding:0;font-family:\"%4\";font-size:13px;}")
            .arg(QString::fromStdString(rgba_css(theme.text, 0.035)),
                 QString::fromStdString(theme.muted),
                 QString::fromStdString(rgba_css(theme.text, 0.08)),
                 QString::fromStdString(theme.font)) +
        QStringLiteral("QPushButton:hover{color:%1;border-color:%2;background:%3;}")
            .arg(QString::fromStdString(theme.red_bright), QString::fromStdString(theme.red_soft),
                 QString::fromStdString(rgba_css(theme.red, 0.15))));
    QObject::connect(close_button_, &QPushButton::clicked, this, [this] { dismiss(); });

    timer_ = new QTimer(this);
    QObject::connect(timer_, &QTimer::timeout, this, [this] { tick(); });
}

void FloatingHud::set_state(const std::string& state) {
    state_ = (state == "idle" || state == "listening" || state == "thinking" ||
              state == "speaking" || state == "sleeping" || state == "error")
                 ? state
                 : "idle";
    sync_timer();
    update();
}

void FloatingHud::set_subtitle(const std::string& text, const std::string& sender) {
    subtitle_ = compact_text(text, kMaxSubtitle);
    subtitle_sender_ = (sender == "SEN") ? "SEN" : "JARVIS";
    if (isVisible()) update();
}

void FloatingHud::set_camera(const std::string& text, int count, bool active) {
    std::string scene = compact_text(text, 160);
    if (!scene.empty() && (scene[0] == '{' || scene[0] == '[')) {
        scene.clear();
    }
    camera_text_ = scene;
    camera_count_ = std::max(0, count);
    camera_active_ = active;
    if (isVisible()) update();
}

void FloatingHud::set_mic_enabled(bool enabled) {
    if (mic_enabled_ == enabled) return;
    mic_enabled_ = enabled;
    if (!enabled) mic_level_ = 0.0;
    if (isVisible()) update();
}

void FloatingHud::set_mic_level(double level) {
    const double value = std::clamp(level, 0.0, 1.0);
    if (std::fabs(value - mic_level_) < 0.015) return;
    mic_level_ = value;
    if (isVisible()) update();
}

void FloatingHud::show_for_minimize() {
    if (!position_checked_) {
        reposition();
    } else {
        ensure_on_screen();
    }
    const bool appeared = !isVisible();
    show();
    raise();
    Q_UNUSED(appeared);
}

void FloatingHud::hide_for_restore() { hide(); }

void FloatingHud::dismiss() { hide(); }

void FloatingHud::reposition() {
    if (settings_ != nullptr) {
        const auto stored = settings_->floating_hud_position();
        if (stored.has_value()) {
            const QPoint p(stored->first, stored->second);
            const QPoint center = p + QPoint(kCardWidth / 2, kCardHeight / 2);
            QScreen* screen = QGuiApplication::screenAt(center);
            if (screen != nullptr) {
                const QRect visible =
                    screen->availableGeometry().intersected(QRect(p, QSize(kCardWidth, kCardHeight)));
                if (visible.width() >= 120 && visible.height() >= 60) {
                    move(p);
                    position_checked_ = true;
                    return;
                }
            }
        }
    }
    move_to_default();
    save_position();
    position_checked_ = true;
}

void FloatingHud::reset_position() {
    move_to_default();
    save_position();
}

void FloatingHud::move_to_default() {
    QScreen* screen = QGuiApplication::primaryScreen();
    if (screen == nullptr) return;
    const QRect area = screen->availableGeometry();
    const int x = area.right() - width() - kEdgeMargin;
    const int y = area.top() + kEdgeMargin;
    move(std::max(area.left(), x), std::max(area.top(), y));
}

void FloatingHud::ensure_on_screen() {
    const QPoint center = QPoint(x(), y()) + QPoint(kCardWidth / 2, kCardHeight / 2);
    QScreen* screen = QGuiApplication::screenAt(center);
    if (screen == nullptr) {
        move_to_default();
        save_position();
        return;
    }
    const QRect visible =
        screen->availableGeometry().intersected(QRect(QPoint(x(), y()), QSize(kCardWidth, kCardHeight)));
    if (visible.width() < 120 || visible.height() < 60) {
        move_to_default();
        save_position();
    }
}

void FloatingHud::save_position() {
    if (settings_ != nullptr) {
        settings_->save_floating_hud_position(x(), y());
    }
}

void FloatingHud::snap_to_screen() {
    QScreen* screen = QGuiApplication::screenAt(frameGeometry().center());
    if (screen == nullptr) screen = QGuiApplication::primaryScreen();
    if (screen == nullptr) return;
    const QRect area = screen->availableGeometry();
    int x = this->x();
    int y = this->y();
    if (std::abs(x - area.left()) <= kSnapDistance) {
        x = area.left();
    } else if (std::abs(x + width() - area.right()) <= kSnapDistance) {
        x = area.right() - width();
    }
    if (std::abs(y - area.top()) <= kSnapDistance) {
        y = area.top();
    } else if (std::abs(y + height() - area.bottom()) <= kSnapDistance) {
        y = area.bottom() - height();
    }
    x = std::max(area.left(), std::min(x, area.right() - width()));
    y = std::max(area.top(), std::min(y, area.bottom() - height()));
    move(x, y);
}

int FloatingHud::interval() const {
    return is_active_state(state_) ? kActiveIntervalMs : kIdleIntervalMs;
}

void FloatingHud::sync_timer() {
    if (animating_ && isVisible()) {
        timer_->start(interval());
    } else {
        timer_->stop();
    }
}

void FloatingHud::showEvent(QShowEvent* event) {
    QWidget::showEvent(event);
    if (!position_checked_) reposition();
    sync_timer();
}

void FloatingHud::hideEvent(QHideEvent* event) {
    timer_->stop();
    QWidget::hideEvent(event);
}

void FloatingHud::enterEvent(QEnterEvent* event) {
    hover_ = true;
    update();
    QWidget::enterEvent(event);
}

void FloatingHud::leaveEvent(QEvent* event) {
    hover_ = false;
    update();
    QWidget::leaveEvent(event);
}

void FloatingHud::tick() {
    double step = (state_ == "listening" || state_ == "speaking") ? 0.026 : 0.018;
    if (state_ == "thinking") step = 0.032;
    phase_ = std::fmod(phase_ + step, 2.0 * kPi);
    const double spin_step = (state_ == "thinking" || state_ == "listening") ? 0.52 : 0.18;
    spin_ = std::fmod(spin_ + spin_step, 2.0 * kPi);
    pulse_ = 0.5 + 0.5 * std::sin(phase_ * 2.1);
    update();
}

void FloatingHud::paintEvent(QPaintEvent* event) {
    Q_UNUSED(event);
    const int w = width();
    const int h = height();
    if (w <= 0 || h <= 0) return;
    QPainter painter(this);
    painter.setRenderHint(QPainter::Antialiasing, true);
    painter.setRenderHint(QPainter::TextAntialiasing, true);

    const QColor color = state_color(state_);
    const QRectF card(kSurfaceInset + 0.5, kSurfaceInset + 0.5,
                      w - kSurfaceInset * 2 - 1.0, h - kSurfaceInset * 2 - 1.0);
    paint_surface(painter, card, color);

    QPainterPath clip;
    clip.addRoundedRect(card, kSurfaceRadius, kSurfaceRadius);
    painter.save();
    painter.setClipPath(clip);
    paint_sweep(painter, card, color);
    paint_corner_ticks(painter, card, color);
    paint_orb(painter, card, color);
    paint_header(painter, card, color);
    paint_subtitle(painter, color);
    paint_divider(painter, color);
    paint_status_chips(painter);
    paint_waveform(painter, color);
    paint_footer(painter);
    painter.restore();
}

void FloatingHud::paint_surface(QPainter& painter, const QRectF& card, const QColor& color) {
    painter.save();
    painter.setPen(Qt::NoPen);
    const double growths[3] = {5.0, 3.2, 1.6};
    const int alphas[3] = {14, 22, 30};
    for (int i = 0; i < 3; ++i) {
        const double growth = growths[i];
        const QRectF halo = card.adjusted(-growth, -growth + 1.0, growth, growth + 1.0);
        painter.setBrush(QColor(0, 0, 0, alphas[i]));
        painter.drawRoundedRect(halo, kSurfaceRadius + growth, kSurfaceRadius + growth);
    }

    QLinearGradient background(card.topLeft(), card.bottomRight());
    background.setColorAt(0.0, QColor(23, 19, 26, 253));
    background.setColorAt(0.46, QColor(11, 11, 16, 253));
    background.setColorAt(1.0, QColor(5, 5, 8, 254));
    painter.setBrush(QBrush(background));
    painter.drawRoundedRect(card, kSurfaceRadius, kSurfaceRadius);

    QLinearGradient sheen(card.left(), card.top(), card.left(), card.top() + 78);
    sheen.setColorAt(0.0, QColor(255, 255, 255, 16));
    sheen.setColorAt(1.0, QColor(255, 255, 255, 0));
    QPainterPath sheen_clip;
    sheen_clip.addRoundedRect(card, kSurfaceRadius, kSurfaceRadius);
    painter.save();
    painter.setClipPath(sheen_clip);
    painter.setBrush(QBrush(sheen));
    painter.drawRect(QRectF(card.left(), card.top(), card.width(), 78));
    painter.restore();

    const QPointF glow_center(card.left() + kOrbX, card.top() + kOrbY - 4.0);
    QRadialGradient glow(glow_center, 168.0);
    const bool active = is_active_state(state_);
    const int center_alpha = active ? 44 + int(16 * pulse_) : 30;
    glow.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), center_alpha));
    glow.setColorAt(0.5, QColor(color.red(), color.green(), color.blue(), 11));
    glow.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0));
    painter.setBrush(QBrush(glow));
    painter.drawRoundedRect(card, kSurfaceRadius, kSurfaceRadius);

    QLinearGradient border(card.topLeft(), card.bottomRight());
    int base_alpha = active ? (124 + int(40 * pulse_)) : 92;
    if (hover_) base_alpha = std::min(255, base_alpha + 22);
    border.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), base_alpha));
    border.setColorAt(0.55, QColor(color.red(), color.green(), color.blue(), 42));
    border.setColorAt(1.0, QColor(255, 255, 255, 18));
    painter.setBrush(Qt::NoBrush);
    painter.setPen(QPen(QBrush(border), 1.1));
    painter.drawRoundedRect(card, kSurfaceRadius, kSurfaceRadius);

    painter.setPen(QPen(QColor(255, 255, 255, 10), 1.0));
    painter.drawRoundedRect(card.adjusted(1.4, 1.4, -1.4, -1.4), kSurfaceRadius - 1.4,
                            kSurfaceRadius - 1.4);
    painter.restore();
}

void FloatingHud::paint_sweep(QPainter& painter, const QRectF& card, const QColor& color) {
    const double span = 88.0;
    const double travel = std::fmod(phase_ / (2.0 * kPi), 1.0);
    const double start = card.left() + 14.0 - span + travel * (card.width() - 28.0 + span * 2.0);
    const double left = std::max(card.left() + 14.0, start);
    const double right = std::min(card.right() - 14.0, start + span);
    if (right - left < 8.0) return;
    QLinearGradient gradient(start, 0, start + span, 0);
    int alpha = is_active_state(state_) ? 130 : 40;
    if (hover_) alpha += 24;
    gradient.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), 0));
    gradient.setColorAt(0.5, QColor(color.red(), color.green(), color.blue(), alpha));
    gradient.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0));
    painter.save();
    QPen pen(QBrush(gradient), 1.6);
    pen.setCapStyle(Qt::RoundCap);
    painter.setPen(pen);
    painter.drawLine(QPointF(left, card.top() + 0.9), QPointF(right, card.top() + 0.9));
    painter.restore();
}

void FloatingHud::paint_corner_ticks(QPainter& painter, const QRectF& card, const QColor& color) {
    painter.save();
    painter.setBrush(Qt::NoBrush);
    painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 58), 1.0));
    const double length = 9.0;
    const double inset = 9.0;
    const double x1 = card.left() + inset;
    const double y1 = card.top() + inset;
    const double x2 = card.left() + inset;
    const double y2 = card.bottom() - inset;
    painter.drawLine(QPointF(x1, y1), QPointF(x1 + length, y1));
    painter.drawLine(QPointF(x1, y1), QPointF(x1, y1 + length));
    painter.drawLine(QPointF(x2 - length, y2), QPointF(x2, y2));
    painter.drawLine(QPointF(x2, y2 - length), QPointF(x2, y2));
    painter.restore();
}

void FloatingHud::paint_orb(QPainter& painter, const QRectF& card, const QColor& color) {
    painter.save();
    const QPointF center(card.left() + kOrbX, card.top() + kOrbY);
    const bool active = is_active_state(state_);
    const double breathing =
        1.0 + ((state_ == "listening" || state_ == "speaking") ? 0.13 : 0.05) * pulse_;

    QRadialGradient halo(center, 46.0);
    halo.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), 34));
    halo.setColorAt(0.55, QColor(color.red(), color.green(), color.blue(), 8));
    halo.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0));
    painter.setPen(Qt::NoPen);
    painter.setBrush(QBrush(halo));
    painter.drawEllipse(center, 46.0, 46.0);

    painter.setBrush(Qt::NoBrush);
    painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 34), 1.0));
    painter.drawEllipse(center, 27.0, 27.0);
    QPen arc_pen(QColor(color.red(), color.green(), color.blue(), active ? 172 : 122), 1.6);
    arc_pen.setCapStyle(Qt::RoundCap);
    painter.setPen(arc_pen);
    painter.drawArc(QRectF(center.x() - 27.0, center.y() - 27.0, 54.0, 54.0),
                    int((-spin_ * 180.0 / kPi - 58.0) * 16.0), 74 * 16);
    painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 28), 1.0));
    painter.drawEllipse(center, 21.0, 21.0);
    QPen arc_pen2(QColor(color.red(), color.green(), color.blue(), 128), 1.2);
    arc_pen2.setCapStyle(Qt::RoundCap);
    painter.setPen(arc_pen2);
    painter.drawArc(QRectF(center.x() - 21.0, center.y() - 21.0, 42.0, 42.0),
                    int(spin_ * 180.0 / kPi) * 16, 128 * 16);

    QPen tick_pen(QColor(color.red(), color.green(), color.blue(), 62), 1.0);
    painter.setPen(tick_pen);
    const double dirs[4][2] = {{0.0, -1.0}, {0.0, 1.0}, {-1.0, 0.0}, {1.0, 0.0}};
    for (int i = 0; i < 4; ++i) {
        const double dx = dirs[i][0];
        const double dy = dirs[i][1];
        painter.drawLine(QPointF(center.x() + dx * 29.5, center.y() + dy * 29.5),
                         QPointF(center.x() + dx * 33.0, center.y() + dy * 33.0));
    }

    const double radius = 10.2 * breathing;
    QRadialGradient core(center, radius + 1.5);
    core.setColorAt(0.0, QColor(255, 242, 245, 250));
    core.setColorAt(0.32, QColor(color.red(), color.green(), color.blue(), 240));
    core.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 24));
    painter.setPen(Qt::NoPen);
    painter.setBrush(QBrush(core));
    painter.drawEllipse(center, radius, radius);

    QRadialGradient speck(center, radius * 0.5);
    speck.setColorAt(0.0, QColor(255, 255, 255, 190));
    speck.setColorAt(1.0, QColor(255, 255, 255, 0));
    painter.setBrush(QBrush(speck));
    painter.drawEllipse(center, radius * 0.62, radius * 0.62);
    painter.restore();
}

void FloatingHud::paint_header(QPainter& painter, const QRectF& card, const QColor& color) {
    painter.save();
    const Theme& theme = current_theme();
    QFont title_font(QString::fromStdString(theme.font));
    title_font.setPointSizeF(9.3);
    title_font.setWeight(QFont::DemiBold);
    title_font.setLetterSpacing(QFont::AbsoluteSpacing, 3.4);
    painter.setFont(title_font);
    painter.setPen(QColor(theme.text.c_str()));
    painter.drawText(QRectF(kTextX, card.top() + 10.0, width() - kTextX - 72.0, 18.0),
                     int(Qt::AlignLeft | Qt::AlignVCenter), QStringLiteral("JARVIS"));

    QFont state_font(QString::fromStdString(theme.font));
    state_font.setPointSizeF(6.5);
    state_font.setWeight(QFont::DemiBold);
    state_font.setLetterSpacing(QFont::AbsoluteSpacing, 1.6);
    painter.setFont(state_font);
    const std::string label = state_label(state_);
    const QFontMetrics metrics(state_font);
    const QRectF chip(kTextX, card.top() + 31.0,
                      metrics.horizontalAdvance(QString::fromStdString(label)) + 30.0, 17.0);
    painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 62), 1.0));
    painter.setBrush(QColor(color.red(), color.green(), color.blue(), 18));
    painter.drawRoundedRect(chip, 8.5, 8.5);

    const QPointF dot(chip.left() + 10.0, chip.center().y());
    painter.setPen(Qt::NoPen);
    painter.setBrush(QColor(color.red(), color.green(), color.blue(), 46));
    painter.drawEllipse(dot, 4.0, 4.0);
    painter.setBrush(QColor(color.red(), color.green(), color.blue(), 234));
    painter.drawEllipse(dot, 2.2, 2.2);

    painter.setPen(QColor(color.red(), color.green(), color.blue(), 234));
    painter.drawText(QRectF(chip.left() + 17.0, chip.top(), chip.width() - 18.0, chip.height()),
                     int(Qt::AlignLeft | Qt::AlignVCenter), QString::fromStdString(label));

    QFont clock_font(QString::fromStdString(theme.mono));
    clock_font.setPointSizeF(7.0);
    clock_font.setWeight(QFont::Medium);
    painter.setFont(clock_font);
    painter.setPen(QColor((hover_ ? theme.muted : theme.dim).c_str()));
    painter.drawText(QRectF(width() - 114.0, card.top() + 12.0, 62.0, 15.0),
                     int(Qt::AlignRight | Qt::AlignVCenter),
                     QDateTime::currentDateTime().toString("HH:mm"));
    painter.restore();
}

void FloatingHud::paint_subtitle(QPainter& painter, const QColor& color) {
    painter.save();
    const Theme& theme = current_theme();
    std::string sender;
    std::string text;
    QColor tone;
    bool muted = false;
    if (!subtitle_.empty()) {
        sender = subtitle_sender_;
        text = subtitle_;
        tone = (subtitle_sender_ == "SEN") ? QColor(theme.gold.c_str()) : QColor(color);
        muted = false;
    } else if (camera_active_ && !camera_text_.empty()) {
        sender = "KAMERA";
        text = camera_text_;
        tone = QColor(theme.red_bright.c_str());
        muted = false;
    } else {
        sender = "DURUM";
        text = state_hint(state_);
        tone = QColor(theme.muted.c_str());
        muted = true;
    }

    const QRectF tick_rect(kContentLeft, kSubY + 3.0, 2.4, 12.0);
    painter.setPen(Qt::NoPen);
    painter.setBrush(tone);
    painter.drawRoundedRect(tick_rect, 1.2, 1.2);

    QFont sender_font(QString::fromStdString(theme.font));
    sender_font.setPointSizeF(6.4);
    sender_font.setWeight(QFont::DemiBold);
    sender_font.setLetterSpacing(QFont::AbsoluteSpacing, 1.3);
    painter.setFont(sender_font);
    QColor sender_color = tone;
    sender_color.setAlpha(muted ? 170 : 228);
    painter.setPen(sender_color);
    const int sender_width =
        QFontMetrics(sender_font).horizontalAdvance(QString::fromStdString(sender));
    painter.drawText(QRectF(kContentLeft + 9.0, kSubY, sender_width + 1.0, 17.0),
                     int(Qt::AlignLeft | Qt::AlignVCenter), QString::fromStdString(sender));

    QFont body_font(QString::fromStdString(theme.font));
    body_font.setPointSizeF(8.2);
    body_font.setWeight(QFont::Medium);
    painter.setFont(body_font);
    const QFontMetrics metrics(body_font);
    const double body_x = kContentLeft + 9.0 + sender_width + 10.0;
    const double available = std::max(24.0, width() - kContentLeft - body_x);
    const QString elided = metrics.elidedText(QString::fromStdString(text), Qt::ElideRight,
                                              int(available));
    painter.setPen(QColor((muted ? theme.muted : theme.text_soft).c_str()));
    painter.drawText(QRectF(body_x, kSubY, available, 17.0),
                     int(Qt::AlignLeft | Qt::AlignVCenter), elided);
    painter.restore();
}

void FloatingHud::paint_divider(QPainter& painter, const QColor& color) {
    painter.save();
    const double left = kContentLeft;
    const double right = width() - kContentLeft;
    painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 140), 1.0));
    painter.drawLine(QPointF(left, kDividerY), QPointF(left + 14.0, kDividerY));
    QLinearGradient gradient(left + 14.0, 0.0, right, 0.0);
    gradient.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), 52));
    gradient.setColorAt(0.6, QColor(255, 255, 255, 14));
    gradient.setColorAt(1.0, QColor(255, 255, 255, 0));
    painter.setPen(QPen(QBrush(gradient), 1.0));
    painter.drawLine(QPointF(left + 14.0, kDividerY), QPointF(right, kDividerY));
    painter.restore();
}

namespace {

void draw_chip_base(QPainter& painter, const QRectF& rect, const QColor& tone,
                    const QColor& outline, const QColor& fill) {
    painter.setPen(QPen(outline, 1.0));
    painter.setBrush(fill);
    painter.drawRoundedRect(rect, rect.height() / 2.0, rect.height() / 2.0);
    Q_UNUSED(tone);
}

void draw_mic_glyph(QPainter& painter, const QPointF& origin, const QColor& color) {
    painter.save();
    painter.setBrush(Qt::NoBrush);
    QPen pen(color, 1.25);
    pen.setCapStyle(Qt::RoundCap);
    pen.setJoinStyle(Qt::RoundJoin);
    painter.setPen(pen);
    painter.drawRoundedRect(QRectF(origin.x() + 4.0, origin.y() + 1.2, 4.0, 6.6), 2.0, 2.0);
    QPainterPath path;
    path.moveTo(origin.x() + 2.4, origin.y() + 6.0);
    path.arcTo(QRectF(origin.x() + 2.4, origin.y() + 3.2, 7.2, 7.2), 180.0, -180.0);
    painter.drawPath(path);
    painter.drawLine(QPointF(origin.x() + 6.0, origin.y() + 10.4),
                     QPointF(origin.x() + 6.0, origin.y() + 12.0));
    painter.restore();
}

void draw_camera_glyph(QPainter& painter, const QPointF& origin, const QColor& color) {
    painter.save();
    painter.setBrush(Qt::NoBrush);
    QPen pen(color, 1.25);
    pen.setCapStyle(Qt::RoundCap);
    pen.setJoinStyle(Qt::RoundJoin);
    painter.setPen(pen);
    painter.drawRoundedRect(QRectF(origin.x() + 1.4, origin.y() + 3.4, 9.2, 6.8), 1.8, 1.8);
    painter.drawRoundedRect(QRectF(origin.x() + 4.2, origin.y() + 1.6, 4.0, 2.2), 0.9, 0.9);
    painter.drawEllipse(QPointF(origin.x() + 6.0, origin.y() + 6.8), 2.0, 2.0);
    painter.restore();
}

void draw_chip(QPainter& painter, const QRectF& rect, const QFont& font, const std::string& text,
               const std::function<void(QPainter&, const QPointF&, const QColor&)>& glyph,
               const QColor& tone, const QColor& outline, const QColor& fill) {
    painter.save();
    draw_chip_base(painter, rect, tone, outline, fill);
    glyph(painter, QPointF(rect.left() + 11.0, rect.center().y() - 6.0), tone);
    painter.setFont(font);
    painter.setPen(tone);
    painter.drawText(QRectF(rect.left() + 30.0, rect.top(), rect.width() - 38.0, rect.height()),
                     int(Qt::AlignLeft | Qt::AlignVCenter), QString::fromStdString(text));
    painter.restore();
}

}  // namespace

void FloatingHud::paint_status_chips(QPainter& painter) {
    const Theme& theme = current_theme();
    QFont font(QString::fromStdString(theme.font));
    font.setPointSizeF(6.4);
    font.setWeight(QFont::DemiBold);
    font.setLetterSpacing(QFont::AbsoluteSpacing, 0.9);
    const QFontMetrics metrics(font);

    const QColor accent(theme.red_bright.c_str());
    const std::string mic_text = mic_enabled_ ? "MİKROFON AÇIK" : "MİKROFON KAPALI";
    const double mic_width =
        11.0 + 12.0 + 7.0 + metrics.horizontalAdvance(QString::fromStdString(mic_text)) + 13.0;
    const QRectF mic_rect(kContentLeft, kChipY, mic_width, kChipHeight);
    QColor mic_tone;
    QColor mic_outline;
    QColor mic_fill;
    if (mic_enabled_) {
        mic_tone = accent;
        mic_outline = QColor(accent.red(), accent.green(), accent.blue(), 74);
        mic_fill = QColor(accent.red(), accent.green(), accent.blue(), 20);
    } else {
        mic_tone = QColor(theme.dim.c_str());
        mic_outline = QColor(255, 255, 255, 24);
        mic_fill = QColor(255, 255, 255, 6);
    }
    draw_chip(painter, mic_rect, font, mic_text, draw_mic_glyph, mic_tone, mic_outline, mic_fill);

    std::string cam_text;
    QColor cam_tone;
    QColor cam_outline;
    QColor cam_fill;
    if (camera_active_) {
        cam_text = camera_count_ > 0 ? ("KAMERA · " + std::to_string(camera_count_) + " HEDEF")
                                     : "KAMERA · CANLI";
        cam_tone = QColor(theme.gold.c_str());
        cam_outline = QColor(cam_tone.red(), cam_tone.green(), cam_tone.blue(), 82);
        cam_fill = QColor(cam_tone.red(), cam_tone.green(), cam_tone.blue(), 20);
    } else {
        cam_text = "KAMERA KAPALI";
        cam_tone = QColor(theme.dim.c_str());
        cam_outline = QColor(255, 255, 255, 24);
        cam_fill = QColor(255, 255, 255, 6);
    }
    const double cam_width =
        11.0 + 12.0 + 7.0 + metrics.horizontalAdvance(QString::fromStdString(cam_text)) + 13.0;
    const QRectF cam_rect(mic_rect.right() + 8.0, kChipY, cam_width, kChipHeight);
    draw_chip(painter, cam_rect, font, cam_text, draw_camera_glyph, cam_tone, cam_outline,
              cam_fill);
}

void FloatingHud::paint_waveform(QPainter& painter, const QColor& color) {
    painter.save();
    const double left = kContentLeft;
    const double right = width() - kContentLeft;
    constexpr int bars = 56;
    constexpr double gap = 2.2;
    const double bar_width = std::max(1.4, (right - left - (bars - 1) * gap) / bars);
    const double max_height = 13.0;
    const std::string& state = state_;

    painter.setPen(QPen(QColor(255, 255, 255, 9), 1.0));
    painter.drawLine(QPointF(left, kWaveY), QPointF(right, kWaveY));

    for (int index = 0; index < bars; ++index) {
        const double envelope = std::pow(std::sin(kPi * (index + 1) / (bars + 1)), 0.55);
        double level;
        if (state == "listening") {
            const double wobble = 0.5 + 0.5 * std::sin(phase_ * 4.2 + index * 0.9);
            if (mic_level_ > 0.02) {
                level = 0.05 + 0.95 * mic_level_ * (0.45 + 0.55 * wobble);
            } else {
                level = 0.02 + 0.02 * wobble;
            }
        } else if (state == "speaking") {
            level = 0.12 + 0.8 * std::fabs(std::sin(phase_ * 3.2 + index * 0.68));
        } else if (state == "thinking") {
            level = 0.1 + 0.44 * (0.5 + 0.5 * std::sin(phase_ * 2.8 - index * 0.36));
        } else if (state == "error") {
            level = 0.14 + 0.26 * std::fabs(std::sin(phase_ * 5.0 + index * 0.48));
        } else {
            level = 0.05 + 0.09 * (0.5 + 0.5 * std::sin(phase_ * 1.2 + index * 0.3));
        }
        const double bar_height = std::max(1.2, max_height * level * envelope);
        int alpha = 46 + int(150 * level);
        if (state == "listening" || state == "speaking") alpha = std::min(232, alpha + 24);
        if (hover_) alpha = std::min(255, alpha + 12);
        painter.setPen(Qt::NoPen);
        painter.setBrush(QColor(color.red(), color.green(), color.blue(), alpha));
        const double x = left + index * (bar_width + gap);
        painter.drawRoundedRect(QRectF(x, kWaveY - bar_height / 2.0, bar_width, bar_height),
                                bar_width / 2.0, bar_width / 2.0);
    }
    painter.restore();
}

void FloatingHud::paint_footer(QPainter& painter) {
    painter.save();
    const Theme& theme = current_theme();
    QFont font(QString::fromStdString(theme.font));
    font.setPointSizeF(5.7);
    font.setWeight(QFont::Medium);
    font.setLetterSpacing(QFont::AbsoluteSpacing, 1.0);
    painter.setFont(font);
    painter.setPen(QColor((hover_ ? theme.muted : theme.faint).c_str()));
    painter.drawText(QRectF(kContentLeft, height() - 19.0, width() - kContentLeft * 2.0, 11.0),
                     int(Qt::AlignLeft | Qt::AlignVCenter),
                     QString::fromUtf8("ÇİFT TIKLA · ANA PENCERE"));
    painter.drawText(QRectF(kContentLeft, height() - 19.0, width() - kContentLeft * 2.0, 11.0),
                     int(Qt::AlignRight | Qt::AlignVCenter),
                     QString::fromUtf8("SÜRÜKLE · KÖŞEYE YAPIŞ"));
    painter.restore();
}

void FloatingHud::mousePressEvent(QMouseEvent* event) {
    if (event->button() == Qt::LeftButton) {
        dragging_ = true;
        moved_ = false;
        press_pos_ = event->globalPosition().toPoint();
        drag_offset_ = event->globalPosition().toPoint() - frameGeometry().topLeft();
        setCursor(Qt::ClosedHandCursor);
        event->accept();
        return;
    }
    QWidget::mousePressEvent(event);
}

void FloatingHud::mouseMoveEvent(QMouseEvent* event) {
    if (dragging_ && (event->buttons() & Qt::LeftButton)) {
        if ((event->globalPosition().toPoint() - press_pos_).manhattanLength() > 4) {
            moved_ = true;
        }
        move(event->globalPosition().toPoint() - drag_offset_);
        event->accept();
        return;
    }
    QWidget::mouseMoveEvent(event);
}

void FloatingHud::mouseReleaseEvent(QMouseEvent* event) {
    if (event->button() == Qt::LeftButton && dragging_) {
        dragging_ = false;
        setCursor(Qt::OpenHandCursor);
        if (moved_) {
            snap_to_screen();
            save_position();
            position_checked_ = true;
        }
        event->accept();
        return;
    }
    QWidget::mouseReleaseEvent(event);
}

void FloatingHud::mouseDoubleClickEvent(QMouseEvent* event) {
    if (event->button() == Qt::LeftButton) {
        if (on_restore_requested) on_restore_requested();
        event->accept();
        return;
    }
    QWidget::mouseDoubleClickEvent(event);
}

void FloatingHud::contextMenuEvent(QContextMenuEvent* event) {
    QMenu menu(this);
    const Theme& theme = current_theme();
    menu.setStyleSheet(
        QStringLiteral("QMenu{background:%1;color:%2;border:1px solid %3;padding:4px;}")
            .arg(QString::fromStdString(theme.panel_2), QString::fromStdString(theme.text),
                 QString::fromStdString(theme.border)) +
        QStringLiteral("QMenu::item{padding:6px 18px;}") +
        QStringLiteral("QMenu::item:selected{background:%1;color:%2;}")
            .arg(QString::fromStdString(theme.red_dim), QString::fromStdString(theme.text)));
    QAction* restore = menu.addAction(QString::fromUtf8("Ana pencereye dön"));
    QObject::connect(restore, &QAction::triggered, this,
                     [this] { if (on_restore_requested) on_restore_requested(); });
    QAction* reset = menu.addAction(QString::fromUtf8("Konumu sıfırla"));
    QObject::connect(reset, &QAction::triggered, this, [this] { reset_position(); });
    menu.addSeparator();
    QAction* hide = menu.addAction(QString::fromUtf8("HUD'ı gizle"));
    QObject::connect(hide, &QAction::triggered, this, [this] { dismiss(); });
    menu.exec(event->globalPos());
    event->accept();
}

}  // namespace jarvis::ui
