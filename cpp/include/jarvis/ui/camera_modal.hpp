#pragma once

#include <jarvis/ui/hud_modal.hpp>

#include <QCameraDevice>
#include <QList>

#include <memory>

class QCamera;
class QComboBox;
class QMediaCaptureSession;
class QVideoWidget;

namespace jarvis::ui {

class HudButton;

// Kamera paneli; QtMultimedia ile canlı önizleme gösterir.
// Python app/ui/camera_window.py karşılığı (ML el/yüz HUD'si kapsam dışıdır).
class CameraModal : public HudModal {
public:
    explicit CameraModal(QWidget* parent, int width = 720, int height = 560);
    ~CameraModal() override;

private:
    void populate_cameras();
    void start_camera(int index);
    void stop_camera();

    QComboBox* camera_combo_ = nullptr;
    QVideoWidget* video_ = nullptr;
    HudButton* start_button_ = nullptr;
    std::unique_ptr<QMediaCaptureSession> session_;
    std::unique_ptr<QCamera> camera_;
    QList<QCameraDevice> devices_;
    int active_index_ = -1;
};

}  // namespace jarvis::ui
