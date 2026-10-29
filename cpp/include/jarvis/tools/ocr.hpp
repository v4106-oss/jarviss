#pragma once

// Ekran OCR — Python app/core/ocr_tools.py karşılığı.
//
// Motor Windows.Media.Ocr (C++/WinRT); yeni üçüncü parti bağımlılık yok, yalnız
// Windows SDK (windowsapp.lib). Eşleştirme, pencere araçlarıyla aynı
// normalize_text katlamasını kullanır ("Düzenle" ↔ "duzenle", "İptal" ↔ "iptal").
//
// Tuzak (Python'da not düşülmüş): WinRT OCR motoru başka bir iş parçacığında
// kurulduğunda tanım çağrısı kullanılamaz hale gelebiliyor; Python bunu
// refresh=True ile yeniden kurarak atlatıyor. Burada önbellek iş parçacığı
// ölçeğinde (thread_local) ve tanım başarısız olursa motor bir kez yeniden
// kuruluyor. Motor, ajan iş parçacığını kilitleyen tanımı olay tabanlı beklemeye
// dayanarak yapar; GUI iş parçacığı hiç bekletilmez.

#include <nlohmann/json.hpp>

#include <array>
#include <cstddef>
#include <filesystem>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace jarvis::tools {

// Python LANGUAGE_TAGS eşlemesi (tr/turkce/türkçe → tr-TR, en/english/ingilizce → en-US).
// Bilinmeyen değer küçültülmüş hâliyle aday olarak denenebilir; boşsa boş döner.
std::string ocr_language_tag(const std::string& raw);

// Motorun bir görüntüden okuduğu ham sonuç: satır metni ve kelime kutuları.
// Kutular görüntü pikselidir; gerçek ekran koordinatına origin eklenerek geçilir.
struct OcrWord {
    std::string text;
    double x = 0.0;
    double y = 0.0;
    double width = 0.0;
    double height = 0.0;
};

struct OcrLine {
    std::string text;
    std::vector<OcrWord> words;
};

// find() eşleşmesi: kelime/kelime grubu kutusu ve güven puanı
// (1.0 tam, 0.9 ön ek, 0.8 alt dizi, 0.5 tüm satır).
struct OcrMatch {
    std::string text;
    std::string line;
    std::array<long long, 4> box{};  // [left, top, genişlik, yükseklik]
    double score = 0.0;
};

// Python ScreenOCR._match — saf mantık, WinRT gerekmez; birim testi burada.
std::vector<OcrMatch> match_ocr_lines(const std::vector<OcrLine>& lines, const std::string& query);

// Görüntü kutusundan gerçek ekran merkezi: [sol, üst, genişlik, yükseklik] +
// ekran düzlemi başlangıcı. find() bu merkezi tıklama için verir; ikinci
// monitörde başlangıç negatiftir.
std::array<long long, 2> ocr_screen_center(const std::array<long long, 4>& box, long long origin_x,
                                           long long origin_y);

// find_text_on_screen'in eşleşme seçimi (Python index doğrulaması): negatif sıra
// her zaman hata, aralık dışı sıra yalnızca eşleşme varsa hata; liste boşsa
// seçim yok (nullopt).
std::optional<std::size_t> ocr_selected_index(const nlohmann::json& matches, long long index);

// Normalize edilmiş metinler üzerinde bigram benzerliği (0..1). Python'da yok:
// OCR bir metni bulamadığında model kendi düzeltebilsin diye "nearest" alanı
// için kullanılıyor (PARITE notu).
double ocr_text_similarity(const std::string& left, const std::string& right);

// Okunan satırların sorguya en yakın `count` tanesi [{text, similarity}].
nlohmann::json ocr_nearest_lines(const std::vector<OcrLine>& lines, const std::string& query,
                                 std::size_t count = 3);

// Windows OCR motoru kurulu mu? (en az bir aday dil ya da profil dili.)
bool ocr_engine_available();

// PNG dosyasını okur ve satır/kelime kutularını döndürür; ikinci değer kullanılan
// dil etiketi. Motor ya da çözme başarısızsa jarvis::ai::ActionForbidden fırlatılır.
// Çağıran iş parçacığını tanım bitene dek bekletir (AIWorker hattı için tasarlandı).
std::pair<std::vector<OcrLine>, std::string> ocr_recognize_file(const std::filesystem::path& path,
                                                                 const std::string& language);

// ScreenOCR — yürütücünün çağırdığı iki giriş noktası (read_lines, find).
class ScreenOcr {
public:
    static nlohmann::json read_lines(const std::filesystem::path& image, long long origin_x,
                                      long long origin_y, const std::string& language,
                                      long long limit, double scale);
    static nlohmann::json find(const std::filesystem::path& image, const std::string& query,
                               long long origin_x, long long origin_y,
                               const std::string& language);
};

}  // namespace jarvis::tools
