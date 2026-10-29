#include <jarvis/ui/hud_chat.hpp>

#include <QApplication>
#include <QClipboard>
#include <QDateTime>
#include <QEnterEvent>
#include <QFontMetrics>
#include <QHBoxLayout>
#include <QLabel>
#include <QLinearGradient>
#include <QPainter>
#include <QPushButton>
#include <QScrollArea>
#include <QScrollBar>
#include <QTimer>
#include <QVBoxLayout>

#include <regex>
#include <vector>

#include <jarvis/ui/hud_widgets.hpp>
#include <jarvis/ui/shell_parts.hpp>
#include <jarvis/ui/theme.hpp>

namespace jarvis::ui {

namespace {

constexpr int kStreamFlushMs = 45;
constexpr std::size_t kRichTextLimit = 24000;

std::string html_escape(const std::string& text) {
    std::string out;
    out.reserve(text.size());
    for (const char c : text) {
        switch (c) {
            case '&': out += "&amp;"; break;
            case '<': out += "&lt;"; break;
            case '>': out += "&gt;"; break;
            case '"': out += "&quot;"; break;
            case '\'': out += "&#39;"; break;
            default: out += c;
        }
    }
    return out;
}

// std::regex_replace benzeri; dönüşüm fonksiyonu eşleşmeleri alır.
std::string regex_replace_cb(const std::string& input, const std::regex& re,
                             const std::function<std::string(const std::smatch&)>& fn) {
    std::string out;
    out.reserve(input.size());
    auto begin = std::sregex_iterator(input.begin(), input.end(), re);
    auto end = std::sregex_iterator();
    std::size_t last = 0;
    for (auto it = begin; it != end; ++it) {
        const std::smatch& m = *it;
        out.append(input, last, std::size_t(m.position()) - last);
        out += fn(m);
        last = std::size_t(m.position()) + std::size_t(m.length());
    }
    out.append(input, last, input.size() - last);
    return out;
}

// Python _Avatar: kullanıcı için kırmızı, Jarvis için altın tonlu daire.
class Avatar : public QWidget {
public:
    Avatar(bool is_user, int size = 30, QWidget* parent = nullptr)
        : QWidget(parent), is_user_(is_user), size_(size) {
        setFixedSize(size, size);
        setAttribute(Qt::WA_TransparentForMouseEvents, true);
    }

protected:
    void paintEvent(QPaintEvent* event) override {
        Q_UNUSED(event);
        QPainter painter(this);
        painter.setRenderHint(QPainter::Antialiasing, true);
        const QRectF rect = QRectF(this->rect()).adjusted(0.6, 0.6, -0.6, -0.6);
        const Theme& theme = current_theme();
        QColor accent = parse_color(is_user_ ? theme.red : theme.gold);
        QColor deep = parse_color(theme.panel_2);
        QLinearGradient gradient(rect.topLeft(), rect.bottomRight());
        gradient.setColorAt(0.0, QColor(accent.red(), accent.green(), accent.blue(), 62));
        gradient.setColorAt(1.0, deep);
        painter.setBrush(gradient);
        painter.setPen(QColor(accent.red(), accent.green(), accent.blue(), 90));
        painter.drawEllipse(rect);
        const double pad = size_ * 0.28;
        draw_glyph(painter, is_user_ ? "user" : "spark",
                   QRectF(pad, pad, size_ - pad * 2, size_ - pad * 2),
                   QColor(is_user_ ? theme.red_bright.c_str() : theme.gold.c_str()), 1.5);
    }

private:
    bool is_user_;
    int size_;
};

// Gün ayracı; iki yatay çizgi arasında etiket.
class DayDivider : public QWidget {
public:
    explicit DayDivider(const std::string& text, QWidget* parent = nullptr) : QWidget(parent) {
        auto* layout = new QHBoxLayout(this);
        layout->setContentsMargins(30, 4, 30, 4);
        layout->setSpacing(10);
        const Theme& theme = current_theme();
        const QString line_style = QStringLiteral("background:%1;border:none;")
                                       .arg(QString::fromStdString(rgba_css(theme.text, 0.05)));
        for (int i = 0; i < 2; ++i) {
            auto* line = new QFrame();
            line->setFixedHeight(1);
            line->setStyleSheet(line_style);
            layout->addWidget(line, 1);
            if (i == 0) {
                layout->addWidget(hud_label(text, 6, theme.faint, QFont::Weight::Medium, 0.3));
            }
        }
    }
};

// Boş durum: öneri çipleri.
class ChatEmptyState : public QWidget {
public:
    std::function<void(const QString&)> on_suggested;

