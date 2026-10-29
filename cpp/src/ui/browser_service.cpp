#include <jarvis/ui/browser_service.hpp>

#include <jarvis/ui/theme.hpp>

#include <QDir>
#include <QEventLoop>
#include <QMetaObject>
#include <QSizePolicy>
#include <QThread>
#include <QTimer>
#include <QVBoxLayout>
#include <QWebChannel>
#include <QWebEnginePage>
#include <QWebEnginePermission>
#include <QWebEngineProfile>
#include <QWebEngineSettings>
#include <QWebEngineView>
#include <QWidget>

#include <algorithm>
#include <cmath>
#include <regex>

namespace jarvis::ui {

namespace {

const char* kReaderJs =
    R"JS((function(){var body=document.body?document.body.innerText:'';body=(body||'').replace(/\s+/g,' ').trim().slice(0,12000);return body;})())JS";

QString browser_data_dir() {
    QString base = qEnvironmentVariable("LOCALAPPDATA");
    if (base.isEmpty()) {
        base = QDir::homePath() + QStringLiteral("/AppData/Local");
    }
    QString path = base + QStringLiteral("/Jarvis/browser");
    QDir().mkpath(path + QStringLiteral("/profile"));
    QDir().mkpath(path + QStringLiteral("/cache"));
    return path;
}

}  // namespace

// JS tarafından çağrılan köprü; QWebChannel meta-nesne sistemini kullandığından
// Q_OBJECT gerektirir (projenin geri kalanındaki std::function kalıbının tek
// bilinçli istisnası).
class MapBridge : public QObject {
    Q_OBJECT
public:
    explicit MapBridge(QObject* parent = nullptr) : QObject(parent) {}

