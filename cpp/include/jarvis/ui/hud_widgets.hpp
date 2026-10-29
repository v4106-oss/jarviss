#pragma once

#include <QColor>
#include <QFrame>
#include <QFont>
#include <QLabel>
#include <QProgressBar>
#include <QPushButton>
#include <QTimer>
#include <QVBoxLayout>
#include <QWidget>

#include <functional>
#include <string>
#include <vector>

class QHideEvent;
class QMouseEvent;
class QPainter;
class QPaintEvent;
class QPointF;
class QShowEvent;

namespace jarvis::ui {

// Python'daki hud_font() karşılığı: sade tipografi, yumuşatılmış harf aralığı.
QFont hud_font(int size = 10, QFont::Weight weight = QFont::Weight::Medium,
               double spacing = 1.4, const std::string& family = std::string(),
               bool italic = false);

// Python'daki hud_label() karşılığı.
QLabel* hud_label(const std::string& text = "", int size = 10,
                  const std::string& color = std::string(),
                  QFont::Weight weight = QFont::Weight::Medium, double spacing = 1.4,
                  bool upper = false, const std::string& family = std::string());

void set_label_color(QLabel* label, const std::string& color);

// 1px ayırıcı çizgi.
class Divider : public QFrame {
public:
    explicit Divider(QWidget* parent = nullptr);
};

// İnce çerçeveli, vurgulu HUD düğmesi.
class HudButton : public QPushButton {
public:
    HudButton(const QString& text, bool accent = false, bool small = false,
              QWidget* parent = nullptr);
};

// Tıklanabilir şeffaf çerçeve.
class ClickableFrame : public QFrame {
public:
    explicit ClickableFrame(QWidget* parent = nullptr);
    std::function<void()> onClick;

protected:
    void mouseReleaseEvent(QMouseEvent* event) override;
};

// Yuvarlak köşeli koyu panel (hudPanel).
class Panel : public QFrame {
public:
    explicit Panel(int padding = 16, QWidget* parent = nullptr);
    QVBoxLayout* body() const { return body_; }

private:
    QVBoxLayout* body_ = nullptr;
};

// Başlık + sağda takip metni.
class SectionTitle : public QWidget {
public:
    SectionTitle(const std::string& title, const std::string& trailing = "",
                 QWidget* parent = nullptr);
};

// Sade dairesel ölçüm.
class RingGauge : public QWidget {
public:
    RingGauge(double value = 0.0, const std::string& caption = "",
              const std::string& unit = "%", double diameter = 84, bool show_value = true,
              QWidget* parent = nullptr);
    void set_value(double value);

protected:
    void paintEvent(QPaintEvent* event) override;

private:
    double value_ = 0.0;
    std::string caption_;
    std::string unit_;
    bool show_value_ = true;
};

// Saat diski.
class ClockDisc : public QWidget {
public:
    explicit ClockDisc(QWidget* parent = nullptr);
    void set_time(int hour, int minute, int second = 0);
    std::function<void()> onClick;

protected:
    void paintEvent(QPaintEvent* event) override;
    void mouseReleaseEvent(QMouseEvent* event) override;

private:
    std::string time_ = "00:00";
    int seconds_ = 0;
    double day_percent_ = 0.0;
};

// Durum amblemi (nefes/radar/ping döngüsü).
class StatusEmblem : public QWidget {
public:
    static constexpr double kModeSeconds = 4.6;
    explicit StatusEmblem(QWidget* parent = nullptr);
    void set_status(const std::string& status);
    void start();
    void set_animating(bool enabled);
    std::function<void()> onClick;

protected:
    void paintEvent(QPaintEvent* event) override;
    void mouseReleaseEvent(QMouseEvent* event) override;

private:
    void tick();
    void paint_mode(QPainter& painter, const QPointF& center, double radius, int mode,
                    double local, double fade);

