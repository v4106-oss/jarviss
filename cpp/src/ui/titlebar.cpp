#include <jarvis/ui/titlebar.hpp>

#include <QHBoxLayout>
#include <QLabel>
#include <QMouseEvent>
#include <QPushButton>

#include <jarvis/ui/theme.hpp>

namespace jarvis::ui {

TitleBar::TitleBar(QWidget* parent) : QWidget(parent) {
    const Theme& t = current_theme();
    const QString panel = QString::fromStdString(t.panel);
    const QString border = QString::fromStdString(t.border);
    const QString hover = QString::fromStdString(t.panel_hover);
    const QString text = QString::fromStdString(t.text);

    setFixedHeight(45);
    setStyleSheet(
        QStringLiteral("QWidget{background:%1;border-bottom:1px solid %2;}").arg(panel, border) +
        QStringLiteral("QLabel{color:%1;font-size:15px;font-weight:bold;}").arg(text) +
        QStringLiteral("QPushButton{background:transparent;color:%1;border:none;"
                       "min-width:40px;min-height:35px;font-size:15px;}").arg(text) +
        QStringLiteral("QPushButton:hover{background:%1;border-radius:8px;}").arg(hover));

    auto* layout = new QHBoxLayout(this);
    layout->setContentsMargins(12, 0, 12, 0);

    auto* title = new QLabel(QString::fromUtf8("🤖 Jarvis"));

    min_btn_ = new QPushButton(QString::fromUtf8("—"));
    max_btn_ = new QPushButton(QString::fromUtf8("□"));
    close_btn_ = new QPushButton(QString::fromUtf8("✕"));

    layout->addWidget(title);
    layout->addStretch();
    layout->addWidget(min_btn_);
    layout->addWidget(max_btn_);
    layout->addWidget(close_btn_);

    QWidget* win = parentWidget();
    connect(min_btn_, &QPushButton::clicked, this, [win] {
        if (win) win->showMinimized();
    });
    connect(max_btn_, &QPushButton::clicked, this, [win] {
        if (!win) return;
        if (win->isMaximized()) {
            win->showNormal();
        } else {
            win->showMaximized();
        }
    });
    connect(close_btn_, &QPushButton::clicked, this, [win] {
        if (win) win->close();
    });
}

void TitleBar::mousePressEvent(QMouseEvent* event) {
    if (event->button() == Qt::LeftButton) {
        drag_pos_ = event->globalPosition().toPoint();
    }
}

void TitleBar::mouseMoveEvent(QMouseEvent* event) {
    QWidget* win = parentWidget();
    if (win && (event->buttons() & Qt::LeftButton)) {
        const QPoint delta = event->globalPosition().toPoint() - drag_pos_;
        win->move(win->pos() + delta);
        drag_pos_ = event->globalPosition().toPoint();
    }
}

}  // namespace jarvis::ui
