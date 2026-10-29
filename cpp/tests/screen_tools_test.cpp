// Ekran araçları birim testi — OCR dil eşlemesi, normalize_text eşleştirme ve
// seçim sırası, görüntü kutusundan gerçek ekran koordinatına çeviri, WIC PNG
// turu (kodla → çöz) ve inspect_image örneklemesi ekrandan bağımsız doğrulanır.
// Canlı kısımda yalnızca screen_wait okuma yapar. Ekran yakalama, Türkçe OCR ve
// ikinci iş parçacığında motor kurma kabul doğrulaması `--live` ile elle
// çalıştırılır; build.bat o kısmı çağırmaz (test paketi hızlı ve başsız kalsın).
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <optional>
#include <string>
#include <thread>
#include <vector>

#include "jarvis/ai/safety.hpp"
#include "jarvis/tools/ocr.hpp"
#include "jarvis/tools/screen_capture.hpp"
#include "jarvis/tools/ui_automation.hpp"

static int failures = 0;
// CHECK değişken sayıda argüman alır: json ve std::array gibi içinde virgül
// bulunan ifadeler tek argümanlık makroda argüman sayısını bozardı.
#define CHECK(...)                                                              \
    do {                                                                        \
        if (!(__VA_ARGS__)) {                                                   \
            std::printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #__VA_ARGS__); \
            ++failures;                                                         \
        }                                                                       \
    } while (0)

using jarvis::ai::ActionForbidden;
using jarvis::tools::ImageReport;
using jarvis::tools::OcrLine;
using jarvis::tools::OcrMatch;
using jarvis::tools::OcrWord;
using jarvis::tools::PixelBuffer;
using jarvis::tools::UiAutomation;
using nlohmann::json;