    std::string status_ = "HAZIR";
    double elapsed_ = 0.0;
    int last_mode_ = -1;
    double blip_angle_ = 40.0;
    QTimer* timer_ = nullptr;
    bool animating_ = true;
};

// Altın renkli mini çubuk grafiği.
class SparkBars : public QWidget {
public:
    explicit SparkBars(const std::string& color = std::string(), QWidget* parent = nullptr);
    void push(double value);

protected:
    void paintEvent(QPaintEvent* event) override;

private:
    QColor color_;
    std::vector<double> values_;
};

// Hava durumu ikonu.
class WeatherIcon : public QWidget {
public:
    explicit WeatherIcon(const std::string& kind = "cloud", int size = 36,
                         QWidget* parent = nullptr);
    void set_kind(const std::string& kind);

protected:
    void paintEvent(QPaintEvent* event) override;

private:
    std::string kind_;
};

// Sıcaklık aralığı çubuğu.
class TempRangeBar : public QWidget {
public:
    explicit TempRangeBar(QWidget* parent = nullptr);
    void set_range(double low, double high, double current);

protected:
    void paintEvent(QPaintEvent* event) override;

private:
    double low_ = 13.0;
    double high_ = 18.0;
    double current_ = 16.0;
};

// Saatlik sıcaklık eğrisi veri noktası.
struct HourPoint {
    double temp = 0.0;
    int precip = 0;
    std::string label;
};

// Saatlik sıcaklık eğrisi + yağış çubukları.
class TempCurve : public QWidget {
public:
    explicit TempCurve(QWidget* parent = nullptr);
    void set_hours(const std::vector<HourPoint>& hours);

protected:
    void paintEvent(QPaintEvent* event) override;

private:
    std::vector<HourPoint> hours_;
};

// Ana konum bilgisi.
struct HomeLocation {
    std::string name = "Derince";
    std::string region = "Kocaeli";
    double lat = 40.757;
    double lon = 29.815;
};

// Harita üzerinde ev noktasını gösteren dekoratif mini harita.
class MiniMap : public QWidget {
public:
    explicit MiniMap(QWidget* parent = nullptr);
    void set_animating(bool enabled);
    void set_home(const HomeLocation& home);
    std::function<void()> onClick;

protected:
    void paintEvent(QPaintEvent* event) override;
    void mouseReleaseEvent(QMouseEvent* event) override;
    void showEvent(QShowEvent* event) override;
    void hideEvent(QHideEvent* event) override;

private:
    void tick();
    double phase_ = 0.0;
    HomeLocation home_;
    bool animating_ = true;
    QTimer* timer_ = nullptr;
};

// Metrik kartı.
class MetricCard : public QFrame {
public:
    MetricCard(const std::string& title, const std::string& value = "—",
               const std::string& note = "", bool meter = false, QWidget* parent = nullptr);
    void set(const std::string& value, const std::string& note = "",
             double percent = -1.0);

private:
    QLabel* value_ = nullptr;
    QLabel* note_ = nullptr;
    QFrame* meter_ = nullptr;
};

// Küçük ilerleme çubuğu.
class MeterBar : public QProgressBar {
public:
    explicit MeterBar(int value = 0, int height = 3, QWidget* parent = nullptr);
    void set_value(double value);
};

// Boş durum etiketi.
class EmptyState : public QLabel {
public:
    explicit EmptyState(const std::string& text, QWidget* parent = nullptr);
};

// Başlık altında aralıklı süpürme çizgisi.
class ScanLine : public QWidget {
public:
    explicit ScanLine(QWidget* parent = nullptr);
    void set_animating(bool enabled);

protected:
    void paintEvent(QPaintEvent* event) override;
    void showEvent(QShowEvent* event) override;
    void hideEvent(QHideEvent* event) override;

private:
    void advance();
    double head_position(double progress) const;

    double progress_ = 0.0;
    int direction_ = 1;
    double speed_ = 0.012;
    bool active_ = false;
    double wait_ = 1.0;
    double ghost_x_ = -1.0;
    double ghost_life_ = 0.0;
    bool animating_ = true;
    QTimer* timer_ = nullptr;
};

}  // namespace jarvis::ui
