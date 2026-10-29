#pragma once

// AgentRunner — Qt arayüzü ile AI/iş yürütücü hattı arasındaki QObject köprüsü.
//
// AIWorker kendi iş parçacığında çalışır; oradan gelen metin parçaları doğrudan
// widget'a dokunmaz. Worker geri çağırmaları bu sınıfın sinyallerini yayınlar,
// Qt kuyruklu bağlantıyla GUI iş parçacığına taşır. (Düz lambda ile iş parçacığı
// değiştirmek bu uygulamada çökmeye yol açıyordu.)

#include <functional>
#include <memory>
#include <string>

#include <QObject>
#include <QString>

namespace jarvis::core {
class Settings;
}
namespace jarvis::home {
class SmartHome;
}

namespace jarvis::ui {

class AgentRunner : public QObject {
    Q_OBJECT
public:
    AgentRunner(jarvis::core::Settings& settings, jarvis::home::SmartHome& home,
                QObject* parent = nullptr);
    ~AgentRunner() override;

    // Yeni bir soru/istem başlatır. Mevcut koşu sürüyorsa false döner.
    bool ask(const std::string& prompt);
    bool busy() const;

    void cancel();
    void pause();
    void resume();
    void set_approval(bool approved);

signals:
    void started();
    void chunk_received(const QString& text);
    void response_received(const QString& text);
    void progress_received(const QString& text);
    void error_received(const QString& text);
    void approval_received(const QString& text);
    void takeover_received(const QString& text);
    void pause_state_changed(const QString& text);
    void browser_open_requested(const QString& url);
    void finished();

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace jarvis::ui
