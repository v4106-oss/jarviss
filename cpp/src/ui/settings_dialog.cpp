#include <jarvis/ui/settings_dialog.hpp>

#include <QComboBox>
#include <QEnterEvent>
#include <QFormLayout>
#include <QGridLayout>
#include <QHBoxLayout>
#include <QLabel>
#include <QLineEdit>
#include <QMouseEvent>
#include <QPainter>
#include <QPaintEvent>
#include <QPen>
#include <QPushButton>
#include <QTabWidget>
#include <QVBoxLayout>

#include <jarvis/core/settings.hpp>
#include <jarvis/ui/theme.hpp>

namespace jarvis::ui {

namespace {

QColor blend_colors(const QColor& base, const QColor& target, double amount) {
    const double t = std::clamp(amount, 0.0, 1.0);
    return QColor(static_cast<int>(base.red() + (target.red() - base.red()) * t),
                  static_cast<int>(base.green() + (target.green() - base.green()) * t),
                  static_cast<int>(base.blue() + (target.blue() - base.blue()) * t),
                  static_cast<int>(base.alpha() + (target.alpha() - base.alpha()) * t));
}

QString hex(const std::string& value) {
    return QString::fromStdString(value);
}

}  // namespace

ThemePreview::ThemePreview(const Theme& theme, bool selected, QWidget* parent)
    : QFrame(parent), theme_(&theme), selected_(selected) {
    setFixedSize(176, 142);
    setCursor(Qt::CursorShape::PointingHandCursor);
    setAccessibleName(QString::fromStdString(theme.label));
    setToolTip(QString::fromStdString(theme.label) + QStringLiteral(" temasını seç"));
}

void ThemePreview::set_selected(bool selected) {
    selected_ = selected;
    update();
}

void ThemePreview::enterEvent(QEnterEvent* event) {
    hover_ = true;
    update();
    QFrame::enterEvent(event);
}

void ThemePreview::leaveEvent(QEvent* event) {
    hover_ = false;
    update();
    QFrame::leaveEvent(event);
}

void ThemePreview::mouseReleaseEvent(QMouseEvent* event) {
    if (event->button() == Qt::MouseButton::LeftButton && onClick) {
        onClick();
    }
    QFrame::mouseReleaseEvent(event);
}

void ThemePreview::paintEvent(QPaintEvent* /*event*/) {
    QPainter painter(this);
    painter.setRenderHint(QPainter::RenderHint::Antialiasing);
    const Theme& pal = *theme_;
    const double hover = hover_ ? 1.0 : 0.0;
    const double selected = selected_ ? 1.0 : 0.0;
    const double lift = 2.0 * hover * (1.0 - 0.4 * selected);
    QRectF outer = QRectF(rect()).adjusted(1.5, 1.5 - lift, -1.5, -1.5 - lift);

    if (selected > 0.01) {
        QColor glow = parse_color(pal.red);
        glow.setAlpha(38);
        painter.setPen(QPen(glow, 4.0));
        painter.setBrush(Qt::BrushStyle::NoBrush);
        painter.drawRoundedRect(outer.adjusted(-1, -1, 1, 1), 13, 13);
    }

    painter.setPen(Qt::PenStyle::NoPen);
    painter.setBrush(parse_color(pal.background));
    painter.drawRoundedRect(outer, 12, 12);

    QRectF header(outer.left() + 10, outer.top() + 10, outer.width() - 20, 24);
    painter.setBrush(parse_color(pal.panel_2));
    painter.drawRoundedRect(header, 6, 6);

    painter.setBrush(parse_color(pal.red));
    painter.drawEllipse(QPointF(header.left() + 12, header.center().y()), 4, 4);
    QFont title_font(QString::fromStdString(pal.font), 8, QFont::Weight::DemiBold);
    title_font.setLetterSpacing(QFont::SpacingType::AbsoluteSpacing, 1.6);
    painter.setFont(title_font);
    painter.setPen(parse_color(pal.text));
    painter.drawText(QRectF(header.left() + 22, header.top(), 64, header.height()),
                     Qt::AlignmentFlag::AlignVCenter | Qt::AlignmentFlag::AlignLeft,
                     QStringLiteral("JARVIS"));
    painter.setFont(QFont(QString::fromStdString(pal.font), 6, QFont::Weight::Medium));
    painter.setPen(parse_color(pal.red_bright));
    painter.drawText(QRectF(header.right() - 68, header.top(), 58, header.height()),
                     Qt::AlignmentFlag::AlignVCenter | Qt::AlignmentFlag::AlignRight,
                     QStringLiteral("PROTOKOL"));

    QRectF rail(outer.left() + 10, outer.top() + 42, 46, 74);
    QRectF body(outer.left() + 62, outer.top() + 42, outer.width() - 72, 74);
    painter.setBrush(parse_color(pal.panel));
    painter.drawRoundedRect(rail, 6, 6);
    painter.setBrush(parse_color(pal.panel));
    painter.drawRoundedRect(body, 6, 6);

    painter.setBrush(parse_color(pal.red));
    painter.drawEllipse(QPointF(rail.center().x(), rail.top() + 18), 6, 6);
    painter.setBrush(parse_color(pal.faint));
    for (int index = 0; index < 3; ++index) {
        painter.drawRoundedRect(
            QRectF(rail.left() + 10, rail.top() + 34 + index * 12, 26, 4), 2, 2);
    }

    painter.setBrush(parse_color(pal.panel_2));
    painter.drawRoundedRect(QRectF(body.left() + 8, body.top() + 10, body.width() - 16, 22), 4, 4);
    painter.setBrush(parse_color(pal.red_soft));
    painter.drawRoundedRect(
        QRectF(body.left() + 8, body.top() + 40, body.width() * 0.52, 6), 3, 3);
    painter.setBrush(parse_color(pal.faint));
    painter.drawRoundedRect(QRectF(body.left() + 8, body.top() + 52, body.width() - 16, 4), 2, 2);
    painter.drawRoundedRect(
        QRectF(body.left() + 8, body.top() + 62, body.width() * 0.72, 4), 2, 2);

    QFont label_font(QString::fromStdString(pal.font), 8, QFont::Weight::DemiBold);
    label_font.setLetterSpacing(QFont::SpacingType::AbsoluteSpacing, 0.8);
    painter.setFont(label_font);
    painter.setPen(blend_colors(parse_color(pal.text_soft), parse_color(pal.text), selected));
    painter.drawText(QRectF(outer.left() + 12, outer.bottom() - 26, outer.width() - 24, 18),
                     Qt::AlignmentFlag::AlignVCenter | Qt::AlignmentFlag::AlignLeft,
                     QString::fromStdString(pal.label));

    if (selected > 0.01) {
        QRectF check_rect(outer.right() - 28, outer.bottom() - 27, 16, 16);
        QColor check_fill = parse_color(pal.red);
        check_fill.setAlpha(255);
        painter.setBrush(check_fill);
        painter.setPen(Qt::PenStyle::NoPen);
        painter.drawEllipse(check_rect.center(), 8.0, 8.0);
        QPen check_pen(parse_color(pal.background), 1.8);
        check_pen.setCapStyle(Qt::PenCapStyle::RoundCap);
        painter.setPen(check_pen);
        painter.drawLine(QPointF(check_rect.left() + 4, check_rect.center().y()),
                         QPointF(check_rect.left() + 7, check_rect.bottom() - 5));
        painter.drawLine(QPointF(check_rect.left() + 7, check_rect.bottom() - 5),
                         QPointF(check_rect.right() - 4, check_rect.top() + 5));
    }

    QColor border = blend_colors(parse_color(pal.border), parse_color(pal.red_soft), hover);
    border = blend_colors(border, parse_color(pal.red), selected);
    painter.setBrush(Qt::BrushStyle::NoBrush);
    painter.setPen(QPen(border, 1.0 + 1.0 * selected));
    painter.drawRoundedRect(outer, 12, 12);
}

SettingsDialog::SettingsDialog(QWidget* parent, jarvis::core::Settings& settings)
    : HudModal(parent, "Ayarlar", "Tema ve yapay zekâ bağlantısını düzenle", 760, 540, 24, true,
               false, false),
      settings_(&settings),
      selected_theme_(settings.theme()) {
    auto* tabs = new QTabWidget();
    tabs->setStyleSheet(
        QStringLiteral(
            "QTabWidget::pane{border:1px solid %1;border-radius:10px;top:-1px;}"
            "QTabBar::tab{background:transparent;color:%2;padding:8px 18px;border:none;"
            "border-bottom:2px solid transparent;font-weight:600;}"
            "QTabBar::tab:selected{color:%3;border-bottom:2px solid %3;}")
            .arg(hex(current_theme().border), hex(current_theme().muted),
                 hex(current_theme().red)));
    content_->addWidget(tabs, 1);

    build_themes_tab(tabs);
    build_connection_tab(tabs, settings);

    auto* actions = new QHBoxLayout();
    actions->addStretch(1);
    auto* save_button = new QPushButton(QStringLiteral("Kaydet"));
    save_button->setCursor(Qt::CursorShape::PointingHandCursor);
    save_button->setMinimumWidth(120);
    save_button->setStyleSheet(
        QStringLiteral(
            "QPushButton{background:%1;color:%2;border:1px solid %3;border-radius:10px;"
            "padding:9px 20px;font-weight:600;}"
            "QPushButton:hover{background:%4;border-color:%1;}")
            .arg(hex(current_theme().red), hex(current_theme().background),
                 hex(current_theme().red_soft), hex(current_theme().red_bright)));
    connect(save_button, &QPushButton::clicked, this, [this] {
        save_connection(*settings_);
        close_modal();
    });
    actions->addWidget(save_button);
    content_->addLayout(actions);
}

void SettingsDialog::build_themes_tab(QTabWidget* tabs) {
    auto* page = new QWidget();
    auto* layout = new QVBoxLayout(page);
    layout->setContentsMargins(6, 10, 6, 6);
    layout->setSpacing(12);

    auto* note = new QLabel(
        QStringLiteral("Tema seçimi anında uygulanır ve kaydedilir."));
    note->setStyleSheet(
        QStringLiteral("color:%1;font-size:12px;").arg(hex(current_theme().muted)));
    layout->addWidget(note);

    auto* grid = new QGridLayout();
    grid->setSpacing(12);
    const std::vector<std::string> order = theme_order();
    int column = 0;
    int row = 0;
    for (const auto& key : order) {
        const Theme& theme = theme_palette(key);
        auto* card = new ThemePreview(theme, key == selected_theme_);
        card->onClick = [this, key] { apply_theme(key, *settings_); };
        theme_cards_.push_back(card);
        grid->addWidget(card, row, column);
        if (++column >= 4) {
            column = 0;
            ++row;
        }
    }
    layout->addLayout(grid);
    layout->addStretch(1);
    tabs->addTab(page, QStringLiteral("Temalar"));
}

void SettingsDialog::build_connection_tab(QTabWidget* tabs, jarvis::core::Settings& settings) {
    auto* page = new QWidget();
    auto* form = new QFormLayout(page);
    form->setContentsMargins(6, 10, 6, 6);
    form->setSpacing(10);

    provider_ = new QComboBox();
    provider_->addItem(QStringLiteral("Google Gemini API"), QStringLiteral("gemini"));
    provider_->addItem(QStringLiteral("Ollama (yerel, ücretsiz)"), QStringLiteral("ollama"));
    provider_->addItem(QStringLiteral("Token Harbor (ücretsiz API)"),
                       QStringLiteral("tokenharbor"));
    const int provider_index = provider_->findData(QString::fromStdString(settings.provider()));
    provider_->setCurrentIndex(std::max(0, provider_index));
    form->addRow(QStringLiteral("Yapay zekâ motoru:"), provider_);

    api_key_ = new QLineEdit(QString::fromStdString(settings.api_key()));
    api_key_->setEchoMode(QLineEdit::EchoMode::Password);
    api_key_->setPlaceholderText(QStringLiteral("Google AI Studio API anahtarın"));
    model_ = new QLineEdit(QString::fromStdString(settings.model()));
    model_->setPlaceholderText(QStringLiteral("Örn. gemini-3.8-flash"));
    form->addRow(QStringLiteral("Gemini API anahtarı:"), api_key_);
    form->addRow(QStringLiteral("Gemini modeli:"), model_);

    ollama_url_ = new QLineEdit(QString::fromStdString(settings.ollama_url()));
    ollama_url_->setPlaceholderText(QStringLiteral("http://localhost:11434"));
    ollama_model_ = new QLineEdit(QString::fromStdString(settings.ollama_model()));
    ollama_model_->setPlaceholderText(QStringLiteral("Örn. qwen2.5-coder:3b"));
    form->addRow(QStringLiteral("Ollama adresi:"), ollama_url_);
    form->addRow(QStringLiteral("Ollama modeli:"), ollama_model_);

    tokenharbor_key_ = new QLineEdit(QString::fromStdString(settings.tokenharbor_api_key()));
    tokenharbor_key_->setEchoMode(QLineEdit::EchoMode::Password);
    tokenharbor_key_->setPlaceholderText(
        QStringLiteral("tokenharbor.ai panosundaki anahtar"));
    tokenharbor_model_ = new QLineEdit(QString::fromStdString(settings.tokenharbor_model()));
    tokenharbor_model_->setPlaceholderText(QStringLiteral("Boş bırak = ücretsiz modeli kendi seçer"));
    form->addRow(QStringLiteral("Token Harbor API anahtarı:"), tokenharbor_key_);
    form->addRow(QStringLiteral("Token Harbor modeli:"), tokenharbor_model_);

    for (auto* field : {api_key_, model_, ollama_url_, ollama_model_, tokenharbor_key_,
                        tokenharbor_model_}) {
        field->setStyleSheet(
            QStringLiteral("QLineEdit{background:%1;color:%2;border:1px solid %3;border-radius:8px;"
                           "padding:7px 10px;}")
                .arg(hex(current_theme().input), hex(current_theme().text),
                     hex(current_theme().border)));
    }
    provider_->setStyleSheet(
        QStringLiteral("QComboBox{background:%1;color:%2;border:1px solid %3;border-radius:8px;"
                       "padding:6px 10px;}")
            .arg(hex(current_theme().input), hex(current_theme().text),
                 hex(current_theme().border)));

    tabs->addTab(page, QStringLiteral("Bağlantı"));
}

void SettingsDialog::apply_theme(const std::string& key, jarvis::core::Settings& settings) {
    selected_theme_ = key;
    theme_changed_ = true;
    set_current_theme(key);
    settings.set_theme(key);
    for (auto* card : theme_cards_) {
        card->set_selected(card->key() == key);
    }
    if (on_theme_changed) {
        on_theme_changed();
    }
}

void SettingsDialog::save_connection(jarvis::core::Settings& settings) {
    const QString provider = provider_->currentData().toString();
    settings.save(api_key_->text().toStdString(), model_->text().toStdString(),
                  provider.toStdString(), ollama_url_->text().toStdString(),
                  ollama_model_->text().toStdString(),
                  tokenharbor_key_->text().toStdString(),
                  tokenharbor_model_->text().toStdString(), settings.tokenharbor_url());
}

}  // namespace jarvis::ui
