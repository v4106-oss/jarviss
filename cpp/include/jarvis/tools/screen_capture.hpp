#pragma once

// ScreenCapture — Python app/core/desktop.py (DesktopTools.capture) ve
// app/core/ocr_tools.py (grab_region) karşılığı.
//
// Pillow ve ImageGrab yoktur: sanal masaüstünün tamamı (veya bir pencere
// dikdörtgeni) Win32 BitBlt + CreateDIBSection ile 32 bit BGRA tampona okunur,
// PNG olarak Windows Imaging Component (WIC) ile kodlanır. Yeni üçüncü taraf
// bağımlılık yoktur — GDI, WIC ve C++/WinRT Windows SDK'nın kendi parçalarıdır.
//
// Dikkat: süreç monitör başına DPI farkındalığı için main.cpp'de
// make_process_dpi_aware() çağırır. Bu yapılmazsa Windows, ölçümleri DPI
// ölçeğine göre sahte değerlerle döndürür; piksel boyutu ile SendInput
// koordinatları kayar ve tıklamalar hedefi vurmaz.
//
// Bilinçli sapmalar (PARITE.md satırı 21-23): Python 1920 piksele küçültülmüş
// JPEG tutar ve 20 dosya saklar; C++ ölçeklendirmeden PNG yazar (ölçek daima
// 1.000, koordinat tartışması yok) ve ekran başına 50, önizleme başına keep/2 =
// 25 dosya saklar.

#include <nlohmann/json.hpp>

#include <array>
#include <cstddef>
#include <filesystem>
#include <functional>
#include <optional>
#include <string>
#include <vector>

namespace jarvis::tools {

// 32 bit BGRA piksel tamponu. origin_* tamponun gerçek ekran koordinatı düzlemi
// (sanal masaüstü sol-üst köşesi); OCR kutuları bu ofisle ekrana çevrilir.
struct PixelBuffer {
    long long width = 0;
    long long height = 0;
    long long origin_x = 0;
    long long origin_y = 0;
    std::vector<unsigned char> bgra;

    std::size_t stride() const { return static_cast<std::size_t>(width) * 4; }
};

// Süreci monitör başına DPI farkındalıklı yapar. main.cpp'de QApplication'dan
// önce bir kez çağrılır; tekrar çağrılmak zararsızdır (Windows zaten ayarlıysa
// çağırmayı reddeder ve biz yok sayarız).
void make_process_dpi_aware();

// Sanal masaüstü dikdörtgeni: [sol, üst, genişlik, yükseklik]. DPI farkındalığı
// olmadan değerler ölçeklenmiş olur — bu yüzden çağrıdan önce
// make_process_dpi_aware() koşulmalıdır.
std::array<long long, 4> virtual_desktop_rect();

// %LOCALAPPDATA%\Jarvis\cache\screen (yoksa oluşturulur).
std::filesystem::path screen_cache_directory();

// screen_*.png deseninden en fazla keep, inspect_*.png deseninden en fazla
// keep / 2 en yeni dosyayı bırakır; eskileri siler. Ad sıralaması zaman damalı
// adlar için kronolojik sırayla aynıdır.
void prune_cache(const std::filesystem::path& directory, std::size_t keep = 50);

// region verilirse yalnızca o ekran dikdörtgeni, verilmezse sanal masaüstünün
// tamamı yakalanır. Bölge ekran dışında kalıyorsa false + neden.
bool grab_screen(const std::optional<std::array<long long, 4>>& region, PixelBuffer& out,
                 std::string& error);

// BGRA tamponu PNG olarak yazar (WIC). Alpha kanalı 255'e sabitlenir.
bool write_png(const PixelBuffer& data, const std::filesystem::path& path, std::string& error);

// Herhangi bir WIC'ın çözebildiği görseli (PNG/JPG/BMP…) 32 bit BGRA olarak okur.
bool read_image(const std::filesystem::path& path, PixelBuffer& out, std::string& error);

// En uzun kenarı en fazla max_side olacak şekilde en-uygun (nearest) küçültür.
// max_side >= mevcut boyutsa tampon olduğu gibi kopyalanır.
PixelBuffer shrink_image(const PixelBuffer& data, long long max_side);

struct ColorShare {
    std::array<long long, 3> rgb{};
    double ratio = 0.0;
};

struct ImageReport {
    long long width = 0;
    long long height = 0;
    long long sample_width = 0;
    long long sample_height = 0;
    double non_white_ratio = 0.0;  // 245+ üç kanal dışındaki piksel oranı
    bool blank = false;            // non_white_ratio < 0.005
    std::optional<std::array<long long, 4>> content_bbox;  // özgün görüntü pikseli
    std::array<std::array<double, 3>, 3> grid{};           // 3x3 doluluk oranları
    std::vector<ColorShare> dominant;                       // en fazla 5 renk
};

// Python inspect_image'in örneklemesi: 320 pikselle küçültülmüş kopya üzerinde
// boşluk oranı, baskın renkler (32'lik kovalar) ve 3x3 doluluk ızgarası.
ImageReport analyze_image(const PixelBuffer& data, long long sample_side = 320);

// capture_screen aracı: isteğe bağlı pencereyi öne getirir, sanal masaüstünü
// PNG'ye yazar, görüntü yolu sink ile modelin sonraki adımına verilür,
// read_text=true ise OCR satırlarını gerçek ekran koordinatlarıyla ekler.
// Türkçe yanıt metni döner (Python biçimiyle aynı bilgi alanları).
std::string capture_screen(const nlohmann::json& args,
                           const std::function<void(const std::string&)>& sink);

// inspect_image aracı: yolu çözülmüş görseli raporlar ve en fazla 1200 piksel
// önizlemeyi sink ile modele gönderir. JSON metni döner.
std::string inspect_image(const std::filesystem::path& path,
                          const std::function<void(const std::string&)>& sink);

}  // namespace jarvis::tools
