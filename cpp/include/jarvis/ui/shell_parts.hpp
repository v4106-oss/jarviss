#pragma once

#include <QColor>
#include <QFrame>
#include <QPainterPath>
#include <QPushButton>
#include <QTimer>
#include <QWidget>

#include <functional>
#include <string>

class QEnterEvent;
class QHBoxLayout;
class QLabel;
class QMouseEvent;
class QPainter;
class QPaintEvent;
class QPointF;
class QRectF;
class QStackedWidget;
class QVBoxLayout;

namespace jarvis::ui {

// Python app/ui/shell_parts.py glyph_path() karşılığı: 24x24 kutuda normalize
// edilmiş vektörel ikon yolu.
QPainterPath glyph_path(const std::string& key);

// Python draw_glyph() karşılığı: yolu hedef dikdörtgene ölçekleyip çizer.
void draw_glyph(QPainter& painter, const std::string& key, const QRectF& rect,
                const QColor& color, double width = 1.6);

// Vektörel ikon pencereciği (GlyphIcon).
class GlyphIcon : public QWidget {
public:
    explicit GlyphIcon(const std::string& key = "spark", int size = 18,
                       const std::string& color = std::string(), QWidget* parent = nullptr);
    void set_glyph(const std::string& key);
    void set_color(const std::string& color);

protected:
    void paintEvent(QPaintEvent* event) override;

private:
    std::string key_;
    QColor color_;
};

// Dairesel/kare HUD ikon düğmesi; durum rengi ve nabız halkası taşır (GlyphButton).
class GlyphButton : public QWidget {
public:
    explicit GlyphButton(const std::string& key, const std::string& tooltip = "", int size = 34,
                         bool checkable = false, bool accent = false, QWidget* parent = nullptr);

    void set_glyph(const std::string& key);
    std::string glyph() const { return key_; }
    void set_active(bool active);
    bool is_active() const;
    void set_accent(bool accent);
    void set_checked(bool checked);
    bool is_checked() const { return checked_; }
    void pulse();

    std::function<void()> onClick;

protected:
    void paintEvent(QPaintEvent* event) override;
    void enterEvent(QEnterEvent* event) override;
    void leaveEvent(QEvent* event) override;
    void mouseReleaseEvent(QMouseEvent* event) override;

private:
    void ensure_anim();
    void tick();

    std::string key_;
    bool checkable_ = false;
    bool accent_ = false;
    bool hover_ = false;
    bool active_ = false;
    bool checked_ = false;
    double hover_value_ = 0.0;
    double active_value_ = 0.0;
    double pulse_value_ = 0.0;
    bool pulsing_ = false;
    int size_;
    QTimer timer_;
};

// Dikey gezinme rayının ikon + kısa etiket düğmesi (NavButton).
class NavButton : public QWidget {
public:
    explicit NavButton(const std::string& key, const std::string& label,
                       const std::string& tooltip = "", QWidget* parent = nullptr);
    void set_active(bool active);
    bool is_active() const { return active_; }
    std::function<void()> onClick;

protected:
    void paintEvent(QPaintEvent* event) override;
    void enterEvent(QEnterEvent* event) override;
    void leaveEvent(QEvent* event) override;
    void mousePressEvent(QMouseEvent* event) override;
    void mouseReleaseEvent(QMouseEvent* event) override;

private:
    void ensure_anim();
    void tick();

    std::string key_;
    std::string label_;
    bool hover_ = false;
    bool active_ = false;
    bool press_ = false;
    double hover_value_ = 0.0;
    double active_value_ = 0.0;
    double press_value_ = 0.0;
    QTimer timer_;
};

// Sade marka işareti: ince halka ve tek vurgu noktası (BrandMark).
class BrandMark : public QWidget {
public:
    explicit BrandMark(int size = 38, QWidget* parent = nullptr);
    void set_animating(bool enabled);

protected:
    void paintEvent(QPaintEvent* event) override;
};

// Nefes alan durum noktası (PulseDot).
class PulseDot : public QWidget {
public:
    explicit PulseDot(const std::string& color = std::string(), int size = 12,
                      QWidget* parent = nullptr);
    void set_animating(bool enabled);
    void set_color(const std::string& color);
    void set_active(bool active);

protected:
    void paintEvent(QPaintEvent* event) override;

private:
    void tick();

