#pragma once

#include <QObject>
#include <QString>
#include <QUrl>

#include <functional>
#include <string>

class QEventLoop;
class QTimer;
class QVBoxLayout;
class QWebChannel;
class QWebEnginePage;
class QWebEngineProfile;
class QWebEngineView;
class QWidget;

namespace jarvis::ui {

class MapBridge;

// Uygulama içi tek motorlu tarayıcı servisi. Harita, görünür tarayıcı ve ajan
// okumaları aynı kalıcı QWebEngineProfile'ı paylaşır; ikinci bir Chromium
// motoru doğmaz (Python app/ui/browser_service.py karşılığı).
class BrowserService : public QObject {
public:
    static BrowserService* instance();

    // Aynı profili kullanan bağımsız bir tarayıcı görünümü kurar.
    QWebEngineView* create_view(QWidget* parent);

    // Profil/önbellek motorunu arka planda hazırlar; görünüm oluşturmaz.
    void prewarm();

    bool map_loaded() const;
    std::string map_error() const;

    QWebEngineView* attach_map(QVBoxLayout* layout, QWidget* parent);
    void show_map();
    void detach_map();
    void load_map(const std::string& html, const QUrl& base_url);
    void run_js(const std::string& script);
    bool sync_home(double lat, double lon, const std::string& name, bool is_set);

    // Sayfayı gizli tarayıcıda açıp görünür metnini döndürür (ajan okuması).
    std::string read_url(const std::string& url, int timeout_ms = 20000);

    std::function<void(bool)> on_map_ready;
    std::function<void(double, double, double)> on_poi_clicked;
    std::function<void(const std::string&, double, double, double)> on_category_requested;
    std::function<void(double, double, const std::string&)> on_route_requested;
    std::function<void(const std::string&)> on_external_link_requested;

private:
    BrowserService();

    void ensure_profile();
    void configure_page(QWebEnginePage* page, bool local_files);
    void ensure_view(QWidget* parent);
    void ensure_reader();
    void release_reader();
    std::string read_url_blocking(const std::string& url, int timeout_ms);
    void on_map_load_finished(bool ok);
    void on_reader_load_finished(bool ok);
    void home_clicked(double lat, double lon);

    QWebEngineProfile* profile_ = nullptr;
    QWidget* host_ = nullptr;
    QVBoxLayout* host_layout_ = nullptr;
    QVBoxLayout* attached_layout_ = nullptr;
    QWebEngineView* view_ = nullptr;
    QWebEnginePage* page_ = nullptr;
    QWebChannel* channel_ = nullptr;
    MapBridge* bridge_ = nullptr;
    bool map_loading_ = false;
    bool map_loaded_ = false;
    std::string map_error_;
    std::string last_home_signature_;

    QWebEnginePage* reader_page_ = nullptr;
    bool reader_busy_ = false;
    bool reader_done_ = false;
    std::string reader_result_;
    QEventLoop* reader_loop_ = nullptr;
};

}  // namespace jarvis::ui
