#include <jarvis/ui/weather_modal.hpp>

#include <QGridLayout>
#include <QHBoxLayout>
#include <QLabel>
#include <QVBoxLayout>

#include <sstream>
#include <iomanip>

#include <cmath>

#include <jarvis/ui/theme.hpp>

namespace jarvis::ui {

namespace {

std::string fmt_temp(double value) {
    std::ostringstream out;
    out << static_cast<int>(std::lround(value)) << "°";
    return out.str();
}

std::string fmt_one_decimal(double value) {
    std::ostringstream out;
    out << std::fixed << std::setprecision(1) << value;
    return out.str();
}

}  // namespace

WeatherModal::WeatherModal(QWidget* parent, const WeatherData& data)
    : HudModal(parent, "Hava Durumu",
               data.location + " · " + data.region + " · " + data.condition_label, 620, 0, 24,
               true, false, false),
      data_(data) {
    const Theme& theme = current_theme();

    auto* header = new QHBoxLayout();
    header->setSpacing(18);

    auto* icon = new WeatherIcon(data_.condition, 64);
    header->addWidget(icon);

    auto* summary = new QVBoxLayout();
    summary->setSpacing(2);
    auto* temp = hud_label(fmt_temp(data_.temp), 40, theme.text, QFont::Weight::DemiBold, 0.4);
    summary->addWidget(temp);
    auto* detail = hud_label("Hissedilen " + fmt_temp(data_.feels_like) + " · " +
                                 data_.condition_label,
                             10, theme.muted, QFont::Weight::Medium, 1.2);
    summary->addWidget(detail);
    header->addLayout(summary, 1);
    content_->addLayout(header);
    content_->addSpacing(6);

    auto* range = new TempRangeBar();
    range->set_range(data_.low, data_.high, data_.temp);
    content_->addWidget(range);

    auto* metrics = new QGridLayout();
    metrics->setSpacing(10);
    auto* humidity = new RingGauge(data_.humidity, "NEM", "%", 84, true);
    metrics->addWidget(humidity, 0, 0);
    auto* wind = new MetricCard("RÜZGÂR", std::to_string(static_cast<int>(std::lround(data_.wind))) +
                                              " km/s");
    metrics->addWidget(wind, 0, 1);
    auto* uv = new MetricCard("UV İNDEKSİ", std::to_string(data_.uv));
    metrics->addWidget(uv, 0, 2);
    auto* rain = new MetricCard("YAĞIŞ", fmt_one_decimal(data_.rain) + " mm");
    metrics->addWidget(rain, 0, 3);
    content_->addLayout(metrics);

    if (!data_.hourly.empty()) {
        auto* curve = new TempCurve();
        curve->set_hours(data_.hourly);
        content_->addWidget(curve);
    }

    if (!data_.days.empty()) {
        auto* days_row = new QHBoxLayout();
        days_row->setSpacing(12);
        for (const auto& day : data_.days) {
            auto* cell = new QVBoxLayout();
            cell->setSpacing(4);
            auto* label = hud_label(day.label, 10, theme.text_soft, QFont::Weight::DemiBold, 1.4);
            label->setAlignment(Qt::AlignCenter);
            cell->addWidget(label);
            auto* day_icon = new WeatherIcon(day.condition, 30);
            cell->addWidget(day_icon, 0, Qt::AlignHCenter);
            auto* temps = hud_label(fmt_temp(day.high) + " / " + fmt_temp(day.low), 10,
                                    theme.muted, QFont::Weight::Medium, 1.0);
            temps->setAlignment(Qt::AlignCenter);
            cell->addWidget(temps);
            days_row->addLayout(cell, 1);
        }
        content_->addLayout(days_row);
    }

    content_->addStretch(1);
}

}  // namespace jarvis::ui
