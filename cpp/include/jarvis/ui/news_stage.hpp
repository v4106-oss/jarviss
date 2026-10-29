#pragma once

#include <QFont>
#include <QPixmap>
#include <QRect>
#include <QWidget>

#include <functional>
#include <string>
#include <vector>

class QColor;
class QHideEvent;
class QMouseEvent;
class QNetworkAccessManager;
class QNetworkReply;
class QObject;
class QPainter;
class QPaintEvent;
class QPropertyAnimation;
class QRectF;
class QTimer;

namespace jarvis::ui {

// Haber slaytı verisi; Python news_stage.py show_items girdisinin karşılığı.
struct NewsItem {
    std::string title;
    std::string source;
    std::string category;
    std::string published_at;
    std::string url;
    std::string image;
};

// Sağ üstteki küçük rozetten büyüyen, geçişli haber slaytı.
// Python app/ui/news_stage.py NewsStage karşılığı.
class NewsStage : public QWidget {
public:
    explicit NewsStage(QWidget* parent, std::function<bool()> is_busy = nullptr);

    void show_items(const std::vector<NewsItem>& items);
    void next_slide();
    void collapse();
    void dismiss();

protected:
    void paintEvent(QPaintEvent* event) override;
    void mousePressEvent(QMouseEvent* event) override;
    void hideEvent(QHideEvent* event) override;
    bool eventFilter(QObject* watched, QEvent* event) override;

private:
    QRect expanded_rect() const;
    QRect collapsed_rect() const;
    void animate_geometry(const QRect& target, int duration, bool hiding = false);
    void tick();
    void fetch_images();
    void on_image_ready(QNetworkReply* reply, int index);
    void paint_slide(QPainter& painter, const QRectF& rect, int index, double alpha,
                     double offset, double base_zoom, double now_ms);
    void paint_text_slide(QPainter& painter, const QRectF& rect, const NewsItem& slide,
                          double alpha);
    void paint_loading(QPainter& painter, const QRectF& rect, const QColor& accent,
                       const QColor& muted, double now_ms);
    void paint_pill(QPainter& painter, const QRectF& panel, const QColor& accent,
                    const QColor& text_soft, double now_ms);
    const QPixmap* current_pixmap() const;
    static std::string relative_time(const std::string& value);

    std::function<bool()> is_busy_;
    std::vector<NewsItem> slides_;
    std::vector<QPixmap> pixmaps_;
    std::vector<double> pixmap_start_;
    int index_ = 0;
    int previous_ = -1;
    double transition_ = 0.0;
    qint64 transition_start_ = 0;
    qint64 slide_started_ = 0;
    qint64 shown_at_ = 0;
    qint64 last_busy_check_ = 0;
    bool expanded_ = false;
    bool geometry_hiding_ = false;
    int token_ = 0;
    int geometry_serial_ = 0;
    QRect close_rect_;
    QFont font_head_;
    QFont font_title_;
    QFont font_meta_;
    QFont font_category_;
    QFont font_pill_;
    QTimer* timer_ = nullptr;
    QNetworkAccessManager* nam_ = nullptr;
    QPropertyAnimation* geometry_animation_ = nullptr;
    qint64 clock_start_ms_ = 0;
};

}  // namespace jarvis::ui