    explicit ChatEmptyState(QWidget* parent = nullptr) : QWidget(parent) {
        const Theme& theme = current_theme();
        auto* outer = new QHBoxLayout(this);
        outer->setContentsMargins(0, 8, 0, 8);
        outer->addStretch(1);
        auto* panel = new QWidget();
        panel->setMaximumWidth(560);
        auto* layout = new QVBoxLayout(panel);
        layout->setContentsMargins(20, 18, 20, 18);
        layout->setSpacing(9);

        auto* mark = new QHBoxLayout();
        mark->addStretch(1);
        mark->addWidget(new GlyphIcon("spark", 26, theme.red_soft));
        mark->addStretch(1);
        layout->addLayout(mark);

        auto* title = hud_label("Komuta hazır", 10, theme.text_soft, QFont::Weight::Medium, 0.3);
        title->setAlignment(Qt::AlignHCenter);
        layout->addWidget(title);

        auto* hint = hud_label(
            "Yaz, mikrofonla konuş ya da aşağıdaki önerilerden başla.\n"
            "Jarvis ekranı, kamerayı ve sistemi tek akışta kullanabilir.",
            8, theme.dim, QFont::Weight::Normal, 0.2);
        hint->setAlignment(Qt::AlignHCenter);
        hint->setWordWrap(true);
        layout->addWidget(hint);

        auto* chips = new QHBoxLayout();
        chips->setSpacing(6);
        chips->addStretch(1);
        const std::vector<std::string> suggestions = {
            "Bugün neler var?", "Kamerayı aç ve beni tara", "Ekranımda ne var?", "Haritayı aç"};
        for (const auto& text : suggestions) {
            auto* chip = new QPushButton(QString::fromStdString(text));
            chip->setCursor(Qt::PointingHandCursor);
            chip->setFont(hud_font(7, QFont::Weight::Medium, 0.2));
            chip->setStyleSheet(
                QStringLiteral("QPushButton{background:%1;color:%2;border:1px solid %3;"
                               "border-radius:13px;padding:5px 12px;}")
                    .arg(QString::fromStdString(rgba_css(theme.text, 0.028)),
                         QString::fromStdString(theme.text_soft),
                         QString::fromStdString(theme.border)) +
                QStringLiteral("QPushButton:hover{color:%1;border-color:%2;background:%3;}")
                    .arg(QString::fromStdString(theme.red_bright),
                         QString::fromStdString(rgba_css(theme.red, 0.35)),
                         QString::fromStdString(rgba_css(theme.red, 0.07))));
            QObject::connect(chip, &QPushButton::clicked, this,
                             [this, text] { if (on_suggested) on_suggested(QString::fromStdString(text)); });
            chips->addWidget(chip);
        }
        chips->addStretch(1);
        layout->addLayout(chips);
        outer->addWidget(panel);
        outer->addStretch(1);
    }
};

}  // namespace

std::string render_markdown(const std::string& input) {
    const std::string raw = input.empty() ? "" : input;
    if (raw.find_first_not_of(" \t\r\n") == std::string::npos) {
        return "";
    }
    if (raw.size() > kRichTextLimit) {
        std::string escaped = html_escape(raw);
        std::string out;
        for (char c : escaped) {
            if (c == '\n') {
                out += "<br>";
            } else {
                out += c;
            }
        }
        return out;
    }

    const Theme& theme = current_theme();
    const std::string dim = theme.dim;
    const std::string mono = theme.mono;
    const std::string text = theme.text;
    const std::string text_soft = theme.text_soft;
    const std::string red_bright = theme.red_bright;
    const std::string red_soft = theme.red_soft;

    std::vector<std::string> blocks;
    std::vector<std::string> inlines;

    std::regex code_block_re(R"(```[ \t]*([A-Za-z0-9+#.\-]*)[ \t]*\n([\s\S]*?)```)");
    std::string working = regex_replace_cb(
        raw, code_block_re, [&](const std::smatch& m) -> std::string {
            const std::string language = m[1].str();
            const std::string code = html_escape(m[2].str());
            std::string label;
            if (!language.empty()) {
                std::string upper = language;
                for (auto& c : upper) c = char(std::toupper(static_cast<unsigned char>(c)));
                label = "<div style=\"color:" + dim + ";font-family:" + mono +
                        ";font-size:8px;letter-spacing:1px;margin-bottom:2px;\">" +
                        html_escape(upper) + "</div>";
            }
            blocks.push_back(
                "<div style=\"background-color:" + rgba_css(text, 0.055) +
                ";border:1px solid " + rgba_css(text, 0.08) +
                ";border-radius:8px;padding:7px 9px;margin:3px 0;\">" + label +
                "<span style=\"font-family:" + mono + ";font-size:9px;color:" + text_soft +
                ";\">" + code + "</span></div>");
            return "\x00B" + std::to_string(blocks.size() - 1) + "\x00";
        });

    working = html_escape(working);

    std::regex inline_code_re(R"(`([^`\n]+)`)");
    working = regex_replace_cb(working, inline_code_re, [&](const std::smatch& m) -> std::string {
        inlines.push_back(
            "<span style=\"background-color:" + rgba_css(text, 0.07) + ";font-family:" + mono +
            ";color:" + text + ";padding:0 3px;border-radius:3px;\">" + m[1].str() + "</span>");
        return "\x00I" + std::to_string(inlines.size() - 1) + "\x00";
    });

    std::regex link_re(R"(\[([^\]\n]+)\]\((https?://[^\s)]+)\))");
    working = regex_replace_cb(working, link_re, [&](const std::smatch& m) -> std::string {
        return "<a href=\"" + m[2].str() + "\" style=\"color:" + red_bright +
               ";text-decoration:none;\">" + m[1].str() + "</a>";
    });

    std::regex bold_re(R"(\*\*(.+?)\*\*)");
    working = std::regex_replace(working, bold_re, "<b>$1</b>");
    std::regex italic_re(R"(\*([^*\n]+)\*)");
    working = std::regex_replace(working, italic_re, "<i>$1</i>");

    std::vector<std::string> lines;
    std::string line;
    for (char c : working) {
        if (c == '\n') {
            lines.push_back(line);
            line.clear();
        } else {
            line += c;
        }
    }
    lines.push_back(line);

    std::vector<std::string> rendered_lines;
    for (const auto& ln : lines) {
        const std::string stripped = [&] {
            std::size_t a = ln.find_first_not_of(" \t");
            std::size_t b = ln.find_last_not_of(" \t");
            return (a == std::string::npos) ? std::string() : ln.substr(a, b - a + 1);
        }();
        if (stripped.empty()) {
            rendered_lines.push_back("");
            continue;
        }
        if (stripped[0] == '#') {
            std::size_t pos = stripped.find_first_not_of('#');
            std::string heading = (pos == std::string::npos) ? std::string() : stripped.substr(pos);
            std::size_t hpos = heading.find_first_not_of(" \t");
            if (hpos != std::string::npos) heading = heading.substr(hpos);
            rendered_lines.push_back(
                "<div style=\"color:" + text +
                ";font-weight:600;font-size:10px;letter-spacing:0.3px;margin:2px 0;\">" +
                heading + "</div>");
            continue;
        }
        if (stripped.size() >= 2 && stripped[1] == ' ' &&
            (stripped[0] == '-' || stripped[0] == '*' || (stripped[0] == '\xE2' && stripped[1] == '\x80'))) {
            rendered_lines.push_back(
                "<div style=\"margin-left:4px;\"><span style=\"color:" + red_soft +
                ";\">•</span> " + stripped.substr(2) + "</div>");
            continue;
        }
        rendered_lines.push_back(ln);
    }

    std::string joined;
    for (std::size_t i = 0; i < rendered_lines.size(); ++i) {
        if (i > 0) joined += "<br>";
        joined += rendered_lines[i];
    }
    working = joined;

    for (std::size_t i = 0; i < blocks.size(); ++i) {
        const std::string needle = "\x00B" + std::to_string(i) + "\x00";
        std::size_t pos = 0;
        while ((pos = working.find(needle, pos)) != std::string::npos) {
            working.replace(pos, needle.size(), blocks[i]);
            pos += blocks[i].size();
        }
    }
    for (std::size_t i = 0; i < inlines.size(); ++i) {
        const std::string needle = "\x00I" + std::to_string(i) + "\x00";
        std::size_t pos = 0;
        while ((pos = working.find(needle, pos)) != std::string::npos) {
            working.replace(pos, needle.size(), inlines[i]);
            pos += inlines[i].size();
        }
    }
    return working;
}

// ---------------------------------------------------------------------------
// ChatEntry
// ---------------------------------------------------------------------------
ChatEntry::ChatEntry(const std::string& text, bool is_user, QWidget* parent)
    : QWidget(parent), is_user_(is_user), raw_(text) {
    const Theme& theme = current_theme();
    auto* outer = new QHBoxLayout(this);
    outer->setContentsMargins(0, 2, 0, 2);
    outer->setSpacing(8);

    avatar_ = new Avatar(is_user);
    bubble_ = new QFrame();
    bubble_->setObjectName("chatBubble");
    bubble_->setSizePolicy(QSizePolicy::Maximum, QSizePolicy::Preferred);
    const QString accent = QString::fromStdString(is_user ? theme.red_bright : theme.gold);
    bubble_->setStyleSheet(
        QStringLiteral("QFrame#chatBubble{background:%1;border:1px solid %2;border-radius:14px;}")
            .arg(QString::fromStdString(is_user ? rgba_css(theme.red, theme.is_light ? 0.05 : 0.07)
                                                : rgba_css(theme.text, 0.028)),
                 QString::fromStdString(is_user ? rgba_css(theme.red, 0.14)
                                                : rgba_css(theme.text, 0.06))));
    auto* bubble_layout = new QVBoxLayout(bubble_);
    bubble_layout->setContentsMargins(12, 8, 12, 9);
    bubble_layout->setSpacing(5);

    auto* top = new QHBoxLayout();
    top->setContentsMargins(0, 0, 0, 0);
    top->setSpacing(7);
    sender_ = hud_label(is_user ? "Sen" : "Jarvis", 7,
                        is_user ? theme.red_bright : theme.gold, QFont::Weight::Medium, 0.2);
    auto* dot = new QLabel(QString::fromUtf8("●"));
    dot->setFont(hud_font(6, QFont::Weight::DemiBold, 0.0));
    dot->setStyleSheet(QStringLiteral("color:%1;background:transparent;border:none;").arg(accent));
    auto* time_label = hud_label(QDateTime::currentDateTime().toString("HH:mm").toStdString(), 7,
                                 theme.faint, QFont::Weight::Medium, 0.6);
    copy_button_ = new GlyphButton("copy", "Mesajı kopyala", 22);
    copy_button_->onClick = [this] { copy_to_clipboard(); };
    copy_button_->hide();
    top->addWidget(dot);
    top->addWidget(sender_);
    top->addWidget(time_label);
    top->addStretch(1);
    top->addWidget(copy_button_);
    bubble_layout->addLayout(top);

    label_ = new QLabel(QString());
    label_->setFont(hud_font(10, QFont::Weight::Normal, 0.15));
    label_->setStyleSheet(QStringLiteral("color:%1;background:transparent;border:none;")
                              .arg(QString::fromStdString(theme.text_soft)));
    label_->setWordWrap(true);
    label_->setTextFormat(Qt::PlainText);
    label_->setTextInteractionFlags(Qt::TextSelectableByMouse | Qt::LinksAccessibleByMouse);
    label_->setOpenExternalLinks(true);
    label_->setMinimumWidth(150);
    bubble_layout->addWidget(label_);

    if (is_user) {
        outer->addStretch(1);
        outer->addWidget(bubble_);
        outer->addWidget(avatar_, 0, Qt::AlignTop);
    } else {
        outer->addWidget(avatar_, 0, Qt::AlignTop);
        outer->addWidget(bubble_);
        outer->addStretch(1);
    }

    caret_timer_ = new QTimer(this);
    caret_timer_->setInterval(620);
    QObject::connect(caret_timer_, &QTimer::timeout, this, [this] { blink(); });
    set_text(text, false);
}

void ChatEntry::set_text(const std::string& text, bool rich) {
    raw_ = text;
    rich_ = rich;
    refresh_text();
}

void ChatEntry::set_sender(const std::string& text) {
    sender_->setText(QString::fromStdString(text));
    bool streaming = text.find("YAZIYOR") != std::string::npos;
    if (streaming != streaming_) {
        set_streaming(streaming);
    }
}

void ChatEntry::set_streaming(bool streaming) {
    if (streaming == streaming_) {
        return;
    }
    streaming_ = streaming;
    if (streaming) {
        caret_timer_->start();
    } else {
        caret_timer_->stop();
        caret_on_ = true;
    }
    refresh_text();
}

void ChatEntry::set_expanded(bool expanded) {
    expanded_ = expanded;
    bubble_->setMaximumWidth(expanded ? 1180 : 800);
    label_->setFont(hud_font(expanded ? 11 : 10, QFont::Weight::Normal, 0.15));
}

void ChatEntry::blink() {
    if (!streaming_) {
        caret_timer_->stop();
        return;
    }
    caret_on_ = !caret_on_;
    refresh_text();
}

void ChatEntry::refresh_text() {
    if (rich_ && !streaming_) {
        label_->setTextFormat(Qt::RichText);
        label_->setText(QString::fromStdString(render_markdown(raw_).empty() ? "…"
                                                                            : render_markdown(raw_)));
    } else {
        label_->setTextFormat(Qt::PlainText);
        std::string displayed;
        if (raw_.empty()) {
            displayed = "…";
        } else {
            displayed = raw_;
        }
        if (streaming_ && !rich_) {
            displayed += caret_on_ ? "  ▌" : "";
        }
        label_->setText(QString::fromStdString(displayed));
    }
}

void ChatEntry::copy_to_clipboard() {
    QClipboard* clipboard = QApplication::clipboard();
    if (clipboard != nullptr) {
        clipboard->setText(QString::fromStdString(raw_));
    }
    copy_button_->set_glyph("check");
    QTimer::singleShot(1300, this, [this] { copy_button_->set_glyph("copy"); });
}

void ChatEntry::enterEvent(QEnterEvent* event) {
    copy_button_->show();
    QWidget::enterEvent(event);
}

void ChatEntry::leaveEvent(QEvent* event) {
    copy_button_->hide();
    QWidget::leaveEvent(event);
}

// ---------------------------------------------------------------------------
// HudChat
// ---------------------------------------------------------------------------
HudChat::HudChat(QWidget* parent) : QWidget(parent) {
    auto* layout = new QVBoxLayout(this);
    layout->setContentsMargins(0, 0, 0, 0);
    layout->setSpacing(0);

    scroll_ = new QScrollArea();
    scroll_->setWidgetResizable(true);
    scroll_->setFrameShape(QFrame::NoFrame);
    scroll_->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    scroll_->setStyleSheet("background:transparent;border:none;");
    scroll_->viewport()->setStyleSheet("background:transparent;");
    container_ = new QWidget();
    container_->setStyleSheet("background:transparent;");
    layout_ = new QVBoxLayout(container_);
    layout_->setContentsMargins(10, 6, 14, 6);
    layout_->setSpacing(8);
    auto* empty = new ChatEmptyState();
    empty_ = empty;
    empty->on_suggested = [this](const QString& value) {
        if (on_suggested) on_suggested(value);
    };
    layout_->addWidget(empty_);
    layout_->addStretch(1);
    scroll_->setWidget(container_);
    layout->addWidget(scroll_);

    flush_timer_ = new QTimer(this);
    flush_timer_->setSingleShot(true);
    flush_timer_->setInterval(kStreamFlushMs);
    QObject::connect(flush_timer_, &QTimer::timeout, this, [this] { flush_pending(); });
}

void HudChat::insert_before_stretch(QWidget* widget) {
    layout_->insertWidget(std::max(0, layout_->count() - 1), widget);
}

ChatEntry* HudChat::add_message(const std::string& text, bool is_user) {
    flush_pending();
    const QString today = QDateTime::currentDateTime().toString("dd.MM.yyyy");
    if (last_day_ != today.toStdString()) {
        const std::string label = last_day_.empty() ? "Bugün" : today.toStdString();
        insert_before_stretch(new DayDivider(label));
        last_day_ = today.toStdString();
    }
    auto* entry = new ChatEntry(text, is_user);
    entry->set_expanded(expanded_);
    insert_before_stretch(entry);
    empty_->hide();
    scroll_to_bottom();
    return entry;
}

void HudChat::update_message(ChatEntry* entry, const std::string& text, bool rich) {
    if (entry == nullptr) {
        return;
    }
    if (rich) {
        pending_.erase(entry);
        entry->set_text(text, true);
        scroll_to_bottom();
        return;
    }
    pending_[entry] = text;
    if (!flush_timer_->isActive()) {
        flush_timer_->start();
    }
}

void HudChat::flush_pending() {
    if (pending_.empty()) {
        return;
    }
    auto pending = std::move(pending_);
    pending_.clear();
    for (const auto& [entry, text] : pending) {
        entry->set_text(text, false);
    }
    scroll_to_bottom();
}

void HudChat::clear() {
    flush_timer_->stop();
    pending_.clear();
    for (int index = layout_->count() - 1; index >= 0; --index) {
        QLayoutItem* item = layout_->itemAt(index);
        QWidget* widget = item != nullptr ? item->widget() : nullptr;
        if (widget == nullptr || widget == empty_) {
            continue;
        }
        layout_->takeAt(index);
        widget->setParent(nullptr);
        widget->deleteLater();
    }
    last_day_.clear();
    empty_->show();
    scroll_to_bottom();
}

int HudChat::count() const {
    int total = 0;
    for (int index = 0; index < layout_->count(); ++index) {
        QLayoutItem* item = layout_->itemAt(index);
        if (item != nullptr && dynamic_cast<ChatEntry*>(item->widget()) != nullptr) {
            ++total;
        }
    }
    return total;
}

void HudChat::set_expanded(bool expanded) {
    expanded_ = expanded;
    layout_->setContentsMargins(expanded ? 22 : 10, expanded ? 12 : 6, expanded ? 24 : 14,
                                expanded ? 10 : 6);
    for (int index = 0; index < layout_->count(); ++index) {
        QLayoutItem* item = layout_->itemAt(index);
        if (item != nullptr) {
            auto* entry = dynamic_cast<ChatEntry*>(item->widget());
            if (entry != nullptr) {
                entry->set_expanded(expanded);
            }
        }
    }
    scroll_to_bottom();
}

void HudChat::scroll_to_bottom() {
    QTimer::singleShot(16, this, [this] {
        QScrollBar* bar = scroll_->verticalScrollBar();
        if (bar != nullptr) {
            bar->setValue(bar->maximum());
        }
    });
}

}  // namespace jarvis::ui