namespace {

OcrWord make_word(const std::string& text, double x, double y, double w, double h) {
    OcrWord word;
    word.text = text;
    word.x = x;
    word.y = y;
    word.width = w;
    word.height = h;
    return word;
}

OcrLine make_line(const std::string& text, std::vector<OcrWord> words) {
    OcrLine line;
    line.text = text;
    line.words = std::move(words);
    return line;
}

// ScreenOcr::find'in payload'ındaki matches dizisiyle aynı biçim; index seçimi
// JSON üzerinden yapıldığı için test de JSON kurar.
json matches_json(const std::vector<OcrMatch>& matches) {
    json out = json::array();
    for (const OcrMatch& match : matches) {
        out.push_back(json{{"text", match.text},
                           {"box", json::array({match.box[0], match.box[1], match.box[2],
                                                match.box[3]})},
                           {"score", match.score}});
    }
    return out;
}

template <class Fn>
void expect_forbidden(Fn&& fn, const std::string& fragment) {
    try {
        fn();
        std::printf("FAIL red beklenirken geçti: %s\n", fragment.c_str());
        ++failures;
    } catch (const ActionForbidden& error) {
        if (std::string(error.what()).find(fragment) == std::string::npos) {
            std::printf("FAIL yanlış red metni: beklenen [%s] gelen [%s]\n", fragment.c_str(),
                        error.what());
            ++failures;
        }
    }
}

PixelBuffer solid(long long width, long long height, unsigned char red, unsigned char green,
                  unsigned char blue) {
    PixelBuffer data;
    data.width = width;
    data.height = height;
    data.bgra.assign(static_cast<std::size_t>(width) * static_cast<std::size_t>(height) * 4, 0);
    for (std::size_t pixel = 0; pixel < data.bgra.size() / 4; ++pixel) {
        data.bgra[pixel * 4 + 0] = blue;
        data.bgra[pixel * 4 + 1] = green;
        data.bgra[pixel * 4 + 2] = red;
        data.bgra[pixel * 4 + 3] = 255;
    }
    return data;
}

bool near(double value, double target, double tolerance) {
    return std::fabs(value - target) <= tolerance;
}

#ifdef _WIN32
// Kabul doğrulaması: gerçek ekranı PNG'ye yazar, Türkçe OCR'ı hem bu iş
// parçacığında hem iki ayrı iş parçacığında çalıştırır. Python'un not düşdüğü
// tuzak budur — WinRT OCR motoru başka bir iş parçacığında kurulduğunda tanım
// çağrısı çöpe dönebiliyor; burada önbellek thread_local ve başarısızlıkta
// motor bir kez yeniden kuruluyor. Kutular sanal masaüstü dikdörtgeninin
// içinde kalmak zorunda (ikinci monitörde başlangıç negatiftir).
void run_live() {
    if (!jarvis::tools::ocr_engine_available()) {
        std::printf("canli kisim atlandi: Windows OCR motoru kurulu degil.\n");
        return;
    }
    const std::array<long long, 4> desktop = jarvis::tools::virtual_desktop_rect();
    std::printf("sanal masaüstü: %lldx%lld, baslangic (%lld,%lld)\n", desktop[2], desktop[3],
                desktop[0], desktop[1]);
    PixelBuffer shot;
    std::string error;
    CHECK(jarvis::tools::grab_screen(std::nullopt, shot, error));
    CHECK(shot.width == desktop[2]);
    CHECK(shot.height == desktop[3]);
    CHECK(static_cast<std::size_t>(shot.width) * static_cast<std::size_t>(shot.height) * 4 ==
          shot.bgra.size());

    const std::filesystem::path path =
        jarvis::tools::screen_cache_directory() / "live_capture_check.png";
    std::error_code ignore;
    std::filesystem::remove(path, ignore);
    CHECK(jarvis::tools::write_png(shot, path, error));
    const std::uintmax_t bytes = std::filesystem::file_size(path, ignore);
    PixelBuffer decoded;
    CHECK(jarvis::tools::read_image(path, decoded, error));
    CHECK(decoded.width == shot.width);
    CHECK(decoded.height == shot.height);
    std::printf("png: %llu bayt, %lldx%lld (ekranla ayni boyut)\n",
                static_cast<unsigned long long>(bytes), decoded.width, decoded.height);

    const json lines = jarvis::tools::ScreenOcr::read_lines(path, shot.origin_x, shot.origin_y,
                                                            "tr", 40, 1.0);
    std::printf("ocr dil: %s, satir sayisi: %lld\n", lines["language"].get<std::string>().c_str(),
                lines["count"].get<long long>());
    CHECK(!lines["language"].get<std::string>().empty());
    CHECK(lines["count"].get<long long>() > 0);
    for (const json& row : lines["lines"]) {
        const std::string text = row["text"].get<std::string>();
        bool turkish = false;
        for (const char ch : text) {
            if (static_cast<unsigned char>(ch) >= 0x80) turkish = true;
        }
        if (turkish) {
            std::printf("turkce ocr ornegi: %s\n", text.c_str());
            break;
        }
    }

    const json searched =
        jarvis::tools::ScreenOcr::find(path, "Dosya", shot.origin_x, shot.origin_y, "tr");
    std::printf("find 'Dosya': eslesme=%zu\n", searched["matches"].size());
    for (const json& row : searched["matches"]) {
        const long long x = row["center_x"].get<long long>();
        const long long y = row["center_y"].get<long long>();
        CHECK(x >= desktop[0] && x < desktop[0] + desktop[2]);
        CHECK(y >= desktop[1] && y < desktop[1] + desktop[3]);
        std::printf("  kutu=%lld,%lld,%lld,%lld merkez=(%lld,%lld) puan=%.2f metin=%s\n",
                    row["box"][0].get<long long>(), row["box"][1].get<long long>(),
                    row["box"][2].get<long long>(), row["box"][3].get<long long>(), x, y,
                    row["score"].get<double>(), row["text"].get<std::string>().c_str());
    }
    if (searched["matches"].empty() && searched["nearest"].is_array()) {
        std::printf("  bulunamadi, en yakin satirlar: %s\n", searched["nearest"].dump().c_str());
    }

    std::size_t first_count = 0;
    std::size_t second_count = 0;
    const auto read_on_this_thread = [&](std::size_t& out) {
        try {
            const json other =
                jarvis::tools::ScreenOcr::read_lines(path, 0, 0, "tr", 40, 1.0);
            out = static_cast<std::size_t>(other["count"].get<long long>());
        } catch (const std::exception& problem) {
            std::printf("FAIL ikinci is parcaciginda OCR: %s\n", problem.what());
            ++failures;
        }
    };
    std::thread left(read_on_this_thread, std::ref(first_count));
    std::thread right(read_on_this_thread, std::ref(second_count));
    left.join();
    right.join();
    std::printf("ayri is parcaciklarinda satir sayilari: %zu / %zu\n", first_count, second_count);
    CHECK(first_count > 0);
    CHECK(second_count > 0);

    std::filesystem::remove(path, ignore);
}
#endif  // _WIN32

}  // namespace

