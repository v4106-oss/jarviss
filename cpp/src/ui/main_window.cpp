#include <jarvis/ui/main_window.hpp>

#include <QCloseEvent>
#include <QEvent>
#include <QHBoxLayout>
#include <QLabel>
#include <QLineEdit>
#include <QMessageBox>
#include <QPainter>
#include <QPainterPath>
#include <QPushButton>
#include <QRegion>
#include <QShortcut>
#include <QSizeGrip>
#include <QTimer>
#include <QVBoxLayout>

#include <jarvis/ui/agent_runner.hpp>
#include <jarvis/ui/book_modal.hpp>
#include <jarvis/ui/browser_modal.hpp>
#include <jarvis/ui/camera_modal.hpp>
#include <jarvis/ui/diagnostics_modal.hpp>
#include <jarvis/ui/floating_hud.hpp>
#include <jarvis/ui/home_modal.hpp>
#include <jarvis/ui/hud_chat.hpp>
#include <jarvis/ui/mic_service.hpp>
#include <jarvis/ui/news_stage.hpp>
#include <jarvis/ui/orb.hpp>
#include <jarvis/ui/settings_dialog.hpp>
#include <jarvis/ui/sidebar.hpp>
#include <jarvis/ui/speech_service.hpp>
#include <jarvis/ui/theme.hpp>
#include <jarvis/ui/titlebar.hpp>
#include <jarvis/ui/weather_modal.hpp>

#include <jarvis/speech/text.hpp>

