#include <jarvis/ui/browser_modal.hpp>

#include <jarvis/ui/browser_service.hpp>
#include <jarvis/ui/hud_widgets.hpp>
#include <jarvis/ui/theme.hpp>

#include <QFont>
#include <QHBoxLayout>
#include <QLabel>
#include <QLineEdit>
#include <QPushButton>
#include <QUrl>
#include <QWebEngineHistory>
#include <QWebEngineView>

#include <regex>

namespace jarvis::ui {

namespace {

std::string to_lower(std::string s) {
    for (char& c : s) {
        if (c >= 'A' && c <= 'Z') c = char(c - 'A' + 'a');
    }
    return s;
}

}  // namespace

BrowserModal::BrowserModal(QWidget* parent, int width, int height)
    : HudModal(parent, "Tarayıcı", "JARVIS İÇ TARAYICI · TEK MOTOR", width, height, 26, false,
               false, true) {
    const Theme& theme = current_theme();

    auto* toolbar = new QHBoxLayout();
    toolbar->setSpacing(8);
    back_button_ = new HudButton(QStringLiteral("←"), false, true);
    back_button_->setToolTip(QStringLiteral("Geri"));
    connect(back_button_, &QPushButton::clicked, this, [this] {
        if (view_ != nullptr) view_->back();
    });
    forward_button_ = new HudButton(QStringLiteral("→"), false, true);
    forward_button_->setToolTip(QStringLiteral("İleri"));
    connect(forward_button_, &QPushButton::clicked, this, [this] {
        if (view_ != nullptr) view_->forward();
    });
    auto* reload_button = new HudButton(QStringLiteral("YENİLE"), false, true);
    connect(reload_button, &QPushButton::clicked, this, [this] {
        if (view_ != nullptr) view_->reload();
    });
    auto* map_button = new HudButton(QStringLiteral("HARİTA"), false, true);
    map_button->setToolTip(QStringLiteral("Jarvis haritasını aç"));
    connect(map_button, &QPushButton::clicked, this, [this] {
        if (on_map_requested) on_map_requested();
    });

    address_ = new QLineEdit();
    address_->setPlaceholderText(QStringLiteral("Adres veya arama yaz…"));
    address_->setFont(hud_font(9, QFont::Weight::Medium, 0.6));
    address_->setMinimumHeight(30);
    address_->setStyleSheet(
        QStringLiteral(
            "QLineEdit{background:%1;color:%2;border:1px solid %3;border-radius:6px;padding:4px 10px;}"
            "QLineEdit:focus{border-color:%4;}")
            .arg(QString::fromStdString(theme.panel),
                 QString::fromStdString(theme.text),
                 QString::fromStdString(theme.border),
                 QString::fromStdString(theme.red_soft)));
    connect(address_, &QLineEdit::returnPressed, this, [this] { navigate(); });
    auto* go_button = new HudButton(QStringLiteral("GİT"), true, true);
    connect(go_button, &QPushButton::clicked, this, [this] { navigate(); });

    toolbar->addWidget(back_button_);
    toolbar->addWidget(forward_button_);
    toolbar->addWidget(reload_button);
    toolbar->addWidget(map_button);
    toolbar->addWidget(address_, 1);
    toolbar->addWidget(go_button);
    content_->addLayout(toolbar);

    status_label_ = hud_label("TARAYICI HAZIR", 7, theme.muted, QFont::Weight::Medium, 1.4);
    content_->addWidget(status_label_);

    try {
        view_ = BrowserService::instance()->create_view(body_);
    } catch (const std::exception& error) {
        status_label_->setText(QStringLiteral("TARAYICI BAŞLATILAMADI: %1").arg(error.what()));
        return;
    }
    view_->setMinimumHeight(430);
    connect(view_, &QWebEngineView::titleChanged, this,
            [this](const QString& title) { on_title(title); });
    connect(view_, &QWebEngineView::urlChanged, this, [this](const QUrl& url) { on_url(url); });
    connect(view_, &QWebEngineView::loadStarted, this,
            [this] { status_label_->setText(QStringLiteral("YÜKLENİYOR…")); });
    connect(view_, &QWebEngineView::loadFinished, this,
            [this](bool ok) { on_load_finished(ok); });
    content_->addWidget(view_, 1);
    update_nav();
}

std::string BrowserModal::normalize_url(const std::string& raw) {
    std::string value = raw;
    size_t a = value.find_first_not_of(" \t\r\n");
    if (a == std::string::npos) return "";
    size_t b = value.find_last_not_of(" \t\r\n");
    value = value.substr(a, b - a + 1);
    if (value.empty()) return "";

    std::string host = value.substr(0, value.find('/'));
    bool looks_like_host =
        host.find('.') != std::string::npos || host.find(':') != std::string::npos ||
        to_lower(host) == "localhost";
    if (value.find(' ') != std::string::npos || !looks_like_host) {
        QString q = QUrl::toPercentEncoding(QString::fromStdString(value));
        return "https://duckduckgo.com/?q=" + q.toStdString();
    }
    if (!std::regex_search(value, std::regex("^https?://", std::regex::icase))) {
        std::string hl = to_lower(host);
        bool local = hl.rfind("localhost", 0) == 0 || hl.rfind("127.0.0.1", 0) == 0;
        value = (local ? "http://" : "https://") + value;
    }
    QUrl parsed(QString::fromStdString(value));
    QString scheme = parsed.scheme().toLower();
    if ((scheme != QStringLiteral("http") && scheme != QStringLiteral("https")) ||
        parsed.host().isEmpty()) {
        return "";
    }
    return value;
}

void BrowserModal::navigate() {
    open_url(address_->text().toStdString());
}

void BrowserModal::open_url(const std::string& raw) {
    std::string target = normalize_url(raw);
    if (target.empty()) {
        status_label_->setText(QStringLiteral("GEÇERSİZ ADRES"));
        return;
    }
    if (view_ == nullptr) return;
    address_->setText(QString::fromStdString(target));
    view_->setUrl(QUrl(QString::fromStdString(target)));
}

void BrowserModal::focus_url() {
    address_->setFocus();
    address_->selectAll();
}

void BrowserModal::on_title(const QString& title) {
    std::string text = title.trimmed().toStdString();
    if (!text.empty()) {
        set_subtitle(text.size() > 90 ? text.substr(0, 90) : text);
    }
}

void BrowserModal::on_url(const QUrl& url) {
    QString value = url.toString();
    if (!value.isEmpty() && value != QStringLiteral("about:blank")) {
        address_->setText(value);
    }
    update_nav();
}

void BrowserModal::on_load_finished(bool ok) {
    status_label_->setText(ok ? QStringLiteral("SAYFA HAZIR")
                              : QStringLiteral("SAYFA YÜKLENEMEDİ"));
    update_nav();
}

void BrowserModal::update_nav() {
    if (view_ == nullptr) return;
    QWebEngineHistory* history = view_->page()->history();
    back_button_->setEnabled(history->canGoBack());
    forward_button_->setEnabled(history->canGoForward());
}

}  // namespace jarvis::ui
