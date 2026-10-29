#pragma once

#include <jarvis/ui/hud_modal.hpp>

#include <functional>
#include <string>

class QLabel;
class QPlainTextEdit;

namespace jarvis::ui {

// Yerel tanılama ve gizlilik raporunu gösteren HUD penceresi.
class DiagnosticsModal : public HudModal {
public:
    DiagnosticsModal(QWidget* parent, const std::string& text,
                     std::function<std::string()> refresh = nullptr,
                     const std::string& data_dir = std::string());

private:
    void copy_report();
    void reload();
    void open_folder();

    std::function<std::string()> refresh_;
    std::string data_dir_;
    QPlainTextEdit* editor_ = nullptr;
    QLabel* notice_ = nullptr;
};

}  // namespace jarvis::ui