namespace jarvis::ui {

MainWindow::MainWindow(QWidget* parent) : QWidget(parent) {
    const Theme& t = current_theme();

    setWindowTitle(QString::fromUtf8("Jarvis"));
    setWindowFlags(Qt::Window | Qt::FramelessWindowHint);
    setMinimumSize(900, 600);
    resize(1100, 720);

    auto* root = new QVBoxLayout(this);
    root->setContentsMargins(0, 0, 0, 0);
    root->setSpacing(0);

    titlebar_ = new TitleBar(this);
    root->addWidget(titlebar_);

    auto* body = new QHBoxLayout();
    body->setContentsMargins(0, 0, 0, 0);
    body->setSpacing(0);

    sidebar_ = new Sidebar(this);
    body->addWidget(sidebar_);

    auto* center = new QVBoxLayout();
    center->setContentsMargins(24, 20, 24, 20);
    center->setSpacing(12);

    orb_ = new OrbWidget(this);
    center->addWidget(orb_, 1);

    chat_ = new HudChat(this);
    center->addWidget(chat_, 3);

    composer_ = new QLineEdit(this);
    composer_->setPlaceholderText(QString::fromUtf8("Bir şey sor… (Enter ile gönder)"));
    composer_->setMinimumHeight(42);
    composer_->setStyleSheet(
        QStringLiteral("QLineEdit{background:%1;color:%2;border:1px solid %3;border-radius:12px;"
                       "padding:0 14px;font-size:14px;}")
            .arg(QString::fromStdString(t.input), QString::fromStdString(t.text),
                 QString::fromStdString(t.border)));
    center->addWidget(composer_);

    stop_button_ = new QPushButton(QString::fromUtf8("Durdur"), this);
    stop_button_->setCursor(Qt::PointingHandCursor);
    stop_button_->setEnabled(false);
    stop_button_->setStyleSheet(
        QStringLiteral("QPushButton{background:%1;color:%2;border:1px solid %3;"
                       "border-radius:12px;padding:0 18px;font-size:13px;}"
                       "QPushButton:disabled{color:%4;}")
            .arg(QString::fromStdString(t.panel), QString::fromStdString(t.text),
                 QString::fromStdString(t.border), QString::fromStdString(t.muted)));
    stop_button_->setFixedHeight(42);

    // Python'daki mic_status etiketi + mic_button ikilisi: durum yazısı ve F4 düğmesi.
    mic_status_ = new QLabel(QString::fromUtf8("Mikrofon kapalı · F4"), this);
    mic_status_->setFixedHeight(42);
    mic_status_->setAlignment(Qt::AlignRight | Qt::AlignVCenter);
    mic_status_->setStyleSheet(QStringLiteral("QLabel{color:%1;font-size:12px;}")
                                   .arg(QString::fromStdString(t.muted)));

    mic_button_ = new QPushButton(QString::fromUtf8("🎙"), this);
    mic_button_->setCursor(Qt::PointingHandCursor);
    mic_button_->setCheckable(true);
    mic_button_->setFixedHeight(42);
    mic_button_->setFixedWidth(56);
    mic_button_->setToolTip(QString::fromUtf8("Mikrofonu aç (F4)"));
    mic_button_->setStyleSheet(
        QStringLiteral("QPushButton{background:%1;color:%2;border:1px solid %3;"
                       "border-radius:12px;font-size:16px;}"
                       "QPushButton:checked{background:%4;color:%2;border:1px solid %4;}")
            .arg(QString::fromStdString(t.panel), QString::fromStdString(t.text),
                 QString::fromStdString(t.border), QString::fromStdString(t.red)));

    auto* composer_row = new QHBoxLayout();
    composer_row->setContentsMargins(0, 0, 0, 0);
    composer_row->setSpacing(10);
    composer_row->addWidget(composer_, 1);
    composer_row->addWidget(mic_status_);
    composer_row->addWidget(mic_button_);
    composer_row->addWidget(stop_button_);
    center->addLayout(composer_row);

    body->addLayout(center, 1);
    root->addLayout(body, 1);

    hud_ = new FloatingHud(&settings_);
    hud_->on_restore_requested = [this] {
        showNormal();
        raise();
        activateWindow();
    };

    news_ = new NewsStage(this);

    connect(composer_, &QLineEdit::returnPressed, this, [this] { send_current_text(); });
    connect(sidebar_->new_chat_button(), &QPushButton::clicked, this,
            [this] { clear_chat(); });
    connect(sidebar_->settings_button(), &QPushButton::clicked, this,
            [this] { show_settings(); });
    connect(sidebar_->browser_button(), &QPushButton::clicked, this,
            [this] { show_browser(); });
    connect(sidebar_->weather_button(), &QPushButton::clicked, this,
            [this] { show_weather(); });
    connect(sidebar_->camera_button(), &QPushButton::clicked, this,
            [this] { show_camera(); });
    connect(sidebar_->book_button(), &QPushButton::clicked, this, [this] { show_book(); });
    connect(sidebar_->home_button(), &QPushButton::clicked, this, [this] { show_home(); });
    connect(sidebar_->diagnostics_button(), &QPushButton::clicked, this,
            [this] { show_diagnostics(); });
    connect(stop_button_, &QPushButton::clicked, this, [this] {
        runner_->cancel();
        speech_->stop();
    });
    connect(mic_button_, &QPushButton::clicked, this, [this] { toggle_mic(); });

    auto* mic_shortcut = new QShortcut(QKeySequence(Qt::Key_F4), this);
    connect(mic_shortcut, &QShortcut::activated, this, [this] { toggle_mic(); });

    runner_ = new AgentRunner(settings_, home_, this);
    connect_agent();
    speech_ = new SpeechService(settings_, this);
    connect_speech();

    auto* grip = new QSizeGrip(this);
    grip->setFixedSize(16, 16);
    grip->move(width() - 16, height() - 16);
    grip->raise();

    update_mask();
}

MainWindow::~MainWindow() {
    closing_ = true;
    if (mic_ != nullptr && mic_thread_.isRunning()) {
        // Aygıtı yakalayan iş parçacığından bırakmasını iste, sonra onu durdur.
        QMetaObject::invokeMethod(mic_, &MicService::shutdown, Qt::BlockingQueuedConnection);
        mic_thread_.quit();
        mic_thread_.wait();
    }
    mic_ = nullptr;
}

void MainWindow::set_orb_state(const std::string& state) {
    orb_->set_state(state);
}

void MainWindow::set_orb_input_level(double level) {
    orb_->set_input_level(level);
}

void MainWindow::append_user_message(const QString& text) {
    chat_->add_message(text.toStdString(), true);
}

void MainWindow::append_assistant_message(const QString& text) {
    chat_->add_message(text.toStdString(), false);
}

void MainWindow::clear_chat() {
    chat_->clear();
}

void MainWindow::send_current_text() {
    const QString text = composer_->text().trimmed();
    if (text.isEmpty()) {
        return;
    }
    if (!runner_->ask(text.toStdString())) {
        append_assistant_message(QString::fromUtf8(
            "Jarvis hâlâ düşünüyor. İstersen Durdur ile iptal edebilirsin."));
        return;
    }
    speech_->stop();
    // "Haberleri oku" gibi açık isteklerde sınır yok; diğerlerinde kısa yanıt.
    voice_limit_ = jarvis::speech::spoken_limit_for_prompt(text.toStdString());
    append_user_message(text);
    composer_->clear();
    streamed_.clear();
    pending_ = chat_->add_message("🤖 Düşünüyorum...");
    pending_->set_streaming(true);
    orb_->set_state("thinking");
}

// Worker sinyalleri GUI iş parçacığına kuyrukla gelir; burada doğrudan widget'a
// dokunmak güvenlidir.
void MainWindow::connect_agent() {
    connect(runner_, &AgentRunner::started, this, [this] {
        stop_button_->setEnabled(true);
        set_orb_state("thinking");
    });
    connect(runner_, &AgentRunner::chunk_received, this, [this](const QString& piece) {
        streamed_ += piece.toUtf8().toStdString();
        if (pending_) chat_->update_message(pending_, streamed_, false);
    });
    connect(runner_, &AgentRunner::progress_received, this, [this](const QString& line) {
        // Akış başlamadıysa ilerlemeyi düşünme baloncuğunda göster.
        if (pending_ && streamed_.empty()) {
            chat_->update_message(pending_, "🛰 " + line.toUtf8().toStdString(), false);
        }
    });
    connect(runner_, &AgentRunner::response_received, this, [this](const QString& answer) {
        if (!pending_) pending_ = chat_->add_message("");
        chat_->update_message(pending_, answer.toUtf8().toStdString(), true);
        pending_->set_streaming(false);
        pending_ = nullptr;
        streamed_.clear();
        if (settings_.voice_replies() && !answer.trimmed().isEmpty()) {
            speech_->speak(answer.toStdString(), voice_limit_);
        }
        voice_limit_.reset();
    });
    connect(runner_, &AgentRunner::error_received, this, [this](const QString& problem) {
        if (pending_) {
            chat_->update_message(pending_, problem.toUtf8().toStdString(), false);
            pending_->set_streaming(false);
            pending_ = nullptr;
        } else {
            chat_->add_message(problem.toUtf8().toStdString(), false);
        }
        streamed_.clear();
    });
    connect(runner_, &AgentRunner::pause_state_changed, this, [this](const QString& reason) {
        if (!reason.isEmpty()) {
            chat_->add_message("⏸ " + reason.toUtf8().toStdString(), false);
        }
    });
    connect(runner_, &AgentRunner::takeover_received, this, [this](const QString& reason) {
        chat_->add_message("🙋 Devralma istendi: " + reason.toUtf8().toStdString(), false);
    });
    connect(runner_, &AgentRunner::approval_received, this, [this](const QString& description) {
        const auto answer = QMessageBox::question(
            this, QString::fromUtf8("Jarvis onay istiyor"), description,
            QMessageBox::Yes | QMessageBox::No, QMessageBox::No);
        const bool approved = answer == QMessageBox::Yes;
        runner_->set_approval(approved);
        chat_->add_message(std::string(approved ? "✅ Onaylandı: " : "🚫 Reddedildi: ") +
                               description.toUtf8().toStdString(),
                           false);
    });
    connect(runner_, &AgentRunner::browser_open_requested, this,
            [this](const QString& url) { open_in_browser(url); });
    connect(runner_, &AgentRunner::finished, this, [this] {
        stop_button_->setEnabled(false);
        if (pending_) {
            if (streamed_.empty()) {
                chat_->update_message(pending_, "ℹ️ Yanıt alınamadı.", false);
            } else {
                chat_->update_message(pending_, streamed_, true);
            }
            pending_->set_streaming(false);
            pending_ = nullptr;
        }
        streamed_.clear();
        // Seslendirme sürüyorsa orb konuşmaya devam eder.
        set_orb_state(speech_->busy() ? "speaking" : "idle");
    });
}

// Ses hattı sinyalleri GUI iş parçacığında doğar; orb rengi ve HUD yazısı burada bağlı.
void MainWindow::connect_speech() {
    connect(speech_, &SpeechService::started, this, [this] {
        set_orb_state("speaking");
        if (mic_ != nullptr) {
            mic_->set_speaking(true, QString::fromStdString(speech_->spoken_text()));
        }
    });
    connect(speech_, &SpeechService::finished, this, [this] {
        if (mic_ != nullptr) mic_->set_speaking(false);
        if (runner_->busy()) return;
        if (mic_enabled_) {
            // Konuşma bitti: Mikrofon açıksa dinlemeye dön (Python _on_speech_finished).
            set_orb_state("listening");
            QTimer::singleShot(150, this, [this] { start_listening(); });
        } else {
            set_orb_state("idle");
        }
    });
    connect(speech_, &SpeechService::error_occurred, this, [this](const QString& message) {
        hud_->set_state("error");
        hud_->set_subtitle("Ses hatası · " + message.toStdString(), "JARVIS");
    });
}

// --- mikrofon sesli sohbet ---------------------------------------------------

void MainWindow::ensure_mic_service() {
    if (mic_ != nullptr) return;
    // MicService kendi iş parçacığında yaşar; QAudioSource orada oluşturulur.
    // Sinyaller `this` bağlamıyla GUI iş parçacığına kuyruklanır (Python'ın
    // _VoiceBridge'i), düz lambda'lar arası iş parçacığı geçişi çökme üretiyordu.
    mic_ = new MicService(settings_);
    mic_->moveToThread(&mic_thread_);
    connect(&mic_thread_, &QThread::finished, mic_, &QObject::deleteLater);
    connect_mic();
    mic_thread_.start();
}

void MainWindow::connect_mic() {
    connect(mic_, &MicService::level, this, [this](double value) {
        if (!mic_enabled_) return;
        if (orb_->state() == "listening") {
            orb_->set_input_level(value);
            hud_->set_mic_level(value);
        }
    });
    connect(mic_, &MicService::heard, this, [this](const QString& text) {
        if (!mic_enabled_) return;
        const QString said = text.trimmed();
        if (said.isEmpty()) return;
        const bool interrupted = speech_->busy();
        if (interrupted) {
            speech_->stop();
            set_orb_state("listening");
        }
        // Ajan meşgulse cümleyi yut: Python da sırayı bekletip göndermiyor.
        if (runner_->busy()) return;
        apply_mic_style();
        composer_->setText(said);
        send_current_text();
    });
    connect(mic_, &MicService::error_occurred, this, [this](const QString& message) {
        mic_enabled_ = false;
        orb_->set_input_level(0.0);
        apply_mic_style();
        set_orb_state("idle");
        const std::string line = "Mikrofon: " + message.toUtf8().toStdString();
        chat_->add_message("⚠ " + line, false);
        hud_->set_subtitle(line, "JARVIS");
    });
    connect(mic_, &MicService::finished, this, [this] {
        if (closing_) return;
        if (!mic_enabled_) {
            if (!runner_->busy()) set_orb_state("idle");
            return;
        }
        // Boşta 350 ms, meşgulken 900 ms sonra dinlemeye dön (Python _on_voice_finished).
        const int delay = (runner_->busy() || speech_->busy()) ? 900 : 350;
        QTimer::singleShot(delay, this, [this] { start_listening(); });
    });
}

void MainWindow::toggle_mic() {
    mic_enabled_ = !mic_enabled_;
    if (mic_enabled_) {
        ensure_mic_service();
        speech_->stop();
        set_orb_state("listening");
        apply_mic_style();
        start_listening();
    } else {
        stop_voice_session();
        speech_->stop();
        apply_mic_style();
        set_orb_state(runner_->busy() ? "thinking" : "idle");
        hud_->set_subtitle(
            QString::fromUtf8("Mikrofon kapatıldı; sesli yanıt da durduruldu.").toStdString(),
            "JARVIS");
    }
}

void MainWindow::apply_mic_style() {
    if (mic_ == nullptr) {
        // İlk açılışta henüz iş parçacığı yok; HUD yine de durumu göstersin.
        hud_->set_mic_enabled(mic_enabled_);
    } else {
        mic_->set_barge_in(settings_.voice_headphones());
    }
    hud_->set_mic_level(0.0);
    mic_button_->setChecked(mic_enabled_);
    mic_button_->setToolTip(QString::fromUtf8(mic_enabled_ ? "Mikrofonu kapat (F4)"
                                                           : "Mikrofonu aç (F4)"));
    if (!mic_enabled_) {
        mic_status_->setText(QString::fromUtf8("Mikrofon kapalı · F4"));
    } else {
        mic_status_->setText(QString::fromUtf8("Mikrofon açık"));
    }
}

void MainWindow::start_listening() {
    if (!mic_enabled_ || closing_ || mic_ == nullptr) return;
    if (mic_->capturing()) return;
    mic_status_->setText(QString::fromUtf8("Dinliyorum"));
    if (!runner_->busy() && !speech_->busy()) set_orb_state("listening");
    mic_device_active_ = settings_.mic_device();
    mic_->set_barge_in(settings_.voice_headphones());
    QMetaObject::invokeMethod(mic_, &MicService::begin, Qt::QueuedConnection);
}

void MainWindow::stop_voice_session() {
    orb_->set_input_level(0.0);
    hud_->set_mic_level(0.0);
    if (mic_ != nullptr) mic_->request_stop();
}

void MainWindow::apply_audio_settings() {
    speech_->apply_settings();
    if (mic_ == nullptr || !mic_enabled_) return;
    mic_->set_barge_in(settings_.voice_headphones());
    if (settings_.mic_device() == mic_device_active_) return;
    // Aygıt değişti: Python _restart_voice_session — kapat, kısa süre sonra yeniden kur.
    mic_device_active_ = settings_.mic_device();
    stop_voice_session();
    QTimer::singleShot(250, this, [this] { start_listening(); });
}

void MainWindow::show_news_items(const std::vector<NewsItem>& items) {
    news_->show_items(items);
}

void MainWindow::show_settings() {
    auto* dialog = new SettingsDialog(this, settings_);
    dialog->on_theme_changed = [this] {
        update();
        composer_->setStyleSheet(
            QStringLiteral("QLineEdit{background:%1;color:%2;border:1px solid %3;border-radius:12px;"
                           "padding:0 14px;font-size:14px;}")
                .arg(QString::fromStdString(current_theme().input),
                     QString::fromStdString(current_theme().text),
                     QString::fromStdString(current_theme().border)));
    };
    dialog->on_closed = [this] { apply_audio_settings(); };
    dialog->show();
    dialog->raise();
}

void MainWindow::show_browser() {
    if (browser_ == nullptr) {
        browser_ = new BrowserModal(this);
    }
    browser_->show();
    browser_->raise();
}

// Ajan bağlantı istedi: tek tarayıcı penceresi yeniden kullanılır.
void MainWindow::open_in_browser(const QString& url) {
    if (browser_ == nullptr) {
        browser_ = new BrowserModal(this);
    }
    browser_->show();
    browser_->raise();
    browser_->open_url(url.toUtf8().toStdString());
}

void MainWindow::show_weather() {
    WeatherData weather;
    weather.location = "Derince";
    weather.region = "Kocaeli";
    weather.condition = "partly";
    weather.condition_label = "Parçalı bulutlu";
    weather.temp = 16.0;
    weather.feels_like = 15.0;
    weather.low = 13.0;
    weather.high = 18.0;
    weather.humidity = 62;
    weather.wind = 12.0;
    weather.uv = 3;
    weather.rain = 0.4;
    weather.hourly = {{14.0, 0, "Şimdi"}, {14.5, 0, "18:00"}, {15.0, 10, "19:00"},
                   {15.5, 20, "20:00"}, {16.0, 10, "21:00"}, {16.5, 0, "22:00"},
                   {16.0, 0, "23:00"}, {15.5, 0, "00:00"}};
    weather.days = {{"Yarın", "cloud", 12.0, 17.0},
                 {"Cum", "rain", 11.0, 15.0},
                 {"Cmt", "clear", 13.0, 20.0}};
    auto* modal = new WeatherModal(this, weather);
    modal->show();
    modal->raise();
}

void MainWindow::show_camera() {
    auto* modal = new CameraModal(this);
    modal->show();
    modal->raise();
}

void MainWindow::show_book() {
    auto* modal = new BookModal(this, &settings_);
    modal->show();
    modal->raise();
}

void MainWindow::show_home() {
    auto* modal = new HomeModal(this, home_);
    modal->show();
    modal->raise();
}

void MainWindow::show_diagnostics() {
    auto* modal = new DiagnosticsModal(
        this,
        "Jarvis C++ (Qt6) — yerel tanılama.\n\n"
        "Bu sürüm; çekirdek (Modül A), ağ (Modül B), depo (Modül C) ve Qt6 arayüz (Modül D) "
        "katmanlarını içerir. Kamera görüntüleri yalnızca cihazda işlenir; hiçbir kare veya "
        "konuşma buluta gönderilmez.",
        nullptr, settings_.data_dir().string());
    modal->show();
    modal->raise();
}

void MainWindow::paintEvent(QPaintEvent* event) {
    Q_UNUSED(event);
    QPainter painter(this);
    painter.setRenderHint(QPainter::Antialiasing, true);
    painter.fillRect(rect(), parse_color(current_theme().background));
}

void MainWindow::changeEvent(QEvent* event) {
    QWidget::changeEvent(event);
    if (event->type() == QEvent::WindowStateChange) {
        if (isMinimized()) {
            hud_->show_for_minimize();
        } else {
            hud_->hide_for_restore();
        }
    }
}

void MainWindow::closeEvent(QCloseEvent* event) {
    // Python _closing bayrağı: kapanırken yeni dinleme turu başlatma.
    closing_ = true;
    stop_voice_session();
    QWidget::closeEvent(event);
}

void MainWindow::resizeEvent(QResizeEvent* event) {
    QWidget::resizeEvent(event);
    update_mask();
}

void MainWindow::update_mask() {
    const double radius = current_theme().window_radius;
    QPainterPath path;
    path.addRoundedRect(QRectF(rect()), radius, radius);
    setMask(QRegion(path.toFillPolygon().toPolygon()));
}

}  // namespace jarvis::ui