    QColor color_;
    double phase_ = 0.0;
    bool active_ = true;
    double burst_ = 0.0;
    QTimer timer_;
};

// Kapsül biçimli durum çipi; aktif rengi QSS özelliğiyle değiştirir (TextChip).
class TextChip : public QPushButton {
public:
    explicit TextChip(const std::string& text = "", const std::string& tooltip = "", bool dot = true,
                      QWidget* parent = nullptr);
    void set_label(const std::string& text);
    void set_active(bool active);
    bool is_active() const;

private:
    void render();

    std::string label_;
    bool dot_ = true;
    bool active_ = false;
};

// Mikrofona ulaşan sesin canlı seviyesi (MicLevelMeter).
class MicLevelMeter : public QWidget {
public:
    explicit MicLevelMeter(QWidget* parent = nullptr);
    void set_enabled(bool enabled);
    void set_level(double level);
    void pulse();

protected:
    void paintEvent(QPaintEvent* event) override;

private:
    void ensure_timer();
    void tick();

    double level_ = 0.0;
    double shown_ = 0.0;
    double heard_ = 0.0;
    bool enabled_ = false;
    QColor accent_;
    QTimer timer_;
};

// Ortadaki komut arama kapsülü (CommandPill).
class CommandPill : public QFrame {
public:
    explicit CommandPill(QWidget* parent = nullptr);
    void set_hint(const std::string& text);
    std::function<void()> onClick;

protected:
    void paintEvent(QPaintEvent* event) override;
    void enterEvent(QEnterEvent* event) override;
    void leaveEvent(QEvent* event) override;
    void mouseReleaseEvent(QMouseEvent* event) override;

private:
    void ensure_anim();
    void tick();

    GlyphIcon* icon_ = nullptr;
    QLabel* hint_ = nullptr;
    bool hover_ = false;
    double hover_value_ = 0.0;
    QTimer timer_;
};

// Yazma alanı çerçevesi; odaklanınca vurgu halkası belirir (ComposerFrame).
class ComposerFrame : public QFrame {
public:
    explicit ComposerFrame(QWidget* parent = nullptr);
    void track(QWidget* widget);

protected:
    bool eventFilter(QObject* watched, QEvent* event) override;
    void paintEvent(QPaintEvent* event) override;

private:
    void ensure_anim();
    void tick();

    double focus_value_ = 0.0;
    QTimer timer_;
};

// Segment şeridi + sayfa yığını (SegmentedControl / SegmentedStack).
class SegmentedControl : public QFrame {
public:
    explicit SegmentedControl(QWidget* parent = nullptr);
    int add_segment(const std::string& label);
    void set_current(int index);
    int current() const { return current_; }
    std::function<void(int)> on_current_changed;

private:
    QHBoxLayout* row_ = nullptr;
    std::vector<QPushButton*> buttons_;
    int current_ = -1;
};

class SegmentedStack : public QWidget {
public:
    explicit SegmentedStack(const std::vector<std::string>& labels = {}, QWidget* parent = nullptr);
    int add_page(QWidget* widget, const std::string& label);
    void set_current(int index);
    SegmentedControl* control() const { return control_; }
    QStackedWidget* stack() const { return stack_; }
    std::function<void(int)> on_current_changed;

private:
    SegmentedControl* control_ = nullptr;
    QStackedWidget* stack_ = nullptr;
};

// Yeni yüzey kartı: başlık, isteğe bağlı ikon ve gövde düzeni (HudCard).
class HudCard : public QFrame {
public:
    explicit HudCard(const std::string& title = "", const std::string& trailing = "",
                     const std::string& glyph = "", int padding = 13, bool clickable = false,
                     QWidget* parent = nullptr);
    void set_trailing(const std::string& text);
    void set_glyph(const std::string& key);
    QVBoxLayout* body() const { return body_; }
    std::function<void()> onClick;

protected:
    void mouseReleaseEvent(QMouseEvent* event) override;

private:
    bool clickable_ = false;
    GlyphIcon* icon_ = nullptr;
    QLabel* trailing_ = nullptr;
    QVBoxLayout* body_ = nullptr;
};

// Küçük etiket + değer satırı (StatusRow).
class StatusRow : public QWidget {
public:
    explicit StatusRow(const std::string& label, const std::string& value = "—",
                       QWidget* parent = nullptr);
    void set_value(const std::string& text);

private:
    QLabel* value_ = nullptr;
};

// Panel içi bölüm etiketi; ince çizgi ve sayaç taşır (SectionLabel).
class SectionLabel : public QWidget {
public:
    explicit SectionLabel(const std::string& title, const std::string& trailing = "",
                          QWidget* parent = nullptr);
};

}  // namespace jarvis::ui
