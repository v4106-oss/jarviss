#include <jarvis/ui/hud_modal.hpp>

#include <jarvis/ui/hud_widgets.hpp>
#include <jarvis/ui/theme.hpp>

#include <QDateTime>
#include <QGraphicsOpacityEffect>
#include <QHBoxLayout>
#include <QKeyEvent>
#include <QLabel>
#include <QMouseEvent>
#include <QPainter>
#include <QParallelAnimationGroup>
#include <QPropertyAnimation>
#include <QPushButton>
#include <QScrollArea>
#include <QTimer>
#include <QVariantAnimation>

#include <algorithm>
#include <cmath>

namespace jarvis::ui {

class HudModal::Veil : public QWidget {
public:
    explicit Veil(HudModal* modal) : QWidget(modal), modal_(modal) {
        setAttribute(Qt::WidgetAttribute::WA_TransparentForMouseEvents, true);
        setAttribute(Qt::WidgetAttribute::WA_NoSystemBackground, true);
        setAttribute(Qt::WidgetAttribute::WA_TranslucentBackground, true);
    }

protected:
    void paintEvent(QPaintEvent* /*event*/) override {
        int alpha = static_cast<int>(238 * std::clamp(modal_->veil_alpha_, 0.0, 1.0));
        if (alpha <= 0) {
            return;
        }
        QPainter painter(this);
        painter.fillRect(rect(), QColor(2, 2, 3, alpha));
    }

private:
    HudModal* modal_ = nullptr;
};

HudModal::HudModal(QWidget* parent, const std::string& title, const std::string& subtitle,
                   int width, int preferred_height, int padding, bool fade, bool scrollable,
                   bool persistent)
    : QWidget(parent),
      preferred_width_(width),
      preferred_height_(preferred_height),
      padding_(padding),
      persistent_(persistent) {
    const Theme& theme = current_theme();
    setAttribute(Qt::WidgetAttribute::WA_StyledBackground, false);
    setFocusPolicy(Qt::FocusPolicy::StrongFocus);

    outer_layout_ = new QVBoxLayout(this);
    outer_layout_->setContentsMargins(24, 20, 24, 20);
    outer_layout_->addStretch(1);
    auto* row = new QHBoxLayout();
    row->setContentsMargins(0, 0, 0, 0);
    row->addStretch(1);
    card_ = new QFrame();
    card_->setObjectName("modalCard");
    card_->setStyleSheet(QStringLiteral("QFrame#modalCard{background:%1;border:1px solid %2;border-radius:%3px;}")
                             .arg(QString::fromStdString(theme.panel),
                                  QString::fromStdString(theme.border))
                             .arg(theme.window_radius));
    row->addWidget(card_);
    row->addStretch(1);
    outer_layout_->addLayout(row);
    outer_layout_->addStretch(1);

    auto* card_layout = new QVBoxLayout(card_);
    card_layout->setContentsMargins(padding_, 22, padding_, 0);
    card_layout->setSpacing(0);

    auto* header = new QWidget();
    auto* header_layout = new QVBoxLayout(header);
    header_layout->setContentsMargins(0, 0, 0, 0);
    header_layout->setSpacing(7);
    auto* top = new QHBoxLayout();
    top->setContentsMargins(0, 0, 0, 0);
    top->setSpacing(12);
    title_label_ = hud_label(title, 15, theme.text, QFont::Weight::DemiBold, 0.2);
    close_button_ = new QPushButton(QStringLiteral("✕"));
    close_button_->setFixedSize(30, 30);
    close_button_->setCursor(Qt::CursorShape::PointingHandCursor);
    close_button_->setStyleSheet(
        QStringLiteral(
            "QPushButton{background:transparent;color:%1;border:1px solid %2;border-radius:7px;font-size:12px;padding:0;}"
            "QPushButton:hover{color:%3;border-color:%4;background:%5;}")
            .arg(QString::fromStdString(theme.text_soft),
                 QString::fromStdString(theme.border),
                 QString::fromStdString(theme.red_bright),
                 QString::fromStdString(theme.red_soft),
                 QString::fromStdString(rgba_css(theme.red, 0.10))));
    connect(close_button_, &QPushButton::clicked, this, [this] { close_modal(); });
    top->addWidget(title_label_);
    top->addStretch(1);
    top->addWidget(close_button_);
    header_layout->addLayout(top);
    subtitle_label_ = hud_label(subtitle, 8, theme.muted, QFont::Weight::Medium, 2.2, true);
    subtitle_label_->setWordWrap(true);
    header_layout->addWidget(subtitle_label_);
    card_layout->addWidget(header);
    card_layout->addSpacing(16);

    body_ = new QWidget();
    content_ = new QVBoxLayout(body_);
    content_->setContentsMargins(0, 0, 4, 26);
    content_->setSpacing(14);
    if (scrollable) {
        scroll_ = new QScrollArea();
        scroll_->setWidgetResizable(true);
        scroll_->setFrameShape(QFrame::Shape::NoFrame);
        scroll_->setHorizontalScrollBarPolicy(Qt::ScrollBarPolicy::ScrollBarAlwaysOff);
        scroll_->setStyleSheet(
            "QScrollArea{background:transparent;border:none;}QScrollArea > QWidget > QWidget{background:transparent;}");
        scroll_->viewport()->setStyleSheet("background:transparent;");
        scroll_->setWidget(body_);
        card_layout->addWidget(scroll_, 1);
    } else {
        card_layout->addWidget(body_, 1);
    }

    if (fade) {
        effect_ = new QGraphicsOpacityEffect(card_);
        effect_->setOpacity(0.0);
        card_->setGraphicsEffect(effect_);
        fade_animation_ = new QPropertyAnimation(effect_, "opacity", this);
        fade_animation_->setDuration(170);
        fade_animation_->setStartValue(0.0);
        fade_animation_->setEndValue(1.0);
        fade_animation_->setEasingCurve(QEasingCurve::Type::OutCubic);
    } else {
        veil_ = new Veil(this);
        veil_->hide();
    }
}

void HudModal::set_subtitle(const std::string& text) {
    subtitle_label_->setText(QString::fromStdString(text).toUpper());
}

void HudModal::reopen() {
    closed_ = false;
}

void HudModal::apply_geometry() {
    QWidget* parent = parentWidget();
    if (parent == nullptr) {
        return;
    }
    int available_width = std::max(360, parent->width() - 64);
    int available_height = std::max(320, parent->height() - 64);
    card_->setFixedWidth(std::min(preferred_width_, available_width));
    int height;
    if (preferred_height_) {
        height = preferred_height_;
    } else {
        height = headerHeightHint() + body_->sizeHint().height() + 58;
    }
    card_->setFixedHeight(std::max(280, std::min(height, available_height)));
}

int HudModal::headerHeightHint() const {
    // Başlık + alt başlık yüksekliği için tahmini değer.
    return subtitle_label_->sizeHint().height() + title_label_->sizeHint().height() + 40;
}

void HudModal::set_backdrop(double value) {
    backdrop_ = value;
    update();
}

void HudModal::set_entrance_offset(double value) {
    entrance_value_ = value;
    int offset = static_cast<int>(std::lround(18 * entrance_value_));
    outer_layout_->setContentsMargins(24, 20 + offset, 24, 20 - offset);
}

void HudModal::start_entrance() {
    stop_animation(entrance_animation_);
    auto* animation = new QVariantAnimation(this);
    animation->setDuration(240);
    animation->setStartValue(1.0);
    animation->setEndValue(0.0);
    animation->setEasingCurve(QEasingCurve::Type::OutCubic);
    connect(animation, &QVariantAnimation::valueChanged, this,
            [this](const QVariant& value) { set_entrance_offset(value.toDouble()); });
    connect(animation, &QVariantAnimation::finished, this, [this, animation] {
        if (entrance_animation_ == animation) {
            entrance_animation_ = nullptr;
        }
        set_entrance_offset(0.0);
        animation->deleteLater();
    });
    entrance_animation_ = animation;
    animation->start();
}

void HudModal::set_veil(double value) {
    veil_alpha_ = value;
    if (veil_ != nullptr) {
        veil_->update();
    }
}

void HudModal::start_veil_enter() {
    if (veil_ == nullptr) {
        return;
    }
    stop_animation(veil_animation_);
    veil_->setGeometry(rect());
    veil_->show();
    veil_->raise();
    set_veil(1.0);
    auto* animation = new QVariantAnimation(this);
    animation->setDuration(300);
    animation->setStartValue(1.0);
    animation->setEndValue(0.0);
    animation->setEasingCurve(QEasingCurve::Type::OutCubic);
    connect(animation, &QVariantAnimation::valueChanged, this,
            [this](const QVariant& value) { set_veil(value.toDouble()); });
    veil_animation_ = animation;
    animation->start();
}

void HudModal::start_backdrop_enter() {
    stop_animation(backdrop_animation_);
    if (skip_backdrop_fade_) {
        skip_backdrop_fade_ = false;
        set_backdrop(1.0);
    } else {
        set_backdrop(0.0);
        auto* animation = new QVariantAnimation(this);
        animation->setDuration(200);
        animation->setStartValue(0.0);
        animation->setEndValue(1.0);
        animation->setEasingCurve(QEasingCurve::Type::OutCubic);
        connect(animation, &QVariantAnimation::valueChanged, this,
                [this](const QVariant& value) { set_backdrop(value.toDouble()); });
        backdrop_animation_ = animation;
        animation->start();
    }
    if (fade_animation_ != nullptr) {
        fade_animation_->stop();
        fade_animation_->start();
    }
}

void HudModal::stop_animation(QObject* animation) {
    if (animation == nullptr) {
        return;
    }
    auto* variant = qobject_cast<QVariantAnimation*>(animation);
    if (variant != nullptr) {
        variant->stop();
    } else {
        auto* property = qobject_cast<QPropertyAnimation*>(animation);
        if (property != nullptr) {
            property->stop();
        }
    }
    animation->deleteLater();
}

void HudModal::showEvent(QShowEvent* event) {
    QWidget::showEvent(event);
    setAttribute(Qt::WidgetAttribute::WA_TransparentForMouseEvents, false);
    opened_at_ = QDateTime::currentMSecsSinceEpoch();
    setGeometry(parentWidget()->rect());
    apply_geometry();
    start_entrance();
    start_backdrop_enter();
    start_veil_enter();
    setFocus();
}

void HudModal::resizeEvent(QResizeEvent* event) {
    QWidget::resizeEvent(event);
    apply_geometry();
    if (veil_ != nullptr) {
        veil_->setGeometry(rect());
    }
}

void HudModal::paintEvent(QPaintEvent* event) {
    QPainter painter(this);
    int alpha = static_cast<int>(216 * std::clamp(backdrop_, 0.0, 1.0));
    painter.fillRect(rect(), QColor(2, 2, 3, alpha));
    QWidget::paintEvent(event);
}

void HudModal::mousePressEvent(QMouseEvent* event) {
    if (QDateTime::currentMSecsSinceEpoch() - opened_at_ < 350) {
        return;
    }
    if (!card_->geometry().contains(event->position().toPoint())) {
        close_modal();
        return;
    }
    QWidget::mousePressEvent(event);
}

void HudModal::keyPressEvent(QKeyEvent* event) {
    if (event->key() == Qt::Key::Key_Escape) {
        close_modal();
        return;
    }
    QWidget::keyPressEvent(event);
}

void HudModal::close_modal() {
    if (closed_) {
        return;
    }
    closed_ = true;
    if (on_closed) {
        on_closed();
    }
    if (!isVisible()) {
        hide();
        if (!persistent_) {
            deleteLater();
        }
        return;
    }
    if (close_animation_ != nullptr) {
        return;
    }
    setAttribute(Qt::WidgetAttribute::WA_TransparentForMouseEvents, true);
    stop_animation(backdrop_animation_);
    backdrop_animation_ = nullptr;
    stop_animation(entrance_animation_);
    entrance_animation_ = nullptr;
    stop_animation(veil_animation_);
    veil_animation_ = nullptr;

    auto* group = new QParallelAnimationGroup(this);
    if (effect_ != nullptr) {
        auto* fade = new QPropertyAnimation(effect_, "opacity", group);
        fade->setDuration(170);
        fade->setStartValue(effect_->opacity());
        fade->setEndValue(0.0);
        fade->setEasingCurve(QEasingCurve::Type::InCubic);
        group->addAnimation(fade);
    }
    auto* backdrop = new QVariantAnimation(group);
    backdrop->setDuration(210);
    backdrop->setStartValue(backdrop_);
    backdrop->setEndValue(0.0);
    backdrop->setEasingCurve(QEasingCurve::Type::InCubic);
    connect(backdrop, &QVariantAnimation::valueChanged, this,
            [this](const QVariant& value) { set_backdrop(value.toDouble()); });
    group->addAnimation(backdrop);
    auto* settle = new QVariantAnimation(group);
    settle->setDuration(210);
    settle->setStartValue(entrance_value_);
    settle->setEndValue(std::min(1.0, entrance_value_ + 0.6));
    settle->setEasingCurve(QEasingCurve::Type::InCubic);
    connect(settle, &QVariantAnimation::valueChanged, this,
            [this](const QVariant& value) { set_entrance_offset(value.toDouble()); });
    group->addAnimation(settle);
    if (veil_ != nullptr) {
        auto* veil = new QVariantAnimation(group);
        veil->setDuration(170);
        veil->setStartValue(veil_alpha_);
        veil->setEndValue(1.0);
        veil->setEasingCurve(QEasingCurve::Type::InCubic);
        connect(veil, &QVariantAnimation::valueChanged, this,
                [this](const QVariant& value) { set_veil(value.toDouble()); });
        group->addAnimation(veil);
    }
    connect(group, &QParallelAnimationGroup::finished, this, [this, group] {
        if (close_animation_ == group) {
            close_animation_ = nullptr;
        }
        finish_close_animation();
        group->deleteLater();
    });
    close_animation_ = group;
    group->start();
}

void HudModal::finish_close_animation() {
    if (veil_ != nullptr) {
        veil_->hide();
    }
    hide();
    if (!persistent_) {
        deleteLater();
    }
}

void HudModal::closeEvent(QCloseEvent* event) {
    if (!closed_) {
        closed_ = true;
        if (on_closed) {
            on_closed();
        }
    }
    QWidget::closeEvent(event);
}

QFrame* hud_card(int width, int height, int padding) {
    const Theme& theme = current_theme();
    auto* frame = new QFrame();
    frame->setObjectName("innerCard");
    if (width) {
        frame->setFixedWidth(width);
    }
    if (height) {
        frame->setFixedHeight(height);
    }
    frame->setStyleSheet(QStringLiteral("QFrame#innerCard{background:%1;border:1px solid %2;border-radius:12px;}")
                             .arg(QString::fromStdString(theme.panel_2),
                                  QString::fromStdString(theme.border)));
    auto* layout = new QVBoxLayout(frame);
    layout->setContentsMargins(padding, padding, padding, padding);
    layout->setSpacing(5);
    return frame;
}

QFrame* stat_card(const std::string& title, const std::string& value,
                  const std::string& note, double percent) {
    const Theme& theme = current_theme();
    QFrame* frame = hud_card();
    auto* body = static_cast<QVBoxLayout*>(frame->layout());
    body->addWidget(hud_label(title, 7, theme.dim, QFont::Weight::Medium, 0.2));
    auto* value_label = hud_label(value, 19, theme.text, QFont::Weight::DemiBold, 0.4);
    body->addWidget(value_label);
    if (percent >= 0.0) {
        body->addWidget(new MeterBar(static_cast<int>(percent), 3));
    }
    body->addWidget(hud_label(note, 7, theme.muted, QFont::Weight::Medium, 0.8));
    return frame;
}

}  // namespace jarvis::ui
