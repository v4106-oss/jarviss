#pragma once

#include <filesystem>
#include <string>

namespace jarvis::log {

// Proje kökü altındaki logs/ dizinini döner (yoksa oluşturur).
std::filesystem::path logs_dir();

// Kalıcı çökme günlüğüne zaman damgalı bir başlık ekler (main.py karşılığı).
void write_crash_header();

// Başlatma hatasını logs/baslatma_hatasi.log dosyasına yazar ve Windows'ta
// kullanıcıya mesaj kutusu gösterir.
void report_startup_error(const std::string& details);

}  // namespace jarvis::log
