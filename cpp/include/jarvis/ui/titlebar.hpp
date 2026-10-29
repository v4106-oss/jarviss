#pragma once

#include <QPoint>
#include <QWidget>

class QPushButton;

namespace jarvis::ui {

// Frameless pencerede sürüklemeli başlık çubuğu; Python app/ui/titlebar.py karşılığı.
class TitleBar : public QWidget {
public:
    explicit TitleBar(QWidget* parent = nullptr);

protected:
    void mousePressEvent(QMouseEvent* event) override;
    void mouseMoveEvent(QMouseEvent* event) override;

private:
    QPoint drag_pos_;
    QPushButton* min_btn_ = nullptr;
    QPushButton* max_btn_ = nullptr;
    QPushButton* close_btn_ = nullptr;
};

}  // namespace jarvis::ui
