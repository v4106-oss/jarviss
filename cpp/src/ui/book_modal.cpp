#include <jarvis/ui/book_modal.hpp>

#include <QApplication>
#include <QCamera>
#include <QClipboard>
#include <QComboBox>
#include <QCloseEvent>
#include <QHBoxLayout>
#include <QLabel>
#include <QLineEdit>
#include <QMediaCaptureSession>
#include <QMediaDevices>
#include <QPlainTextEdit>
#include <QSpinBox>
#include <QTimer>
#include <QVBoxLayout>
#include <QVideoWidget>

#include <jarvis/core/settings.hpp>
#include <jarvis/ui/hud_widgets.hpp>
#include <jarvis/ui/theme.hpp>

namespace jarvis::ui {

namespace {

std::string combo_style() {
    const Theme& theme = current_theme();
    return "QComboBox{background:" + theme.input + ";color:" + theme.text_soft +
           ";border:1px solid " + theme.border + ";border-radius:6px;padding:5px 10px;" +
           "font-size:11px;}"
           "QComboBox:hover{border-color:" + theme.red_dim + ";}"
           "QComboBox::drop-down{border:none;width:18px;}"
           "QComboBox QAbstractItemView{background:" + theme.panel_2 + ";color:" + theme.text_soft +
           ";border:1px solid " + theme.border + ";selection-background-color:" + theme.red_dim +
           ";selection-color:" + theme.text + ";outline:none;}";
}

std::string edit_style() {
    const Theme& theme = current_theme();
    return "QLineEdit,QSpinBox{background:" + theme.input + ";color:" + theme.text +
           ";border:1px solid " + theme.border + ";border-radius:6px;padding:6px 10px;" +
           "font-size:11px;selection-background-color:" + theme.red_dim + ";}"
           "QLineEdit:focus,QSpinBox:focus{border-color:" + theme.red_soft + ";}"
           "QSpinBox::up-button,QSpinBox::down-button{width:0;height:0;}";
}

std::string summary_style() {
    const Theme& theme = current_theme();
    return "QPlainTextEdit{background:" + theme.input + ";color:" + theme.text +
           ";border:1px solid " + theme.border + ";border-radius:7px;padding:10px;" +
           "font-size:11px;selection-background-color:" + theme.red_dim + ";}"
           "QPlainTextEdit:focus{border-color:" + theme.red_soft + ";}";
}

}  // namespace

BookModal::BookModal(QWidget* parent, jarvis::core::Settings* settings, bool auto_start)
    : HudModal(parent, "Kitap Tarayıcı", "KAMERA · ISBN · OCR · KİŞİSEL ÖZET", 1240, 780, 24,
               false, false, false),
      settings_(settings) {
    build_body();
    populate_devices();
    if (auto_start) {
        QTimer::singleShot(180, this, [this] { start_camera(0); });
    } else {
        set_status("Kamera kapalı. ISBN/künye servisi bu C++ sürümünde henüz yok.");
    }
}

BookModal::~BookModal() { stop_camera(); }

void BookModal::build_body() {
    auto* row = new QHBoxLayout();
    row->setSpacing(14);
    row->addWidget(build_camera_card(), 3);
    row->addWidget(build_book_card(), 2);
    content_->addLayout(row, 1);
}

QWidget* BookModal::build_camera_card() {
    auto* holder = new Panel(12);
    QVBoxLayout* layout = holder->body();
    layout->setSpacing(8);

    video_ = new QVideoWidget();
    video_->setMinimumHeight(220);
    const Theme& theme = current_theme();
    video_->setStyleSheet(
        QStringLiteral("QVideoWidget{background:%1;border:1px solid %2;border-radius:12px;}")
            .arg(QString::fromStdString(theme.background_2),
                 QString::fromStdString(theme.border)));
    layout->addWidget(video_, 1);

    auto* device_row = new QHBoxLayout();
    device_row->setSpacing(8);
    device_row->addWidget(hud_label("Kamera", 7, theme.dim, QFont::Weight::Medium, 0.2));
    device_combo_ = new QComboBox();
    device_combo_->setStyleSheet(QString::fromStdString(combo_style()));
    device_combo_->setMinimumWidth(170);
    QObject::connect(device_combo_, QOverload<int>::of(&QComboBox::currentIndexChanged), this,
                     [this](int index) {
                         if (index >= 0 && camera_ && camera_->isActive()) {
                             start_camera(index);
                         }
                     });
    device_row->addWidget(device_combo_, 1);
    camera_toggle_ = new HudButton(QStringLiteral("BAŞLAT"), false, true);
    QObject::connect(camera_toggle_, &QPushButton::clicked, this,
                     [this] { toggle_camera(); });
    device_row->addWidget(camera_toggle_);
    layout->addLayout(device_row);

    auto* scan_row = new QHBoxLayout();
    scan_row->setSpacing(8);
    cover_button_ = new HudButton(QStringLiteral("KAPAK / ISBN TARA"), true);
    page_button_ = new HudButton(QStringLiteral("SAYFA TARA"));
    page_spin_ = new QSpinBox();
    page_spin_->setRange(1, 9999);
    page_spin_->setValue(1);
    page_spin_->setStyleSheet(QString::fromStdString(edit_style()));
    page_spin_->setFixedWidth(74);
    QObject::connect(cover_button_, &QPushButton::clicked, this, [this] {
        set_status("Kapak/ISBN tanıma bu C++ sürümünde henüz yok; kamera önizlemesi açık.",
                   true);
    });
    QObject::connect(page_button_, &QPushButton::clicked, this, [this] {
        set_status("Sayfa OCR motoru bu C++ sürümünde henüz port edilmedi.", true);
    });
    scan_row->addWidget(cover_button_, 2);
    scan_row->addWidget(page_button_, 2);
    scan_row->addWidget(page_spin_);
    layout->addLayout(scan_row);

    auto* file_row = new QHBoxLayout();
    file_row->setSpacing(8);
    import_mode_ = new QComboBox();
    import_mode_->addItems({QStringLiteral("SAYFA"), QStringLiteral("KAPAK / ISBN")});
    import_mode_->setStyleSheet(QString::fromStdString(combo_style()));
    import_mode_->setFixedWidth(140);
    file_row->addWidget(import_mode_);
    file_row->addStretch(1);
    layout->addLayout(file_row);

    pages_label_ = hud_label("Taranan sayfa yok · sayfa OCR'ı bu sürümde yok.", 8, theme.muted,
                             QFont::Weight::Medium, 0.6);
    pages_label_->setWordWrap(true);
    layout->addWidget(pages_label_);

    status_label_ = hud_label("", 8, theme.text_soft, QFont::Weight::Medium, 0.5);
    status_label_->setWordWrap(true);
    layout->addWidget(status_label_);

    last_scan_label_ = hud_label("", 7, theme.dim, QFont::Weight::Medium, 0.4);
    last_scan_label_->setWordWrap(true);
    layout->addWidget(last_scan_label_);

    auto* privacy = hud_label(
        "GİZLİLİK · OCR metni yalnızca bellekte tutulur; kapatınca silinir. Kamera görüntüsü "
        "yalnızca bu cihazda gösterilir; hiçbir kare buluta gönderilmez.",
        7, theme.faint, QFont::Weight::Medium, 0.5);
    privacy->setWordWrap(true);
    layout->addWidget(privacy);
    return holder;
}

QWidget* BookModal::build_book_card() {
    auto* holder = new Panel(12);
    QVBoxLayout* layout = holder->body();
    layout->setSpacing(9);
    const Theme& theme = current_theme();

    auto* search_row = new QHBoxLayout();
    search_row->setSpacing(8);
    search_input_ = new QLineEdit();
    search_input_->setPlaceholderText(QString::fromUtf8("ISBN veya kitap adı"));
    search_input_->setStyleSheet(QString::fromStdString(edit_style()));
    search_button_ = new HudButton(QStringLiteral("ARA"), true, true);
    QObject::connect(search_input_, &QLineEdit::returnPressed, this, [this] {
        set_status("Kitap servisi bu C++ sürümünde henüz yok; ISBN/künye araması kapalı.", true);
    });
    QObject::connect(search_button_, &QPushButton::clicked, this, [this] {
        set_status("Kitap servisi bu C++ sürümünde henüz yok; ISBN/künye araması kapalı.", true);
    });
    search_row->addWidget(search_input_, 1);
    search_row->addWidget(search_button_);
    layout->addLayout(search_row);

    auto* head = new QHBoxLayout();
    head->setSpacing(12);
    cover_label_ = new QLabel(QStringLiteral("KAPAK\nYOK"));
    cover_label_->setAlignment(Qt::AlignCenter);
    cover_label_->setFixedSize(118, 170);
    cover_label_->setFont(hud_font(8, QFont::Weight::DemiBold, 1.4));
    cover_label_->setStyleSheet(
        QStringLiteral("background:%1;color:%2;border:1px solid %3;border-radius:7px;")
            .arg(QString::fromStdString(theme.input), QString::fromStdString(theme.faint),
                 QString::fromStdString(theme.border)));
    head->addWidget(cover_label_);

    auto* info = new QVBoxLayout();
    info->setSpacing(5);
    title_label_ = hud_label("KİTAP SEÇİLMEDİ", 16, theme.text, QFont::Weight::DemiBold, 0.5);
    title_label_->setWordWrap(true);
    authors_label_ = hud_label("Kitap servisi bu C++ sürümünde henüz yok.", 8, theme.muted,
                               QFont::Weight::Medium, 0.4);
    authors_label_->setWordWrap(true);
    meta_label_ = hud_label("", 7, theme.dim, QFont::Weight::Medium, 0.4);
    meta_label_->setWordWrap(true);
    source_label_ = hud_label("", 7, theme.faint, QFont::Weight::Medium, 0.4);
    source_label_->setWordWrap(true);
    info->addWidget(title_label_);
    info->addWidget(authors_label_);
    info->addWidget(meta_label_);
    info->addWidget(source_label_);
    info->addStretch(1);
    head->addLayout(info, 1);
    layout->addLayout(head);

    toc_label_ = hud_label("İÇİNDEKİLER · veride yok", 7, theme.dim, QFont::Weight::Medium, 0.4);
    toc_label_->setWordWrap(true);
    layout->addWidget(toc_label_);

    description_label_ = hud_label("", 7, theme.dim, QFont::Weight::Medium, 0.4);
    description_label_->setWordWrap(true);
    layout->addWidget(description_label_);
    layout->addWidget(new Divider());

    auto* summary_head = new QHBoxLayout();
    summary_head->addWidget(
        hud_label("Kişisel özet / notlar", 7, theme.muted, QFont::Weight::Medium, 0.3));
    summary_head->addStretch(1);
    summary_state_ = hud_label("KAYIT YOK", 7, theme.faint, QFont::Weight::Medium, 1.0);
    summary_head->addWidget(summary_state_);
    layout->addLayout(summary_head);

    summary_edit_ = new QPlainTextEdit();
    summary_edit_->setPlaceholderText(QString::fromUtf8(
        "Kitap profili veya taranan sayfalardan üretilen kişisel özet burada görünür."));
    summary_edit_->setStyleSheet(QString::fromStdString(summary_style()));
    summary_edit_->setMinimumHeight(220);
    layout->addWidget(summary_edit_, 1);

    auto* buttons = new QHBoxLayout();
    buttons->setSpacing(6);
    profile_button_ = new HudButton(QStringLiteral("KİTAP PROFİLİ"), false, true);
    pages_button_ = new HudButton(QStringLiteral("SAYFA ÖZETİ"), true, true);
    save_button_ = new HudButton(QStringLiteral("KAYDET"), false, true);
    copy_button_ = new HudButton(QStringLiteral("KOPYALA"), false, true);
    QObject::connect(profile_button_, &QPushButton::clicked, this, [this] {
        set_status("Özet üretimi AI arka ucuna bağlıydı; bu C++ sürümünde yok.", true);
    });
    QObject::connect(pages_button_, &QPushButton::clicked, this, [this] {
        set_status("Sayfa özeti AI arka ucuna bağlıydı; bu C++ sürümünde yok.", true);
    });
    QObject::connect(save_button_, &QPushButton::clicked, this, [this] {
        set_status("Özet deposu (BookStore) bu C++ sürümünde henüz port edilmedi.", true);
    });
    QObject::connect(copy_button_, &QPushButton::clicked, this, [this] {
        const QString text = summary_edit_->toPlainText().trimmed();
        if (text.isEmpty()) {
            set_status("Panoya kopyalanacak özet yok.", true);
            return;
        }
        QApplication::clipboard()->setText(text);
        set_status("Özet panoya kopyalandı.");
    });
    for (QPushButton* button : {profile_button_, pages_button_, save_button_, copy_button_}) {
        buttons->addWidget(button);
    }
    layout->addLayout(buttons);

    auto* library_row = new QHBoxLayout();
    library_row->addWidget(hud_label("Kitaplık", 7, theme.dim, QFont::Weight::Medium, 0.2));
    library_combo_ = new QComboBox();
    library_combo_->setStyleSheet(QString::fromStdString(combo_style()));
    library_combo_->addItem(QString::fromUtf8("Son özetler…"));
    library_row->addWidget(library_combo_, 1);
    layout->addLayout(library_row);

    for (QPushButton* button : {profile_button_, pages_button_, save_button_, copy_button_}) {
        button->setEnabled(false);
    }
    return holder;
}

void BookModal::populate_devices() {
    devices_ = QMediaDevices::videoInputs();
    device_combo_->clear();
    if (devices_.isEmpty()) {
        device_combo_->addItem(QStringLiteral("Kamera bulunamadı"));
        device_combo_->setEnabled(false);
        camera_toggle_->setEnabled(false);
        return;
    }
    for (int i = 0; i < devices_.size(); ++i) {
        device_combo_->addItem(QString::fromStdString(devices_.at(i).description().toStdString()),
                               i);
    }
    const QCameraDevice default_device = QMediaDevices::defaultVideoInput();
    for (int i = 0; i < devices_.size(); ++i) {
        if (devices_.at(i) == default_device) {
            device_combo_->setCurrentIndex(i);
            break;
        }
    }
}

void BookModal::start_camera(int index) {
    stop_camera();
    if (index < 0 || index >= devices_.size()) {
        set_status("Kamera bulunamadı.", true);
        return;
    }
    camera_ = std::make_unique<QCamera>(devices_.at(index));
    session_ = std::make_unique<QMediaCaptureSession>();
    session_->setCamera(camera_.get());
    session_->setVideoOutput(video_);
    camera_->start();
    active_index_ = index;
    camera_toggle_->setText(QStringLiteral("DURDUR"));
    set_status("CANLI · Kamera önizlemesi açık. Künye/OCR servisleri bu sürümde yok.");
}

void BookModal::stop_camera() {
    if (camera_) {
        camera_->stop();
    }
    session_.reset();
    camera_.reset();
    active_index_ = -1;
    if (camera_toggle_ != nullptr) {
        camera_toggle_->setText(QStringLiteral("BAŞLAT"));
    }
}

void BookModal::toggle_camera() {
    if (camera_ && camera_->isActive()) {
        stop_camera();
        set_status("Kamera durduruldu.");
    } else {
        start_camera(device_combo_->currentIndex());
    }
}

void BookModal::set_status(const std::string& text, bool error) {
    const Theme& theme = current_theme();
    status_label_->setStyleSheet(
        QStringLiteral("color:%1;background:transparent;border:none;")
            .arg(QString::fromStdString(error ? theme.red_bright : theme.text_soft)));
    status_label_->setText(QString::fromStdString(text));
}

void BookModal::closeEvent(QCloseEvent* event) {
    closing_ = true;
    stop_camera();
    HudModal::closeEvent(event);
}

}  // namespace jarvis::ui
