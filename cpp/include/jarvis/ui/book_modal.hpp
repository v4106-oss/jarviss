#pragma once

#include <jarvis/ui/hud_modal.hpp>

#include <QCameraDevice>
#include <QList>

#include <memory>
#include <string>

class QCamera;
class QComboBox;
class QLabel;
class QLineEdit;
class QMediaCaptureSession;
class QPlainTextEdit;
class QSpinBox;
class QVideoWidget;

namespace jarvis::core {
class Settings;
}

namespace jarvis::ui {

class HudButton;

// Kitap tarayıcı paneli: kamera önizlemesi + künye/özet arayüzü.
// Python app/ui/book_modal.py karşılığı. Kitap servisi/OCR/AI arka uçları bu
// C++ sürümünde henüz port edilmediğinden arama/tarama işlemleri durum
// mesajıyla bildirilir; kamera önizlemesi tam çalışır.
class BookModal : public HudModal {
public:
    explicit BookModal(QWidget* parent, jarvis::core::Settings* settings = nullptr,
                       bool auto_start = true);
    ~BookModal() override;

protected:
    void closeEvent(QCloseEvent* event) override;

private:
    void build_body();
    QWidget* build_camera_card();
    QWidget* build_book_card();
    void populate_devices();
    void start_camera(int index);
    void stop_camera();
    void toggle_camera();
    void set_status(const std::string& text, bool error = false);

    jarvis::core::Settings* settings_ = nullptr;

    QComboBox* device_combo_ = nullptr;
    HudButton* camera_toggle_ = nullptr;
    QVideoWidget* video_ = nullptr;
    HudButton* cover_button_ = nullptr;
    HudButton* page_button_ = nullptr;
    QSpinBox* page_spin_ = nullptr;
    QComboBox* import_mode_ = nullptr;
    QLabel* pages_label_ = nullptr;
    QLabel* status_label_ = nullptr;
    QLabel* last_scan_label_ = nullptr;

    QLineEdit* search_input_ = nullptr;
    HudButton* search_button_ = nullptr;
    QLabel* cover_label_ = nullptr;
    QLabel* title_label_ = nullptr;
    QLabel* authors_label_ = nullptr;
    QLabel* meta_label_ = nullptr;
    QLabel* source_label_ = nullptr;
    QLabel* toc_label_ = nullptr;
    QLabel* description_label_ = nullptr;
    QPlainTextEdit* summary_edit_ = nullptr;
    QLabel* summary_state_ = nullptr;
    HudButton* profile_button_ = nullptr;
    HudButton* pages_button_ = nullptr;
    HudButton* save_button_ = nullptr;
    HudButton* copy_button_ = nullptr;
    QComboBox* library_combo_ = nullptr;

    std::unique_ptr<QMediaCaptureSession> session_;
    std::unique_ptr<QCamera> camera_;
    QList<QCameraDevice> devices_;
    int active_index_ = -1;
    bool closing_ = false;
};

}  // namespace jarvis::ui
