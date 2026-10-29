#include <jarvis/ui/home_modal.hpp>

#include <jarvis/ui/hud_widgets.hpp>
#include <jarvis/ui/theme.hpp>

#include <QComboBox>
#include <QFont>
#include <QHBoxLayout>
#include <QLabel>
#include <QPushButton>
#include <QSlider>
#include <QTimer>
#include <QVBoxLayout>
#include <QWidget>

namespace jarvis::ui {

namespace {

std::string upper_tr(std::string s) {
    for (char& c : s) {
        if (c >= 'a' && c <= 'z') c = char(c - 'a' + 'A');
    }
    auto rep = [&](const std::string& from, const std::string& to) {
        size_t pos = 0;
        while ((pos = s.find(from, pos)) != std::string::npos) {
            s.replace(pos, from.size(), to);
            pos += to.size();
        }
    };
    rep("ı", "I");
    rep("ş", "Ş");
    rep("ğ", "Ğ");
    rep("ü", "Ü");
    rep("ö", "Ö");
    rep("ç", "Ç");
    return s;
}

QComboBox* make_combo(const std::vector<std::pair<std::string, std::string>>& options,
                      int default_index = 0) {
    const Theme& theme = current_theme();
    auto* combo = new QComboBox();
    combo->setFont(hud_font(9, QFont::Weight::Medium, 0.4));
    combo->setStyleSheet(
        QStringLiteral(
            "QComboBox{background:%1;color:%2;border:1px solid %3;border-radius:5px;padding:5px 9px;}"
            "QComboBox:hover{border-color:%4;}"
            "QComboBox QAbstractItemView{background:%1;color:%2;border:1px solid %3;"
            "selection-background-color:%5;}")
            .arg(QString::fromStdString(theme.panel),
                 QString::fromStdString(theme.text_soft),
                 QString::fromStdString(theme.border),
                 QString::fromStdString(theme.red_soft),
                 QString::fromStdString(rgba_css(theme.red, 0.18))));
    for (const auto& [label, value] : options) {
        combo->addItem(QString::fromStdString(label), QString::fromStdString(value));
    }
    combo->setCurrentIndex(default_index);
    return combo;
}

HudButton* make_tone_button(const QString& text) {
    const Theme& theme = current_theme();
    auto* button = new HudButton(text, false, true);
    button->setCheckable(true);
    button->setStyleSheet(
        button->styleSheet() +
        QStringLiteral(
            "QPushButton:checked{background:%1;color:%2;border-color:%3;}")
            .arg(QString::fromStdString(rgba_css(theme.red, 0.18)),
                 QString::fromStdString(theme.red_bright),
                 QString::fromStdString(theme.red)));
    return button;
}

std::string tone_label_upper(const std::string& tone) {
    if (tone == "sicak") return "SICAK";
    if (tone == "soguk") return "SOĞUK";
    return "NÖTR";
}

}  // namespace

HomeModal::HomeModal(QWidget* parent, jarvis::home::SmartHome& home)
    : HudModal(parent, "Akıllı Ev",
               "SİMÜLASYON · CİHAZLAR BİLGİSAYARDA CANLANDIRILIR · AĞ KULLANILMAZ", 880, 660),
      home_(home) {
    const Theme& theme = current_theme();

    content_->addWidget(hud_label("Işıklar", 8, theme.muted, QFont::Weight::Medium, 0.3));
    auto* light_row = new QHBoxLayout();
    light_row->setSpacing(12);
    light_row->addWidget(build_light_card("salon"), 1);
    light_row->addWidget(build_light_card("calisma"), 1);
    content_->addLayout(light_row);

    auto* all_off = new HudButton(QStringLiteral("TÜM IŞIKLARI KAPAT"), false, true);
    connect(all_off, &QPushButton::clicked, this, [this] { all_lights_off(); });
    content_->addWidget(all_off, 0, Qt::AlignmentFlag::AlignRight);

    content_->addWidget(new Divider());
    content_->addWidget(
        hud_label("Kahve makineleri", 8, theme.muted, QFont::Weight::Medium, 0.3));
    auto* coffee_row = new QHBoxLayout();
    coffee_row->setSpacing(12);
    coffee_row->addWidget(build_turk_card(), 1);
    coffee_row->addWidget(build_filter_card(), 1);
    content_->addLayout(coffee_row);

    auto* note = hud_label(
        "Türk kahvesi ~75 sn, filtre kahve fincan başına ~40 sn demlenir. "
        "Süreler gerçek zamanlı ilerler; panel kapanınca da durum korunur.",
        7, theme.faint, QFont::Weight::Medium, 0.6);
    note->setWordWrap(true);
    content_->addWidget(note);

    timer_ = new QTimer(this);
    timer_->setInterval(1000);
    connect(timer_, &QTimer::timeout, this, [this] { refresh_now(); });
    on_closed = [this] {
        if (timer_ != nullptr) timer_->stop();
    };
    timer_->start();
    refresh_now();
}

QWidget* HomeModal::build_light_card(const std::string& key) {
    const Theme& theme = current_theme();
    QFrame* frame = hud_card(0, 0, 14);
    auto* body = static_cast<QVBoxLayout*>(frame->layout());
    const jarvis::home::Light& light = home_.light(key);
    body->addWidget(hud_label(light.name, 10, theme.text, QFont::Weight::DemiBold, 1.2, true));
    auto* state = hud_label("KAPALI", 8, theme.dim, QFont::Weight::Medium, 1.0);
    body->addWidget(state);
    body->addSpacing(4);
    auto* brightness = hud_label("%70", 7, theme.muted, QFont::Weight::Medium, 0.6);
    body->addWidget(brightness);

    auto* slider = new QSlider(Qt::Orientation::Horizontal);
    slider->setRange(0, 100);
    slider->setValue(light.brightness);
    slider->setStyleSheet(
        QStringLiteral(
            "QSlider::groove:horizontal{height:4px;background:%1;border:1px solid %2;border-radius:2px;}"
            "QSlider::sub-page:horizontal{background:%3;border-radius:2px;}"
            "QSlider::handle:horizontal{width:13px;margin:-6px 0;border-radius:6px;background:%4;border:1px solid %5;}")
            .arg(QString::fromStdString(theme.panel),
                 QString::fromStdString(theme.border),
                 QString::fromStdString(theme.red),
                 QString::fromStdString(theme.red_bright),
                 QString::fromStdString(theme.red_dim)));
    connect(slider, &QSlider::valueChanged, this, [this, key](int value) {
        on_brightness(key, value);
    });
    body->addWidget(slider);

    auto* buttons = new QHBoxLayout();
    buttons->setSpacing(5);
    auto* toggle = new HudButton(QStringLiteral("AÇ / KAPAT"), true, true);
    connect(toggle, &QPushButton::clicked, this, [this, key] { toggle_light(key); });
    buttons->addWidget(toggle);
    body->addLayout(buttons);

    auto* tones = new QHBoxLayout();
    tones->setSpacing(5);
    LightWidgets widgets;
    widgets.state = state;
    widgets.brightness = brightness;
    widgets.slider = slider;
    for (const auto& [tone, label] :
         {std::pair<std::string, std::string>{"sicak", "SICAK"},
          std::pair<std::string, std::string>{"notr", "NÖTR"},
          std::pair<std::string, std::string>{"soguk", "SOĞUK"}}) {
        auto* button = make_tone_button(QString::fromStdString(label));
        connect(button, &QPushButton::clicked, this, [this, key, tone] { set_tone(key, tone); });
        widgets.tones[tone] = button;
        tones->addWidget(button);
    }
    body->addLayout(tones);
    light_widgets_[key] = widgets;
    return frame;
}

QWidget* HomeModal::build_turk_card() {
    const Theme& theme = current_theme();
    QFrame* frame = hud_card(0, 0, 14);
    auto* body = static_cast<QVBoxLayout*>(frame->layout());
    body->addWidget(
        hud_label("Türk kahvesi", 10, theme.text, QFont::Weight::DemiBold, 1.2, true));
    auto* state = hud_label("MAKİNE BOŞ", 8, theme.muted, QFont::Weight::Medium, 0.8);
    body->addWidget(state);
    body->addSpacing(4);
    body->addWidget(hud_label("Köpük", 7, theme.dim, QFont::Weight::Medium, 1.0));
    auto* foam = make_combo({{"Az köpüklü", "az"}, {"Orta köpüklü", "orta"}, {"Bol köpüklü", "bol"}}, 1);
    body->addWidget(foam);
    body->addWidget(hud_label("Şeker", 7, theme.dim, QFont::Weight::Medium, 1.0));
    auto* sugar = make_combo({{"Sade", "sade"},
                               {"Az şekerli", "az"},
                               {"Orta şekerli", "orta"},
                               {"Çok şekerli", "cok"}},
                             2);
    body->addWidget(sugar);
    auto* brew = new HudButton(QStringLiteral("PİŞİR"), true, true);
    connect(brew, &QPushButton::clicked, this, [this] { HomeModal::brew("turk"); });
    body->addWidget(brew, 0, Qt::AlignmentFlag::AlignLeft);
    CoffeeWidgets widgets;
    widgets.state = state;
    widgets.brew = brew;
    widgets.foam = foam;
    widgets.sugar = sugar;
    coffee_widgets_["turk"] = widgets;
    return frame;
}

QWidget* HomeModal::build_filter_card() {
    const Theme& theme = current_theme();
    QFrame* frame = hud_card(0, 0, 14);
    auto* body = static_cast<QVBoxLayout*>(frame->layout());
    body->addWidget(
        hud_label("Filtre kahve", 10, theme.text, QFont::Weight::DemiBold, 1.2, true));
    auto* state = hud_label("MAKİNE BOŞ", 8, theme.muted, QFont::Weight::Medium, 1.0);
    state->setWordWrap(true);
    body->addWidget(state);
    body->addSpacing(4);
    body->addWidget(hud_label("Fincan", 7, theme.dim, QFont::Weight::Medium, 1.0));
    auto* cups = make_combo({{"1 fincan", "1"}, {"2 fincan", "2"}, {"3 fincan", "3"}, {"4 fincan", "4"}}, 1);
    body->addWidget(cups);
    body->addWidget(
        hud_label("Hazır olunca sıcak tutulur.", 7, theme.faint, QFont::Weight::Medium, 0.6));
    auto* brew = new HudButton(QStringLiteral("DEMLE"), true, true);
    connect(brew, &QPushButton::clicked, this, [this] { HomeModal::brew("filtre"); });
    body->addWidget(brew, 0, Qt::AlignmentFlag::AlignLeft);
    CoffeeWidgets widgets;
    widgets.state = state;
    widgets.brew = brew;
    widgets.cups = cups;
    coffee_widgets_["filtre"] = widgets;
    return frame;
}

void HomeModal::on_brightness(const std::string& key, int value) {
    if (refreshing_) return;
    home_.set_light(key, value > 0, value);
    refresh_now();
}

void HomeModal::toggle_light(const std::string& key) {
    const jarvis::home::Light& light = home_.light(key);
    home_.set_light(key, !light.on);
    refresh_now();
}

void HomeModal::set_tone(const std::string& key, const std::string& tone) {
    home_.set_light(key, std::nullopt, std::nullopt, tone);
    refresh_now();
}

void HomeModal::all_lights_off() {
    home_.set_all_lights(false);
    refresh_now();
}

void HomeModal::brew(const std::string& kind) {
    auto it = coffee_widgets_.find(kind);
    if (it == coffee_widgets_.end()) return;
    const CoffeeWidgets& entry = it->second;
    if (kind == "turk") {
        std::string foam = entry.foam->currentData().toString().toStdString();
        std::string sugar = entry.sugar->currentData().toString().toStdString();
        home_.brew(kind, std::nullopt, foam, sugar);
    } else {
        int cups = entry.cups->currentData().toString().toInt();
        home_.brew(kind, cups);
    }
    refresh_now();
}

void HomeModal::refresh_now() {
    refreshing_ = true;
    try {
        for (auto& [key, widgets] : light_widgets_) {
            const jarvis::home::Light& light = home_.light(key);
            widgets.state->setText(light.on ? QString::fromStdString(
                                                  "AÇIK · %" + std::to_string(light.brightness) +
                                                  " · " + tone_label_upper(light.tone))
                                            : QStringLiteral("KAPALI"));
            widgets.brightness->setText(
                QString::fromStdString("%" + std::to_string(light.brightness)));
            widgets.slider->blockSignals(true);
            widgets.slider->setValue(light.brightness);
            widgets.slider->blockSignals(false);
            for (auto& [tone, button] : widgets.tones) {
                button->blockSignals(true);
                button->setChecked(light.tone == tone);
                button->blockSignals(false);
            }
        }
        for (auto& [kind, widgets] : coffee_widgets_) {
            jarvis::home::Coffee coffee = home_.coffee(kind);
            widgets.state->setText(QString::fromStdString(upper_tr(coffee.describe(home_.now()))));
            widgets.brew->setEnabled(!coffee.started_at || coffee.ready);
        }
    } catch (...) {
    }
    refreshing_ = false;
}

void HomeModal::refresh() {
    refresh_now();
}

}  // namespace jarvis::ui
