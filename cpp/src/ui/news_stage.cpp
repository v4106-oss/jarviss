#include <jarvis/ui/news_stage.hpp>

#include <QDateTime>
#include <QEasingCurve>
#include <QEvent>
#include <QLinearGradient>
#include <QMouseEvent>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QPainter>
#include <QPainterPath>
#include <QPen>
#include <QPropertyAnimation>
#include <QRadialGradient>
#include <QTimer>
#include <QUrl>

#include <algorithm>
#include <cmath>

#include <jarvis/ui/theme.hpp>

namespace jarvis::ui {

namespace {

constexpr int kMaxSlides = 6;
constexpr int kSlideMs = 5200;
constexpr int kTransitionMs = 640;
constexpr int kHideGraceMs = 6500;
constexpr int kMaxVisibleMs = 75000;
constexpr double kPanelRadius = 22;
constexpr double kImageRadius = 15;

QColor tint(const QColor& color, int alpha) {
    QColor out = color;
    out.setAlpha(std::clamp(alpha, 0, 255));
    return out;
}

double ease(double value) {
    const double t = std::clamp(value, 0.0, 1.0);
    return t * t * (3.0 - 2.0 * t);
}

QPainterPath rounded(const QRectF& rect, double radius) {
    QPainterPath path;
    path.addRoundedRect(rect, radius, radius);
    return path;
}

}  // namespace

NewsStage::NewsStage(QWidget* parent, std::function<bool()> is_busy)
    : QWidget(parent), is_busy_(std::move(is_busy)) {
    setAttribute(Qt::WA_NoSystemBackground, true);
    setAttribute(Qt::WA_TranslucentBackground, true);
    setFocusPolicy(Qt::NoFocus);

    const Theme& theme = current_theme();
    font_head_ = QFont(QString::fromStdString(theme.font), 8);
    font_head_.setWeight(QFont::DemiBold);
    font_head_.setLetterSpacing(QFont::AbsoluteSpacing, 2.4);
    font_title_ = QFont(QString::fromStdString(theme.font), 11);
    font_title_.setWeight(QFont::DemiBold);
    font_meta_ = QFont(QString::fromStdString(theme.mono), 7);
    font_meta_.setLetterSpacing(QFont::AbsoluteSpacing, 1.2);
    font_category_ = QFont(QString::fromStdString(theme.font), 34);
    font_category_.setWeight(QFont::DemiBold);
    font_category_.setLetterSpacing(QFont::AbsoluteSpacing, 8.0);
    font_pill_ = QFont(QString::fromStdString(theme.mono), 7);
    font_pill_.setLetterSpacing(QFont::AbsoluteSpacing, 1.6);

    nam_ = new QNetworkAccessManager(this);
    timer_ = new QTimer(this);
    timer_->setInterval(30);
    QObject::connect(timer_, &QTimer::timeout, this, [this] { tick(); });

    if (parent != nullptr) {
        parent->installEventFilter(this);
    }
    clock_start_ms_ = QDateTime::currentMSecsSinceEpoch();
    hide();
}

QRect NewsStage::expanded_rect() const {
    QWidget* parent = parentWidget();
    if (parent == nullptr) {
        return QRect(24, 100, 780, 500);
    }
    const int w = std::max(1, parent->width());
    const int h = std::max(1, parent->height());
    int panel_w = std::min(860, std::max(560, int(w * 0.55)));
    int panel_h = std::min(560, std::max(360, int(h * 0.58)));
    panel_w = std::min(panel_w, w - 60);
    panel_h = std::min(panel_h, h - 210);
    const int top = std::max(84, std::min(int(h * 0.16), h - panel_h - 118));
    return QRect(w - 26 - panel_w, top, panel_w, panel_h);
}

QRect NewsStage::collapsed_rect() const {
    const QRect expanded = expanded_rect();
    return QRect(expanded.right() - 258, expanded.top(), 258, 92);
}

bool NewsStage::eventFilter(QObject* watched, QEvent* event) {
    if (watched == parentWidget() && event->type() == QEvent::Resize) {
        if (isVisible() && expanded_) {
            const bool running = geometry_animation_ != nullptr &&
                                 geometry_animation_->state() == QAbstractAnimation::Running;
            if (!running) {
                setGeometry(expanded_rect());
            }
        }
    }
    return false;
}

void NewsStage::animate_geometry(const QRect& target, int duration, bool hiding) {
    ++geometry_serial_;
    const int serial = geometry_serial_;
    geometry_hiding_ = hiding;
    if (geometry_animation_ != nullptr) {
        geometry_animation_->stop();
        geometry_animation_->deleteLater();
        geometry_animation_ = nullptr;
    }
    auto* animation = new QPropertyAnimation(this, "geometry", this);
    animation->setDuration(duration);
    animation->setStartValue(geometry());
    animation->setEndValue(target);
    animation->setEasingCurve(hiding ? QEasingCurve::InOutCubic : QEasingCurve::OutCubic);
    QObject::connect(animation, &QPropertyAnimation::finished, this, [this, serial, hiding] {
        if (serial != geometry_serial_) return;
        geometry_hiding_ = false;
        if (hiding) hide();
    });
    geometry_animation_ = animation;
    animation->start();
}

void NewsStage::show_items(const std::vector<NewsItem>& items) {
    std::vector<NewsItem> slides;
    for (const auto& item : items) {
        std::string title;
        bool in_space = false;
        for (const char c : item.title) {
            if (c == ' ' || c == '\t' || c == '\n' || c == '\r') {
                if (!in_space && !title.empty()) title += ' ';
                in_space = true;
            } else {
                title += c;
                in_space = false;
            }
        }
        if (title.size() > 180) title = title.substr(0, 180);
        if (title.empty()) continue;
        NewsItem slide;
        slide.title = title;
        slide.source = item.source.substr(0, std::min<std::size_t>(60, item.source.size()));
        slide.category = item.category;
        slide.published_at = item.published_at;
        slide.url = item.url;
        slide.image = item.image;
        slides.push_back(std::move(slide));
        if (int(slides.size()) >= kMaxSlides) break;
    }
    if (slides.empty()) return;

    slides_ = std::move(slides);
    pixmaps_.assign(slides_.size(), QPixmap());
    pixmap_start_.assign(slides_.size(), 0.0);
    index_ = 0;
    previous_ = -1;
    transition_ = 0.0;
    const qint64 now = QDateTime::currentMSecsSinceEpoch() - clock_start_ms_;
    slide_started_ = now;
    shown_at_ = now;
    last_busy_check_ = now;
    ++token_;
    if (!isVisible()) {
        expanded_ = true;
        setGeometry(collapsed_rect());
        show();
        raise();
        animate_geometry(expanded_rect(), 430);
    } else if (geometry_hiding_ || !expanded_) {
        expanded_ = true;
        raise();
        animate_geometry(expanded_rect(), 430);
    }
    fetch_images();
    timer_->start();
    update();
}

void NewsStage::fetch_images() {
    for (int i = 0; i < int(slides_.size()); ++i) {
        const std::string& url = slides_[i].image;
        if (url.empty()) continue;
        QNetworkRequest request(QUrl(QString::fromStdString(url)));
        QNetworkReply* reply = nam_->get(request);
        QObject::connect(reply, &QNetworkReply::finished, this, [this, reply, i] {
            on_image_ready(reply, i);
            reply->deleteLater();
        });
    }
}

void NewsStage::on_image_ready(QNetworkReply* reply, int index) {
    if (index < 0 || index >= int(pixmaps_.size())) return;
    if (reply->error() != QNetworkReply::NoError) return;
    const QByteArray payload = reply->readAll();
    QPixmap pixmap;
    if (!pixmap.loadFromData(payload) || pixmap.isNull()) return;
    pixmaps_[index] = pixmap;
    pixmap_start_[index] = (QDateTime::currentMSecsSinceEpoch() - clock_start_ms_) / 1000.0;
    update();
}

void NewsStage::next_slide() {
    if (slides_.size() < 2 || previous_ >= 0) return;
    previous_ = index_;
    index_ = (index_ + 1) % int(slides_.size());
    transition_ = 0.0;
    transition_start_ = QDateTime::currentMSecsSinceEpoch() - clock_start_ms_;
    slide_started_ = transition_start_;
    update();
}

void NewsStage::collapse() {
    if (!isVisible()) return;
    expanded_ = false;
    ++geometry_serial_;
    animate_geometry(collapsed_rect(), 360, true);
    update();
}

void NewsStage::dismiss() {
    slides_.clear();
    pixmaps_.clear();
    pixmap_start_.clear();
    previous_ = -1;
    transition_ = 0.0;
    ++token_;
    timer_->stop();
    ++geometry_serial_;
    expanded_ = false;
    geometry_hiding_ = false;
    if (geometry_animation_ != nullptr) {
        geometry_animation_->stop();
        geometry_animation_->deleteLater();
        geometry_animation_ = nullptr;
    }
    hide();
}

void NewsStage::hideEvent(QHideEvent* event) {
    timer_->stop();
    ++geometry_serial_;
    geometry_hiding_ = false;
    QWidget::hideEvent(event);
}

void NewsStage::tick() {
    if (!isVisible()) {
        timer_->stop();
        return;
    }
    const qint64 now = QDateTime::currentMSecsSinceEpoch() - clock_start_ms_;
    if (previous_ >= 0) {
        transition_ =
            std::min(1.0, (now - transition_start_) / double(kTransitionMs));
        if (transition_ >= 1.0) {
            previous_ = -1;
        }
    } else if (now - slide_started_ >= kSlideMs) {
        next_slide();
    }
    if (is_busy_ && now - shown_at_ >= kHideGraceMs) {
        if (now - last_busy_check_ >= 600) {
            last_busy_check_ = now;
            bool busy = false;
            busy = is_busy_();
            if (!busy && now - slide_started_ >= 2400) {
                collapse();
                return;
            }
        }
    }
    if (now - shown_at_ > kMaxVisibleMs) {
        collapse();
        return;
    }
    update();
}

void NewsStage::mousePressEvent(QMouseEvent* event) {
    if (event->button() != Qt::LeftButton) return;
    if (close_rect_.contains(event->position().toPoint())) {
        collapse();
        return;
    }
    next_slide();
}

const QPixmap* NewsStage::current_pixmap() const {
    if (index_ >= 0 && index_ < int(pixmaps_.size())) {
        const QPixmap& p = pixmaps_[index_];
        if (!p.isNull()) return &p;
    }
    return nullptr;
}

std::string NewsStage::relative_time(const std::string& value) {
    const QDateTime moment = QDateTime::fromString(QString::fromStdString(value), Qt::ISODate);
    if (!moment.isValid()) return "";
    const qint64 seconds =
        moment.secsTo(QDateTime::currentDateTimeUtc());
    if (seconds < 60) return "az önce";
    if (seconds < 3600) return std::to_string(seconds / 60) + " dk önce";
    if (seconds < 86400) return std::to_string(seconds / 3600) + " sa önce";
    return std::to_string(seconds / 86400) + " gün önce";
}

void NewsStage::paintEvent(QPaintEvent* event) {
    Q_UNUSED(event);
    const int w = width();
    const int h = height();
    if (w < 60 || h < 40) return;
    const double now_ms = (QDateTime::currentMSecsSinceEpoch() - clock_start_ms_) / 1000.0;
    const Theme& theme = current_theme();
    const QColor accent(theme.is_light ? theme.red.c_str() : theme.red_bright.c_str());
    const QColor text_main(theme.text.c_str());
    const QColor text_soft(theme.text_soft.c_str());
    const QColor muted(theme.muted.c_str());
    const QRectF panel = QRectF(this->rect()).adjusted(1.5, 1.5, -1.5, -1.5);

    QPainter painter(this);
    painter.setRenderHint(QPainter::Antialiasing, true);
    painter.setRenderHint(QPainter::TextAntialiasing, true);
    painter.setRenderHint(QPainter::SmoothPixmapTransform, true);

    painter.setPen(Qt::NoPen);
    painter.setBrush(QColor(0, 0, 0, 70));
    painter.drawPath(rounded(panel.translated(0.0, 6.0), kPanelRadius));

    QLinearGradient body(panel.topLeft(), panel.bottomRight());
    body.setColorAt(0.0, QColor(theme.panel_2.c_str()));
    body.setColorAt(1.0, QColor(theme.background_2.c_str()));
    painter.setBrush(QBrush(body));
    painter.drawPath(rounded(panel, kPanelRadius));
    painter.setBrush(Qt::NoBrush);
    painter.setPen(QPen(QColor(theme.border.c_str()), 1.0));
    painter.drawPath(rounded(panel, kPanelRadius));

    if (w < 430 || h < 300) {
        paint_pill(painter, panel, accent, text_soft, now_ms);
        return;
    }

    painter.save();
    painter.setClipPath(rounded(panel, kPanelRadius));
    QRadialGradient glow(QPointF(panel.right() - 60.0, panel.top() + 20.0), panel.width() * 0.5);
    glow.setColorAt(0.0, tint(accent, 20));
    glow.setColorAt(1.0, tint(accent, 0));
    painter.fillRect(panel, QBrush(glow));
    painter.restore();

    const double margin = 22.0;
    const double header_baseline = 32.0;
    const double pulse = 0.5 + 0.5 * std::sin(now_ms * 2.6);
    painter.setPen(Qt::NoPen);
    painter.setBrush(tint(accent, int(36 + 30 * pulse)));
    painter.drawEllipse(QPointF(margin + 4.0, header_baseline - 4.0), 7.5, 7.5);
    painter.setBrush(tint(accent, 220));
    painter.drawEllipse(QPointF(margin + 4.0, header_baseline - 4.0), 2.6, 2.6);
    painter.setFont(font_head_);
    painter.setPen(tint(text_soft, 215));
    painter.drawText(QPointF(margin + 18.0, header_baseline), QStringLiteral("HABER SUNUMU"));

    const QString counter =
        QString("%1 / %2").arg(index_ + 1, 2, 10, QChar('0')).arg(int(slides_.size()), 2, 10, QChar('0'));
    painter.setFont(font_meta_);
    painter.setPen(tint(muted, 190));
    const int counter_width = painter.fontMetrics().horizontalAdvance(counter);
    painter.drawText(QPointF(panel.right() - margin - 38.0 - counter_width, header_baseline),
                     counter);

    close_rect_ = QRect(int(panel.right() - margin - 24.0), 13, 24, 24);
    painter.setPen(QPen(tint(muted, 130), 1.0));
    painter.setBrush(Qt::NoBrush);
    painter.drawRoundedRect(QRectF(close_rect_), 8.0, 8.0);
    const double cx = close_rect_.center().x();
    const double cy = close_rect_.center().y();
    painter.setPen(QPen(tint(text_soft, 200), 1.3));
    painter.drawLine(QPointF(cx - 3.6, cy - 3.6), QPointF(cx + 3.6, cy + 3.6));
    painter.drawLine(QPointF(cx - 3.6, cy + 3.6), QPointF(cx + 3.6, cy - 3.6));

    const double footer_height = 116.0;
    const QRectF image(margin, 52.0, panel.width() - 2.0 * margin,
                       panel.height() - 52.0 - footer_height);
    const QPainterPath image_path = rounded(image, kImageRadius);
    painter.save();
    painter.setClipPath(image_path);
    painter.fillRect(image, QColor(theme.background.c_str()));
    if (previous_ >= 0) {
        const double t = ease(transition_);
        paint_slide(painter, image, previous_, 1.0 - t, -14.0 * t, 1.055, now_ms);
        paint_slide(painter, image, index_, t, 30.0 * (1.0 - t), 1.02, now_ms);
    } else {
        paint_slide(painter, image, index_, 1.0, 0.0, 1.02, now_ms);
    }
    QLinearGradient shade(image.bottomLeft(), image.topLeft());
    shade.setColorAt(0.0, QColor(4, 6, 8, 165));
    shade.setColorAt(0.42, QColor(4, 6, 8, 45));
    shade.setColorAt(1.0, QColor(4, 6, 8, 0));
    painter.fillRect(image, QBrush(shade));
    if (current_pixmap() == nullptr) {
        paint_loading(painter, image, accent, muted, now_ms);
    }
    painter.restore();
    painter.setPen(QPen(QColor(theme.border.c_str()), 1.0));
    painter.setBrush(Qt::NoBrush);
    painter.drawPath(image_path);

    const double title_alpha = previous_ < 0 ? 1.0 : 0.45 + 0.55 * ease(transition_);
    const double meta_y = image.bottom() + 18.0;
    const NewsItem& slide = slides_[index_];
    std::string meta;
    std::string category = slide.category;
    for (auto& c : category) c = char(std::toupper(static_cast<unsigned char>(c)));
    const std::string rel = relative_time(slide.published_at);
    if (!slide.source.empty()) meta = slide.source;
    if (!category.empty()) meta += (meta.empty() ? "" : "  ·  ") + category;
    if (!rel.empty()) meta += (meta.empty() ? "" : "  ·  ") + rel;
    painter.setFont(font_meta_);
    painter.setPen(tint(muted, int(205 * title_alpha)));
    painter.drawText(QRectF(margin, meta_y, panel.width() - 2.0 * margin, 16.0),
                     int(Qt::AlignLeft | Qt::AlignVCenter), QString::fromStdString(meta));

    const QRectF title_rect(margin, meta_y + 20.0, panel.width() - 2.0 * margin, 48.0);
    painter.setFont(font_title_);
    painter.setPen(tint(text_main, int(245 * title_alpha)));
    painter.drawText(title_rect, int(Qt::AlignLeft | Qt::AlignTop | Qt::TextWordWrap),
                     QString::fromStdString(slide.title));

    if (slides_.size() > 1) {
        const QRectF track(margin, panel.bottom() - 8.0, panel.width() - 2.0 * margin, 2.0);
        painter.setPen(Qt::NoPen);
        painter.setBrush(QColor(theme.border.c_str()));
        painter.drawRoundedRect(track, 1.0, 1.0);
        double progress = 0.0;
        if (previous_ < 0) {
            progress = std::min(1.0, (QDateTime::currentMSecsSinceEpoch() - clock_start_ms_ -
                                      slide_started_) /
                                         double(kSlideMs));
        }
        if (progress > 0.0) {
            painter.setBrush(tint(accent, 185));
            painter.drawRoundedRect(
                QRectF(track.left(), track.top(), std::max(2.0, track.width() * progress),
                       track.height()),
                1.0, 1.0);
        }
    }
}

void NewsStage::paint_pill(QPainter& painter, const QRectF& panel, const QColor& accent,
                           const QColor& text_soft, double now_ms) {
    const Theme& theme = current_theme();
    const double margin = 16.0;
    const double pulse = 0.5 + 0.5 * std::sin(now_ms * 2.6);
    painter.setPen(Qt::NoPen);
    painter.setBrush(tint(accent, int(34 + 26 * pulse)));
    painter.drawEllipse(QPointF(margin + 8.0, panel.center().y()), 8.0, 8.0);
    painter.setBrush(tint(accent, 225));
    painter.drawEllipse(QPointF(margin + 8.0, panel.center().y()), 2.8, 2.8);
    painter.setFont(font_pill_);
    painter.setPen(tint(text_soft, 220));
    painter.drawText(QPointF(margin + 26.0, panel.center().y() - 3.0),
                     QStringLiteral("HABER SUNUMU"));
    const int dot_count = int(now_ms * 4.0) % 3 + 1;
    painter.setPen(tint(QColor(theme.muted.c_str()), 180));
    painter.drawText(QPointF(margin + 26.0, panel.center().y() + 15.0),
                     QString::fromUtf8("GÖRSELLER HAZIRLANIYOR ") + QString(dot_count, QChar('·')));
}

void NewsStage::paint_slide(QPainter& painter, const QRectF& rect, int index, double alpha,
                            double offset, double base_zoom, double now_ms) {
    if (index < 0 || index >= int(slides_.size()) || alpha <= 0.01) return;
    const NewsItem& slide = slides_[index];
    const QPixmap* pixmap =
        (index >= 0 && index < int(pixmaps_.size())) ? &pixmaps_[index] : nullptr;
    if (pixmap == nullptr || pixmap->isNull()) {
        if (alpha >= 0.45) paint_text_slide(painter, rect, slide, alpha);
        return;
    }
    const double since = now_ms - pixmap_start_[index];
    const double fade = std::clamp(since / 0.36, 0.0, 1.0);
    const QRectF target = rect.translated(offset, 0.0);
    const double source_w = std::max(1, pixmap->width());
    const double source_h = std::max(1, pixmap->height());
    const double elapsed = std::max(0.0, now_ms - slide_started_ / 1000.0);
    const double zoom = base_zoom + 0.05 * std::min(1.0, elapsed / 6.0);
    const double scale = std::max(target.width() / source_w, target.height() / source_h) * zoom;
    const double draw_w = source_w * scale;
    const double draw_h = source_h * scale;
    const double x = target.center().x() - draw_w / 2.0;
    const double y = target.center().y() - draw_h / 2.0;
    painter.save();
    painter.setOpacity(std::clamp(alpha * fade, 0.0, 1.0));
    painter.drawPixmap(QRectF(x, y, draw_w, draw_h), *pixmap,
                       QRectF(0.0, 0.0, double(source_w), double(source_h)));
    painter.restore();
}

void NewsStage::paint_text_slide(QPainter& painter, const QRectF& rect, const NewsItem& slide,
                                 double alpha) {
    const Theme& theme = current_theme();
    const QColor accent(theme.is_light ? theme.red.c_str() : theme.red_bright.c_str());
    painter.save();
    painter.setOpacity(alpha);
    QLinearGradient gradient(rect.topLeft(), rect.bottomRight());
    gradient.setColorAt(0.0, tint(accent, 46));
    gradient.setColorAt(0.55, QColor(theme.panel_2.c_str()));
    gradient.setColorAt(1.0, QColor(theme.background.c_str()));
    painter.fillRect(rect, QBrush(gradient));
    const QPointF center = rect.center();
    painter.setBrush(Qt::NoBrush);
    const double radii[2] = {rect.height() * 0.42, rect.height() * 0.55};
    for (double radius : radii) {
        painter.setPen(QPen(tint(accent, 34), 1.0));
        painter.drawEllipse(center, radius, radius);
    }
    std::string label = slide.category.empty() ? "haber" : slide.category;
    for (auto& c : label) c = char(std::toupper(static_cast<unsigned char>(c)));
    painter.setFont(font_category_);
    painter.setPen(tint(accent, 88));
    const int metrics_height = painter.fontMetrics().height();
    const int label_width = painter.fontMetrics().horizontalAdvance(QString::fromStdString(label));
    painter.drawText(QPointF(center.x() - label_width / 2.0, center.y() + metrics_height / 3.0),
                     QString::fromStdString(label));
    painter.setFont(font_meta_);
    painter.setPen(tint(QColor(theme.muted.c_str()), 160));
    std::string source = slide.source.empty() ? "JARVIS HABER" : slide.source;
    for (auto& c : source) c = char(std::toupper(static_cast<unsigned char>(c)));
    const int source_width = painter.fontMetrics().horizontalAdvance(QString::fromStdString(source));
    painter.drawText(QPointF(center.x() - source_width / 2.0,
                             center.y() + metrics_height + 26.0),
                     QString::fromStdString(source));
    painter.restore();
}

void NewsStage::paint_loading(QPainter& painter, const QRectF& rect, const QColor& accent,
                              const QColor& muted, double now_ms) {
    const double phase = std::fmod(now_ms * 0.55, 1.0);
    QLinearGradient sweep(rect.left(), rect.top(), rect.right(), rect.top());
    sweep.setColorAt(std::max(0.0, phase - 0.22), tint(accent, 0));
    sweep.setColorAt(std::min(1.0, std::max(0.0, phase)), tint(accent, 16));
    sweep.setColorAt(std::min(1.0, phase + 0.22), tint(accent, 0));
    painter.fillRect(rect, QBrush(sweep));
    painter.setFont(font_meta_);
    painter.setPen(tint(muted, 175));
    const int dot_count = int(now_ms * 3.0) % 3 + 1;
    const QString text =
        QString::fromUtf8("GÖRSELLER HAZIRLANIYOR ") + QString(dot_count, QChar('·'));
    const int text_width = painter.fontMetrics().horizontalAdvance(text);
    painter.drawText(QPointF(rect.center().x() - text_width / 2.0, rect.center().y() + 4.0), text);
}

}  // namespace jarvis::ui
