#include <jarvis/ui/diagnostics_modal.hpp>

#include <jarvis/ui/hud_widgets.hpp>
#include <jarvis/ui/theme.hpp>

#include <QApplication>
#include <QClipboard>
#include <QDesktopServices>
#include <QDir>
#include <QFont>
#include <QHBoxLayout>
#include <QLabel>
#include <QPlainTextEdit>
#include <QPushButton>
#include <QUrl>

namespace jarvis::ui {

DiagnosticsModal::DiagnosticsModal(QWidget* parent, const std::string& text,
                                   std::function<std::string()> refresh,
                                   const std::string& data_dir)
    : HudModal(parent, "Tanılama ve Gizlilik",
               "YEREL RAPOR · API ANAHTARI VE TOKEN YAZILMAZ", 880, 720),
      refresh_(std::move(refresh)),
      data_dir_(data_dir) {
    const Theme& theme = current_theme();
    editor_ = new QPlainTextEdit();
    editor_->setReadOnly(true);
    editor_->setPlainText(QString::fromStdString(text));
    editor_->setFont(hud_font(9, QFont::Weight::Normal, 0.2));
    editor_->setLineWrapMode(QPlainTextEdit::LineWrapMode::WidgetWidth);
    editor_->setMinimumHeight(430);
    editor_->setStyleSheet(
        QStringLiteral(
            "QPlainTextEdit{background:%1;color:%2;border:1px solid %3;border-radius:8px;"
            "padding:10px;selection-background-color:%4;}")
            .arg(QString::fromStdString(theme.panel_2),
                 QString::fromStdString(theme.text_soft),
                 QString::fromStdString(theme.border),
                 QString::fromStdString(rgba_css(theme.red, 0.35))));
    content_->addWidget(editor_, 1);

    auto* actions = new QHBoxLayout();
    actions->setSpacing(7);
    notice_ = hud_label("", 7, theme.dim, QFont::Weight::Medium, 0.6);
    notice_->setWordWrap(true);
    auto* copy = new HudButton(QStringLiteral("KOPYALA"), true, true);
    connect(copy, &QPushButton::clicked, this, [this] { copy_report(); });
    auto* refresh_btn = new HudButton(QStringLiteral("YENİLE"), false, true);
    connect(refresh_btn, &QPushButton::clicked, this, [this] { reload(); });
    refresh_btn->setEnabled(static_cast<bool>(refresh_));
    auto* folder = new HudButton(QStringLiteral("VERİ KLASÖRÜ"), false, true);
    connect(folder, &QPushButton::clicked, this, [this] { open_folder(); });
    folder->setEnabled(!data_dir_.empty());
    actions->addWidget(notice_, 1);
    actions->addWidget(folder);
    actions->addWidget(refresh_btn);
    actions->addWidget(copy);
    content_->addLayout(actions);
}

void DiagnosticsModal::copy_report() {
    QApplication::clipboard()->setText(editor_->toPlainText());
    notice_->setText("Rapor panoya kopyalandı; paylaşmadan önce gözden geçir.");
}

void DiagnosticsModal::reload() {
    if (!refresh_) return;
    try {
        editor_->setPlainText(QString::fromStdString(refresh_()));
        notice_->setText("Rapor yenilendi.");
    } catch (const std::exception& error) {
        notice_->setText(QStringLiteral("Rapor yenilenemedi: %1").arg(error.what()));
    }
}

void DiagnosticsModal::open_folder() {
    if (data_dir_.empty()) return;
    QDir().mkpath(QString::fromStdString(data_dir_));
    QDesktopServices::openUrl(QUrl::fromLocalFile(QString::fromStdString(data_dir_)));
}

}  // namespace jarvis::ui
