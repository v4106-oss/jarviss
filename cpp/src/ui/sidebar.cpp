#include <jarvis/ui/sidebar.hpp>

#include <QLabel>
#include <QPushButton>
#include <QVBoxLayout>

#include <jarvis/ui/theme.hpp>

namespace jarvis::ui {

Sidebar::Sidebar(QWidget* parent) : QWidget(parent) {
    const Theme& t = current_theme();
    const QString panel = QString::fromStdString(t.panel);
    const QString border = QString::fromStdString(t.border);
    const QString text = QString::fromStdString(t.text);
    const QString accent = QString::fromStdString(t.red);
    const QString accent_hover = QString::fromStdString(t.red_bright);

    setFixedWidth(220);
    setStyleSheet(
        QStringLiteral("QWidget{background:%1;border-right:1px solid %2;}").arg(panel, border) +
        QStringLiteral("QLabel{color:%1;font-size:20px;font-weight:bold;padding:15px 10px;}")
            .arg(text) +
        QStringLiteral("QPushButton{background:%1;border:none;border-radius:10px;color:%2;"
                       "font-size:14px;font-weight:bold;padding:12px;}").arg(accent, text) +
        QStringLiteral("QPushButton:hover{background:%1;}").arg(accent_hover));

    auto* layout = new QVBoxLayout(this);
    layout->setContentsMargins(12, 12, 12, 12);
    layout->setSpacing(10);

    auto* title = new QLabel(QString::fromUtf8("🤖 Jarvis"));

    new_chat_ = new QPushButton(QString::fromUtf8("+ Yeni Sohbet"));
    new_chat_->setCursor(Qt::PointingHandCursor);

    browser_ = new QPushButton(QString::fromUtf8("🌐 Tarayıcı / Harita"));
    browser_->setCursor(Qt::PointingHandCursor);

    weather_ = new QPushButton(QString::fromUtf8("🌡 Hava Durumu"));
    weather_->setCursor(Qt::PointingHandCursor);

    camera_ = new QPushButton(QString::fromUtf8("📷 Kamera"));
    camera_->setCursor(Qt::PointingHandCursor);

    book_ = new QPushButton(QString::fromUtf8("📚 Kitap"));
    book_->setCursor(Qt::PointingHandCursor);

    home_ = new QPushButton(QString::fromUtf8("🏠 Akıllı Ev"));
    home_->setCursor(Qt::PointingHandCursor);

    diagnostics_ = new QPushButton(QString::fromUtf8("🩺 Tanılama"));
    diagnostics_->setCursor(Qt::PointingHandCursor);

    settings_ = new QPushButton(QString::fromUtf8("⚙ Yapay Zekâ Ayarları"));
    settings_->setCursor(Qt::PointingHandCursor);

    layout->addWidget(title);
    layout->addWidget(new_chat_);
    layout->addWidget(browser_);
    layout->addWidget(weather_);
    layout->addWidget(camera_);
    layout->addWidget(book_);
    layout->addWidget(home_);
    layout->addWidget(diagnostics_);
    layout->addWidget(settings_);
    layout->addStretch();
}

}  // namespace jarvis::ui