int main(int argc, char** argv) {
    // --- dil eşlemesi (Python LANGUAGE_TAGS) ---
    CHECK(jarvis::tools::ocr_language_tag("tr") == "tr-TR");
    CHECK(jarvis::tools::ocr_language_tag("TR") == "tr-TR");
    CHECK(jarvis::tools::ocr_language_tag("turkce") == "tr-TR");
    CHECK(jarvis::tools::ocr_language_tag("türkçe") == "tr-TR");
    CHECK(jarvis::tools::ocr_language_tag("en") == "en-US");
    CHECK(jarvis::tools::ocr_language_tag("English") == "en-US");
    CHECK(jarvis::tools::ocr_language_tag("İngilizce") == "en-US");
    CHECK(jarvis::tools::ocr_language_tag("").empty());       // boş → motor/profil dili
    CHECK(jarvis::tools::ocr_language_tag("DEUTSCH") == "deutsch");  // bilinmeyen aday olarak kalır

    // --- OCR satır eşleştirme: aksan katlama + puanlar ---
    const std::vector<OcrLine> menu = {
        make_line("Dosya Düzenle Kaydet", {make_word("Dosya", 10, 30, 50, 18),
                                           make_word("Düzenle", 70, 30, 60, 18),
                                           make_word("Kaydet", 140, 30, 55, 18)}),
        make_line("Dosya Ayarları", {make_word("Dosya", 10, 60, 50, 18),
                                     make_word("Ayarları", 70, 60, 70, 18)}),
        make_line("Dosya Dosyalarım", {make_word("Dosya", 10, 120, 50, 18),
                                       make_word("Dosyalarım", 70, 120, 80, 18)}),
        make_line("İptal", {make_word("İptal", 200, 90, 45, 18)}),
    };

    // Küçültülmüş sorgu, aksanlı OCR yazımıyla tam eşleşir: "Düzenle" ↔ "duzenle".
    const std::vector<OcrMatch> duzenle = jarvis::tools::match_ocr_lines(menu, "duzenle");
    CHECK(duzenle.size() == 1);
    CHECK(!duzenle.empty() && duzenle[0].score == 1.0);
    CHECK(!duzenle.empty() && duzenle[0].text == "Düzenle");
    CHECK(!duzenle.empty() && (duzenle[0].box == std::array<long long, 4>{70, 30, 60, 18}));
    // Sorgu tarafındaki aksan da düşer: "Düzenle" ile "duzenle" aynı sonucu verir.
    CHECK(jarvis::tools::match_ocr_lines(menu, "Düzenle").size() == 1);
    // "İptal" ↔ "iptal": noktalı İ Python ile aynı şekilde i'ye iner.
    const std::vector<OcrMatch> iptal = jarvis::tools::match_ocr_lines(menu, "iptal");
    CHECK(iptal.size() == 1);
    CHECK(!iptal.empty() && iptal[0].score == 1.0);
    CHECK(!iptal.empty() && iptal[0].text == "İptal");

    // Tam kelime önce gelir, önek/eşleşme sonra; puanlar azalan sırada listelenir.
    const std::vector<OcrMatch> dosya = jarvis::tools::match_ocr_lines(menu, "dosya");
    CHECK(dosya.size() == 4);
    CHECK(dosya.size() >= 3 && dosya[0].score == 1.0 && dosya[1].score == 1.0 &&
          dosya[2].score == 1.0);
    // Eşit puanlarda yukarıdan aşağı sırası korunur (tıklanacak satır bellidir).
    CHECK(dosya.size() >= 3 && dosya[0].box[1] == 30 && dosya[1].box[1] == 60 &&
          dosya[2].box[1] == 120);
    CHECK(dosya.size() == 4 && dosya[3].score == 0.9 && dosya[3].text == "Dosyalarım");
    // Önek 0.9, alt dizi 0.8.
    const std::vector<OcrMatch> ayar = jarvis::tools::match_ocr_lines(menu, "ayar");
    CHECK(ayar.size() == 1);
    CHECK(!ayar.empty() && ayar[0].score == 0.9);
    const std::vector<OcrMatch> ayd = jarvis::tools::match_ocr_lines(menu, "ayd");
    CHECK(ayd.size() == 1);
    CHECK(!ayd.empty() && ayd[0].score == 0.8);
    // Çok kelimeli sorgu, kelimeleri kapsayan birleşik kutuyu verir.
    const std::vector<OcrMatch> pair = jarvis::tools::match_ocr_lines(menu, "Dosya Düzenle");
    CHECK(pair.size() == 1);
    CHECK(!pair.empty() && pair[0].score == 1.0);
    CHECK(!pair.empty() && pair[0].text == "Dosya Düzenle");
    CHECK(!pair.empty() && (pair[0].box == std::array<long long, 4>{10, 30, 120, 18}));
    CHECK(jarvis::tools::match_ocr_lines(menu, "yazdır").empty());
    CHECK(jarvis::tools::match_ocr_lines(menu, "   ").empty());
    // Satır yedeği: kelime penceresi kurulamıyorsa (tek kelime, iki kelimelik
    // sorgu) tüm satır 0.5 puanla aday olur.
    const std::vector<OcrLine> single = {
        make_line("Dosya Düzenle", {make_word("Dosya Düzenle", 10, 150, 120, 18)})};
    const std::vector<OcrMatch> fallback = jarvis::tools::match_ocr_lines(single, "dosya düzenle");
    CHECK(fallback.size() == 1);
    CHECK(!fallback.empty() && fallback[0].score == 0.5);

    // --- görüntü kutusundan gerçek ekran merkezi ---
    // Birinci monitör: başlangıç (0,0), merkez kutu ortası.
    CHECK((jarvis::tools::ocr_screen_center({100, 50, 40, 20}, 0, 0) ==
           std::array<long long, 2>{120, 60}));
    // Sol tarzdaki ikinci monitör: sanal masaüstü başlangıcı negatif.
    CHECK((jarvis::tools::ocr_screen_center({100, 50, 40, 20}, -1920, 0) ==
           std::array<long long, 2>{-1800, 60}));
    // Pencere bölgesi yakalamada başlangıç pencerenin sol-üst köşesidir.
    CHECK((jarvis::tools::ocr_screen_center({10, 10, 24, 20}, 320, 180) ==
           std::array<long long, 2>{342, 200}));

    // --- eşleşme seçimi (index doğrulaması) ---
    const json two = matches_json(duzenle);
    CHECK(two.is_array());
    CHECK(jarvis::tools::ocr_selected_index(two, 0).has_value());
    CHECK(jarvis::tools::ocr_selected_index(json::array({1, 2}), 1) == std::optional<std::size_t>(1));
    expect_forbidden([&] { jarvis::tools::ocr_selected_index(json::array({1, 2}), 2); },
                     "OCR eşleşme sırası geçersiz: 2 (bulunan: 2)");
    expect_forbidden([&] { jarvis::tools::ocr_selected_index(json::array({1, 2}), -1); },
                     "OCR eşleşme sırası geçersiz: -1 (bulunan: 2)");
    // Boş eşleşmede seçim yoktur ama Python gibi üst sınır hatası da verilmez;
    // negatif sıra yine reddedilir.
    CHECK(!jarvis::tools::ocr_selected_index(json::array(), 0).has_value());
    CHECK(!jarvis::tools::ocr_selected_index(json::array(), 5).has_value());
    expect_forbidden([&] { jarvis::tools::ocr_selected_index(json::array(), -1); },
                     "(bulunan: 0)");
    CHECK(!jarvis::tools::ocr_selected_index(json(nullptr), 0).has_value());

    // --- bulunamadığında en yakın satırlar (model kendini düzeltsin diye) ---
    CHECK(jarvis::tools::ocr_text_similarity("Kaydet", "kaydet") == 1.0);
    CHECK(jarvis::tools::ocr_text_similarity("Kaydet", "Kaydet") == 1.0);
    CHECK(jarvis::tools::ocr_text_similarity("", "Kaydet") == 0.0);
    CHECK(jarvis::tools::ocr_text_similarity("Kaydet", "Dosya") <
          jarvis::tools::ocr_text_similarity("Kaydet", "Kaydetti"));
    const std::vector<OcrLine> screen = {
        make_line("Dosya Düzenle Görünüm", {make_word("Dosya", 0, 0, 40, 16)}),
        make_line("Yardım Et", {make_word("Yardım", 0, 20, 40, 16)}),
        make_line("Kaydet", {make_word("Kaydet", 0, 40, 40, 16)}),
        make_line("", {make_word("", 0, 60, 1, 1)}),  // boş satır listelenmez
    };
    const json nearest = jarvis::tools::ocr_nearest_lines(screen, "duzenl", 2);
    CHECK(nearest.is_array());
    CHECK(nearest.size() == 2);
    CHECK(nearest.size() >= 1 && nearest[0]["text"] == "Dosya Düzenle Görünüm");
    // Yazım eksik sorgu en benzer satırı bulur; ilgisiz satırların puanı düşer.
    CHECK(nearest.size() == 2 && nearest[0]["similarity"].get<double>() >
                                     nearest[1]["similarity"].get<double>());
    CHECK(nearest.size() == 2 && nearest[1]["similarity"].get<double>() < 0.1);
    CHECK(jarvis::tools::ocr_nearest_lines(screen, "duzenl", 0).empty());
    CHECK(jarvis::tools::ocr_nearest_lines({}, "duzenl", 3).empty());

#ifdef _WIN32
    jarvis::tools::make_process_dpi_aware();

    // --- PNG kodlama turu: sentetik 8x8 tamponu yaz, WIC ile geri oku ---
    PixelBuffer pattern;
    pattern.width = 8;
    pattern.height = 8;
    pattern.origin_x = -1234;
    pattern.origin_y = 56;
    pattern.bgra.assign(8 * 8 * 4, 0);
    for (std::size_t pixel = 0; pixel < 8 * 8; ++pixel) {
        pattern.bgra[pixel * 4 + 0] = static_cast<unsigned char>(pixel * 3 % 256);
        pattern.bgra[pixel * 4 + 1] = static_cast<unsigned char>(pixel * 5 % 256);
        pattern.bgra[pixel * 4 + 2] = static_cast<unsigned char>(pixel * 7 % 256);
        pattern.bgra[pixel * 4 + 3] = 255;
    }
    const std::filesystem::path temp =
        std::filesystem::temp_directory_path() / "jarvis_screen_tools_test.png";
    std::error_code ignore;
    std::filesystem::remove(temp, ignore);
    std::string error;
    CHECK(jarvis::tools::write_png(pattern, temp, error));
    CHECK(std::filesystem::exists(temp));
    CHECK(std::filesystem::file_size(temp) > 0);
    PixelBuffer back;
    CHECK(jarvis::tools::read_image(temp, back, error));
    CHECK(back.width == 8);
    CHECK(back.height == 8);
    CHECK(back.bgra == pattern.bgra);  // BGRA kanalları bayt bayt aynı dönmeli
    std::filesystem::remove(temp, ignore);
    // Okunamayan dosya dürüstçe false + neden üretir, istisna fırlatmaz.
    PixelBuffer missing;
    CHECK(!jarvis::tools::read_image(temp, missing, error));
    CHECK(!error.empty());

    // --- inspect_image örneklemesi: boşluk oranı ve baskın renkler ---
    const ImageReport black = jarvis::tools::analyze_image(solid(32, 32, 0, 0, 0));
    CHECK(black.width == 32);
    CHECK(black.height == 32);
    CHECK(near(black.non_white_ratio, 1.0, 0.001));
    CHECK(!black.blank);
    CHECK(black.content_bbox.has_value());
    CHECK(black.dominant.size() == 1);
    CHECK(!black.dominant.empty() && black.dominant[0].rgb == std::array<long long, 3>{0, 0, 0});
    CHECK(!black.dominant.empty() && near(black.dominant[0].ratio, 1.0, 0.001));

    PixelBuffer half = solid(32, 32, 0, 0, 0);
    for (long long row = 0; row < 32; ++row) {
        for (long long column = 0; column < 16; ++column) {
            const std::size_t offset = static_cast<std::size_t>(row * 32 + column) * 4;
            half.bgra[offset + 0] = 255;
            half.bgra[offset + 1] = 255;
            half.bgra[offset + 2] = 255;
        }
    }
    const ImageReport mixed = jarvis::tools::analyze_image(half);
    CHECK(near(mixed.non_white_ratio, 0.5, 0.001));
    CHECK(!mixed.blank);
    CHECK(mixed.dominant.size() == 2);
    // Izgara yarım doluluğu görür: sol üçte bir beyaz (boş), sağ üçte bir siyah (dolu).
    CHECK(near(mixed.grid[0][0], 0.0, 0.001));
    CHECK(near(mixed.grid[0][2], 1.0, 0.001));

    const ImageReport white = jarvis::tools::analyze_image(solid(32, 32, 255, 255, 255));
    CHECK(near(white.non_white_ratio, 0.0, 0.001));
    CHECK(white.blank);
    CHECK(!white.content_bbox.has_value());

    // --- screen_wait canlı okuma: OCR/ekran görüntüsü açmadan zaman aşımı ---
    std::size_t cache_before = 0;
    const std::filesystem::path cache = jarvis::tools::screen_cache_directory();
    if (std::filesystem::exists(cache)) {
        for (const auto& entry : std::filesystem::directory_iterator(cache)) {
            if (!entry.is_regular_file()) continue;
            ++cache_before;
        }
    }
    try {
        const json waited = json::parse(UiAutomation().wait_for(
            json{{"text", "jarvis-test-metin-yok-8842"}, {"timeout", 0.5}}));
        CHECK(waited["found"].get<bool>() == false);
        const long long elapsed = waited["elapsed_ms"].get<long long>();
        CHECK(elapsed >= 400);
        CHECK(elapsed <= 4000);
        CHECK(waited["detail"].get<std::string>().find("bulunamadı") != std::string::npos);
        CHECK(waited["note"].get<std::string>().find("OCR açmaz") != std::string::npos);
        std::printf("screen_wait canlı: found=false, elapsed_ms=%lld\n", elapsed);
    } catch (const ActionForbidden& forbidden) {
        std::printf("screen_wait atlandı (güvenli masaüstü/odak): %s\n", forbidden.what());
    }
    // Araç ekran yakalamadığı için önbellekte yeni dosya oluşmamalı.
    std::size_t cache_after = 0;
    if (std::filesystem::exists(cache)) {
        for (const auto& entry : std::filesystem::directory_iterator(cache)) {
            if (!entry.is_regular_file()) continue;
            ++cache_after;
        }
    }
    CHECK(cache_after == cache_before);
#endif  // _WIN32

#ifdef _WIN32
    // Elle çalıştırılan kabul doğrulaması: build.bat bu bayrağı hiç vermez,
    // böylece test paketi ekranı yakmadan ve OCR motorunu beklemeden geçer.
    if (argc > 1 && std::strcmp(argv[1], "--live") == 0) run_live();
#endif

    if (failures == 0) std::printf("screen_tools_test OK\n");
    return failures == 0 ? 0 : 1;
}