    std::function<void(double, double, double)> on_poi_clicked;
    std::function<void(const std::string&, double, double, double)> on_category_requested;
    std::function<void(double, double, const std::string&)> on_route_requested;
    std::function<void(const std::string&)> on_external_link_requested;

public slots:
    void poiClick(double lat, double lon, double zoom) {
        if (on_poi_clicked) on_poi_clicked(lat, lon, zoom);
    }
    void categoryClick(const QString& category, double lat, double lon, double zoom) {
        if (on_category_requested) on_category_requested(category.toStdString(), lat, lon, zoom);
    }
    void routeTo(double lat, double lon, const QString& name) {
        if (on_route_requested) {
            on_route_requested(lat, lon, name.isEmpty() ? std::string("Hedef") : name.toStdString());
        }
    }
    void openExternalLink(const QString& url) {
        if (on_external_link_requested) on_external_link_requested(url.toStdString());
    }
};

BrowserService::BrowserService() = default;

BrowserService* BrowserService::instance() {
    static BrowserService* service = new BrowserService();
    return service;
}

void BrowserService::ensure_profile() {
    if (profile_ != nullptr) return;
    QString data = browser_data_dir();
    auto* profile = new QWebEngineProfile(QStringLiteral("jarvis"), this);
    profile->setPersistentStoragePath(data + QStringLiteral("/profile"));
    profile->setCachePath(data + QStringLiteral("/cache"));
    profile->setHttpCacheType(QWebEngineProfile::HttpCacheType::DiskHttpCache);
    profile->setHttpCacheMaximumSize(256 * 1024 * 1024);
    profile->setPersistentCookiesPolicy(
        QWebEngineProfile::PersistentCookiesPolicy::AllowPersistentCookies);
    profile_ = profile;
}

void BrowserService::configure_page(QWebEnginePage* page, bool local_files) {
    page->setBackgroundColor(parse_color(current_theme().background));
    QWebEngineSettings* settings = page->settings();
    settings->setAttribute(QWebEngineSettings::WebAttribute::JavascriptEnabled, true);
    settings->setAttribute(QWebEngineSettings::WebAttribute::LocalContentCanAccessRemoteUrls, true);
    settings->setAttribute(QWebEngineSettings::WebAttribute::LocalContentCanAccessFileUrls,
                           local_files);
    settings->setAttribute(QWebEngineSettings::WebAttribute::ScrollAnimatorEnabled, true);
    settings->setAttribute(QWebEngineSettings::WebAttribute::JavascriptCanOpenWindows, false);
    // Jarvis tarayıcısı kamera/mikrofon/bildirim vb. yetkileri istemeden reddeder.
    connect(page, &QWebEnginePage::permissionRequested, page,
            [](QWebEnginePermission permission) { permission.deny(); });
}

void BrowserService::ensure_view(QWidget* parent) {
    if (view_ != nullptr) return;
    ensure_profile();
    host_ = new QWidget(parent);
    host_->setObjectName(QStringLiteral("jarvisBrowserHost"));
    host_->hide();
    host_layout_ = new QVBoxLayout(host_);
    host_layout_->setContentsMargins(0, 0, 0, 0);
    host_layout_->setSpacing(0);

    auto* view = new QWebEngineView(host_);
    auto* page = new QWebEnginePage(profile_, view);
    view->setPage(page);
    view->setContextMenuPolicy(Qt::ContextMenuPolicy::NoContextMenu);
    view->setSizePolicy(QSizePolicy::Policy::Expanding, QSizePolicy::Policy::Expanding);
    view->hide();
    configure_page(page, true);
    connect(page, &QWebEnginePage::loadFinished, this, &BrowserService::on_map_load_finished);

    auto* channel = new QWebChannel(page);
    page->setWebChannel(channel);
    auto* bridge = new MapBridge(this);
    channel->registerObject(QStringLiteral("mapBridge"), bridge);
    bridge->on_poi_clicked = [this](double lat, double lon, double zoom) {
        if (on_poi_clicked) on_poi_clicked(lat, lon, zoom);
    };
    bridge->on_category_requested =
        [this](const std::string& category, double lat, double lon, double zoom) {
            if (on_category_requested) on_category_requested(category, lat, lon, zoom);
        };
    bridge->on_route_requested = [this](double lat, double lon, const std::string& name) {
        if (on_route_requested) on_route_requested(lat, lon, name);
    };
    bridge->on_external_link_requested = [this](const std::string& url) {
        if (on_external_link_requested) on_external_link_requested(url);
    };

    view_ = view;
    page_ = page;
    channel_ = channel;
    bridge_ = bridge;
    host_layout_->addWidget(view);
}

QWebEngineView* BrowserService::create_view(QWidget* parent) {
    ensure_profile();
    auto* view = new QWebEngineView(parent);
    auto* page = new QWebEnginePage(profile_, view);
    view->setPage(page);
    view->setContextMenuPolicy(Qt::ContextMenuPolicy::DefaultContextMenu);
    view->setSizePolicy(QSizePolicy::Policy::Expanding, QSizePolicy::Policy::Expanding);
    configure_page(page, false);
    return view;
}

void BrowserService::prewarm() {
    try {
        ensure_profile();
    } catch (...) {
    }
}

bool BrowserService::map_loaded() const {
    return map_loaded_;
}

std::string BrowserService::map_error() const {
    return map_error_;
}

QWebEngineView* BrowserService::attach_map(QVBoxLayout* layout, QWidget* parent) {
    QWidget* host_parent = layout != nullptr ? layout->parentWidget() : nullptr;
    ensure_view(host_parent != nullptr ? host_parent : parent);
    if (attached_layout_ != layout) {
        if (attached_layout_ != nullptr) {
            attached_layout_->removeWidget(host_);
        }
        layout->addWidget(host_);
        attached_layout_ = layout;
    }
    if (map_loaded_) {
        show_map();
    }
    return view_;
}

void BrowserService::show_map() {
    if (host_ != nullptr) host_->show();
    if (view_ != nullptr) view_->show();
}

void BrowserService::detach_map() {
    QWidget* host = host_;
    if (host == nullptr) return;
    if (attached_layout_ != nullptr) {
        attached_layout_->removeWidget(host);
    }
    host->hide();
    if (view_ != nullptr) view_->hide();
    attached_layout_ = nullptr;
}

void BrowserService::load_map(const std::string& html, const QUrl& base_url) {
    if (map_loaded_ || map_loading_ || view_ == nullptr) return;
    map_loading_ = true;
    map_error_.clear();
    page_->setHtml(QString::fromStdString(html), base_url);
}

void BrowserService::run_js(const std::string& script) {
    if (page_ == nullptr) return;
    page_->runJavaScript(QString::fromStdString(script));
}

bool BrowserService::sync_home(double lat, double lon, const std::string& name, bool is_set) {
    QString lat_s = QString::number(lat, 'f', 6);
    QString lon_s = QString::number(lon, 'f', 6);
    std::string signature =
        lat_s.toStdString() + "," + lon_s.toStdString() + "," + name + (is_set ? "1" : "0");
    bool changed = signature != last_home_signature_;
    last_home_signature_ = signature;

    QString name_q = QString::fromStdString(name);
    name_q.replace("\\", "\\\\").replace("\"", "\\\"");
    QString name_json = QStringLiteral("\"") + name_q + QStringLiteral("\"");
    QString script =
        QStringLiteral("window.jarvisSetHome && window.jarvisSetHome(%1, %2, %3, %4);")
            .arg(lat_s, lon_s, name_json, is_set ? QStringLiteral("true") : QStringLiteral("false"));
    run_js(script.toStdString());
    return changed;
}

void BrowserService::on_map_load_finished(bool ok) {
    if (!map_loading_) return;
    map_loading_ = false;
    map_loaded_ = ok;
    if (!ok) {
        map_error_ = "harita sayfası yüklenemedi";
    }
    if (on_map_ready) {
        on_map_ready(ok);
    }
}

std::string BrowserService::read_url(const std::string& url, int timeout_ms) {
    std::string target = url;
    size_t a = target.find_first_not_of(" \t\r\n");
    if (a == std::string::npos) return "❌ Tarayıcı yalnızca http/https adreslerini okuyabilir.";
    size_t b = target.find_last_not_of(" \t\r\n");
    target = target.substr(a, b - a + 1);
    if (!std::regex_match(target, std::regex("https?://.*", std::regex::icase))) {
        return "❌ Tarayıcı yalnızca http/https adreslerini okuyabilir.";
    }
    int timeout = std::max(3000, timeout_ms);
    if (QThread::currentThread() == thread()) {
        return read_url_blocking(target, timeout);
    }
    std::string result;
    QMetaObject::invokeMethod(this, [&]() { result = read_url_blocking(target, timeout); },
                              Qt::ConnectionType::BlockingQueuedConnection);
    return result;
}

void BrowserService::ensure_reader() {
    if (reader_page_ != nullptr) return;
    ensure_profile();
    auto* page = new QWebEnginePage(profile_, this);
    connect(page, &QWebEnginePage::loadFinished, this, &BrowserService::on_reader_load_finished);
    reader_page_ = page;
}

void BrowserService::release_reader() {
    QWebEnginePage* page = reader_page_;
    reader_page_ = nullptr;
    if (page == nullptr) return;
    disconnect(page, nullptr, this, nullptr);
    page->triggerAction(QWebEnginePage::WebAction::Stop);
    page->deleteLater();
}

std::string BrowserService::read_url_blocking(const std::string& url, int timeout_ms) {
    if (reader_busy_) {
        return "❌ Tarayıcı zaten başka bir sayfayı okuyor; biraz sonra tekrar dene.";
    }
    try {
        ensure_reader();
    } catch (const std::exception& error) {
        return "❌ Tarayıcı hazırlanamadı: " + std::string(error.what());
    }
    reader_busy_ = true;
    reader_done_ = false;
    reader_result_.clear();
    QEventLoop loop;
    reader_loop_ = &loop;
    QTimer timer;
    timer.setSingleShot(true);
    connect(&timer, &QTimer::timeout, &loop, &QEventLoop::quit);
    timer.start(timeout_ms);
    reader_page_->load(QUrl(QString::fromStdString(url)));
    loop.exec();
    timer.stop();
    reader_loop_ = nullptr;
    reader_busy_ = false;
    release_reader();
    if (!reader_done_) {
        return "❌ Tarayıcı zaman aşımına uğradı: " + url;
    }
    if (reader_result_.empty()) {
        return "❌ Tarayıcı sayfadan metin okuyamadı: " + url;
    }
    return reader_result_;
}

void BrowserService::on_reader_load_finished(bool ok) {
    if (!reader_busy_ || reader_loop_ == nullptr || reader_done_) return;
    QWebEnginePage* page = reader_page_;
    if (!ok) {
        reader_done_ = true;
        reader_result_ =
            "❌ Tarayıcı sayfayı yükleyemedi: " + page->url().toString().toStdString();
        reader_loop_->quit();
        return;
    }
    std::string final_url = page->url().toString().toStdString();
    std::string title = page->title().toStdString();
    page->runJavaScript(QString::fromUtf8(kReaderJs),
                        [this, final_url, title](const QVariant& value) {
                            if (reader_loop_ == nullptr) return;
                            std::string text = value.toString().toStdString();
                            size_t a = text.find_first_not_of(" \t\r\n");
                            if (a == std::string::npos) {
                                text.clear();
                            } else {
                                size_t b = text.find_last_not_of(" \t\r\n");
                                text = text.substr(a, b - a + 1);
                            }
                            if (text.empty()) {
                                reader_result_ =
                                    "❌ Sayfada okunabilir metin yok: " + final_url;
                            } else {
                                std::string header = "SAYFA: " +
                                                     (title.empty() ? std::string("(başlıksız)")
                                                                    : title) +
                                                     "\nURL: " + final_url + "\n\n";
                                reader_result_ = header + text.substr(0, 14000);
                            }
                            reader_done_ = true;
                            if (reader_loop_ != nullptr) {
                                reader_loop_->quit();
                            }
                        });
}

}  // namespace jarvis::ui

#include "browser_service.moc"
