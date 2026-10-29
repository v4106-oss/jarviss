#include <jarvis/ui/camera_modal.hpp>

#include <QComboBox>
#include <QHBoxLayout>
#include <QLabel>
#include <QVBoxLayout>

#include <QCamera>
#include <QMediaCaptureSession>
#include <QMediaDevices>
#include <QVideoWidget>

#include <jarvis/ui/hud_widgets.hpp>
#include <jarvis/ui/theme.hpp>

namespace jarvis::ui {

CameraModal::CameraModal(QWidget* parent, int width, int height)
    : HudModal(parent, "Kamera",
               "Canlı kamera önizlemesi. El/yüz algılama HUD'si bu C++ sürümünde henüz yok.",
               width, height, 24, true, false, false) {
    const Theme& theme = current_theme();

    auto* toolbar = new QHBoxLayout();
    toolbar->setSpacing(10);
    camera_combo_ = new QComboBox();
    camera_combo_->setSizeAdjustPolicy(QComboBox::SizeAdjustPolicy::AdjustToContents);
    camera_combo_->setStyleSheet(
        QStringLiteral("QComboBox{background:%1;color:%2;border:1px solid %3;border-radius:8px;"
                       "padding:6px 10px;}")
            .arg(QString::fromStdString(theme.input), QString::fromStdString(theme.text),
                 QString::fromStdString(theme.border)));
    toolbar->addWidget(camera_combo_, 1);

    start_button_ = new HudButton(QStringLiteral("Başlat"));
    toolbar->addWidget(start_button_);
    content_->addLayout(toolbar);

    video_ = new QVideoWidget();
    video_->setMinimumHeight(280);
    video_->setStyleSheet(
        QStringLiteral("QVideoWidget{background:%1;border:1px solid %2;border-radius:12px;}")
            .arg(QString::fromStdString(theme.background_2),
                 QString::fromStdString(theme.border)));
    content_->addWidget(video_, 1);

    auto* note = new QLabel(
        QStringLiteral("Kamera görüntüsü yalnızca bu cihazda gösterilir; hiçbir kare buluta gönderilmez."));
    note->setWordWrap(true);
    note->setStyleSheet(
        QStringLiteral("color:%1;font-size:11px;").arg(QString::fromStdString(theme.muted)));
    content_->addWidget(note);

    populate_cameras();
    connect(camera_combo_, QOverload<int>::of(&QComboBox::currentIndexChanged), this,
            [this](int index) {
                if (index >= 0 && start_button_->text() == QStringLiteral("Durdur")) {
                    start_camera(index);
                }
            });
    connect(start_button_, &QPushButton::clicked, this, [this] {
        if (camera_ && camera_->isActive()) {
            stop_camera();
        } else {
            start_camera(camera_combo_->currentIndex());
        }
    });
}

CameraModal::~CameraModal() {
    stop_camera();
}

void CameraModal::populate_cameras() {
    devices_ = QMediaDevices::videoInputs();
    camera_combo_->clear();
    if (devices_.isEmpty()) {
        camera_combo_->addItem(QStringLiteral("Kamera bulunamadı"));
        camera_combo_->setEnabled(false);
        start_button_->setEnabled(false);
        return;
    }
    for (int i = 0; i < devices_.size(); ++i) {
        camera_combo_->addItem(QString::fromStdString(devices_.at(i).description().toStdString()),
                               i);
    }
    const QCameraDevice default_device = QMediaDevices::defaultVideoInput();
    for (int i = 0; i < devices_.size(); ++i) {
        if (devices_.at(i) == default_device) {
            camera_combo_->setCurrentIndex(i);
            break;
        }
    }
}

void CameraModal::start_camera(int index) {
    stop_camera();
    if (index < 0 || index >= devices_.size()) {
        return;
    }
    camera_ = std::make_unique<QCamera>(devices_.at(index));
    session_ = std::make_unique<QMediaCaptureSession>();
    session_->setCamera(camera_.get());
    session_->setVideoOutput(video_);
    camera_->start();
    active_index_ = index;
    start_button_->setText(QStringLiteral("Durdur"));
}

void CameraModal::stop_camera() {
    if (camera_) {
        camera_->stop();
    }
    session_.reset();
    camera_.reset();
    active_index_ = -1;
    if (start_button_) {
        start_button_->setText(QStringLiteral("Başlat"));
    }
}

}  // namespace jarvis::ui
