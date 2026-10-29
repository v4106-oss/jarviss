#include "jarvis/tools/ocr.hpp"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cmath>
#include <filesystem>
#include <map>
#include <memory>
#include <set>
#include <string>
#include <vector>

#include "jarvis/ai/safety.hpp"
#include "jarvis/core/util.hpp"
#include "jarvis/core/win_path.hpp"
#include "jarvis/tools/window_tools.hpp"

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <ole2.h>

#include <winrt/base.h>
#include <winrt/Windows.Foundation.h>
#include <winrt/Windows.Foundation.Collections.h>
#include <winrt/Windows.Globalization.h>
#include <winrt/Windows.Graphics.Imaging.h>
#include <winrt/Windows.Media.Ocr.h>
#include <winrt/Windows.Storage.h>
#include <winrt/Windows.Storage.Streams.h>
#endif

using nlohmann::json;

namespace jarvis::tools {
namespace {

// Python _normalize ile aynı görevi window_tools::normalize_text görüyor:
// İ/I/ı → i, aksan katlama, küçültme, boşluk tekilleştirme.
std::vector<std::string> split_words(const std::string& folded) {
    std::vector<std::string> out;
    std::size_t index = 0;
    while (index < folded.size()) {
        while (index < folded.size() && folded[index] == ' ') ++index;
        const std::size_t start = index;
        while (index < folded.size() && folded[index] != ' ') ++index;
        if (index > start) out.push_back(folded.substr(start, index - start));
    }
    return out;
}

long long round_pixel(double value) {
    return static_cast<long long>(std::llround(value));
}

// Bir kelime aralığının dışa geçen kutusu; ham tamsayı (Python sözlük değerleri).
std::array<double, 4> words_extent(const std::vector<OcrWord>& words, std::size_t first,
                                   std::size_t last) {
    double left = 0.0;
    double top = 0.0;
    double right = 0.0;
    double bottom = 0.0;
    for (std::size_t i = first; i < last; ++i) {
        const OcrWord& word = words[i];
        if (i == first) {
            left = word.x;
            top = word.y;
            right = word.x + word.width;
            bottom = word.y + word.height;
            continue;
        }
        left = std::min(left, word.x);
        top = std::min(top, word.y);
        right = std::max(right, word.x + word.width);
        bottom = std::max(bottom, word.y + word.height);
    }
    return {left, top, right - left, bottom - top};
}

// Python _match.add içindeki kutu: piksele yuvarlanmış [left, top, genişlik, yükseklik].
std::array<long long, 4> rounded_box(const std::array<double, 4>& extent) {
    return {round_pixel(extent[0]), round_pixel(extent[1]), round_pixel(extent[2]),
            round_pixel(extent[3])};
}

}  // namespace

std::string ocr_language_tag(const std::string& raw) {
    static const std::map<std::string, std::string> kTags = {
        {"tr", "tr-TR"}, {"turkce", "tr-TR"}, {"en", "en-US"},
        {"english", "en-US"}, {"ingilizce", "en-US"},
    };
    // "türkçe" de normalize_text ile "turkce"ye iner; ayrı anahtar gerekmez.
    const std::string folded = normalize_text(raw);
    if (folded.empty()) return {};
    const auto found = kTags.find(folded);
    return found == kTags.end() ? folded : found->second;
}

std::vector<OcrMatch> match_ocr_lines(const std::vector<OcrLine>& lines,
                                      const std::string& query) {
    std::vector<OcrMatch> matches;
    const std::string needle = normalize_text(query);
    if (needle.empty()) return matches;
    const std::vector<std::string> tokens = split_words(needle);
    if (tokens.empty()) return matches;

    std::set<std::array<long long, 4>> seen;
    const auto add = [&](const std::vector<OcrWord>& words, std::size_t first, std::size_t last,
                         const std::string& text, const std::string& line, double score) {
        const std::array<long long, 4> box = rounded_box(words_extent(words, first, last));
        if (!seen.insert(box).second) return;  // aynı kutu ikinci kez listelenmez
        matches.push_back(OcrMatch{text, line, box, score});
    };

    for (const OcrLine& line : lines) {
        const std::vector<OcrWord>& words = line.words;
        // Kelime penceresi ancak satırda yeterli kelime varsa denenir (Python
        // len(window) < len(tokens) satırını atlar); kısa satırlar yedek denemeye kalır.
        for (std::size_t start = 0; words.size() >= tokens.size() &&
                                   start + tokens.size() <= words.size();
             ++start) {
            std::string combined;
            std::string raw;
            for (std::size_t offset = 0; offset < tokens.size(); ++offset) {
                if (offset) {
                    combined += ' ';
                    raw += ' ';
                }
                combined += normalize_text(words[start + offset].text);
                raw += words[start + offset].text;
            }
            double score = 0.0;
            if (combined == needle)
                score = 1.0;
            else if (combined.rfind(needle, 0) == 0)
                score = 0.9;
            else if (combined.find(needle) != std::string::npos)
                score = 0.8;
            else
                continue;
            add(words, start, start + tokens.size(), raw, line.text, score);
        }
        // Satır tümüyle eşleşme yedegi: yalnızca henüz hiç kelime eşleşmesi yokken.
        if (matches.empty() && !line.text.empty() &&
            normalize_text(line.text).find(needle) != std::string::npos) {
            add(line.words, 0, line.words.size(), line.text, line.text, 0.5);
        }
    }

    std::stable_sort(matches.begin(), matches.end(),
                     [](const OcrMatch& a, const OcrMatch& b) {
                         if (a.score != b.score) return a.score > b.score;
                         if (a.box[1] != b.box[1]) return a.box[1] < b.box[1];
                         return a.box[0] < b.box[0];
                     });
    return matches;
}

std::array<long long, 2> ocr_screen_center(const std::array<long long, 4>& box, long long origin_x,
                                           long long origin_y) {
    // Python find(): merkez yuvarlanmış görüntü kutusundan türer, sonra ekran
    // düzlemi başlangıcı eklenir — ikinci monitörde bu başlangıç negatiftir.
    return {origin_x + round_pixel(box[0] + box[2] / 2.0),
            origin_y + round_pixel(box[1] + box[3] / 2.0)};
}

std::optional<std::size_t> ocr_selected_index(const json& matches, long long index) {
    // Python: negatif sıra her zaman hata; boş eşleşmede üst sınır denetlenmez
    // (seçilecek şey zaten yok). "bulunan" mesajı eşleşme sayısıyla aynı kalır.
    const long long count = matches.is_array() ? static_cast<long long>(matches.size()) : 0;
    if (index < 0 || (count > 0 && index >= count)) {
        throw ai::ActionForbidden("OCR eşleşme sırası geçersiz: " + std::to_string(index) +
                                 " (bulunan: " + std::to_string(count) + ")");
    }
    if (count == 0) return std::nullopt;
    return static_cast<std::size_t>(index);
}

namespace {

// Tek karakterli metinlerde bigram yoktur; tekil kod noktası kendi çifti sayılır.
std::map<std::pair<unsigned int, unsigned int>, int> bigrams(const std::vector<unsigned int>& points) {
    std::map<std::pair<unsigned int, unsigned int>, int> out;
    if (points.size() < 2) {
        for (const unsigned int point : points) out[{point, point}] += 1;
        return out;
    }
    for (std::size_t i = 0; i + 1 < points.size(); ++i) {
        out[{points[i], points[i + 1]}] += 1;
    }
    return out;
}

}  // namespace

double ocr_text_similarity(const std::string& left, const std::string& right) {
    const std::string a = normalize_text(left);
    const std::string b = normalize_text(right);
    if (a.empty() || b.empty()) return 0.0;
    if (a == b) return 1.0;
    const auto first = bigrams(code_points(a));
    const auto second = bigrams(code_points(b));
    std::size_t shared = 0;
    std::size_t total = 0;
    for (const auto& entry : first) {
        total += static_cast<std::size_t>(entry.second);
        const auto other = second.find(entry.first);
        if (other != second.end()) shared += std::min(entry.second, other->second);
    }
    for (const auto& entry : second) total += static_cast<std::size_t>(entry.second);
    return total == 0 ? 0.0 : (2.0 * static_cast<double>(shared)) / static_cast<double>(total);
}

json ocr_nearest_lines(const std::vector<OcrLine>& lines, const std::string& query,
                       std::size_t count) {
    struct Row {
        std::string text;
        double score = 0.0;
    };
    std::vector<Row> rows;
    rows.reserve(lines.size());
    for (const OcrLine& line : lines) {
        if (line.text.empty()) continue;
        rows.push_back(Row{util::utf8_take(line.text, 120), ocr_text_similarity(line.text, query)});
    }
    std::stable_sort(rows.begin(), rows.end(),
                     [](const Row& a, const Row& b) { return a.score > b.score; });
    json out = json::array();
    for (std::size_t i = 0; i < rows.size() && i < count; ++i) {
        out.push_back(json{{"text", rows[i].text}, {"similarity", rows[i].score}});
    }
    return out;
}

#ifdef _WIN32
namespace {

namespace imaging = winrt::Windows::Graphics::Imaging;
namespace media = winrt::Windows::Media::Ocr;
namespace storage = winrt::Windows::Storage;

constexpr long long kOcrWaitMilliseconds = 30'000;

void ensure_com() {
    static thread_local bool attempted = false;
    if (attempted) return;
    attempted = true;
    CoInitializeEx(nullptr, COINIT_MULTITHREADED);
}

std::wstring to_wide(const std::string& utf8) {
    return util::utf8_to_wide(utf8);
}

std::string to_utf8(const winrt::hstring& text) {
    return util::wide_to_utf8(std::wstring(text.data(), text.size()));
}

// Tamamlanma delegesi + olay: winrt::get/wait_for'un STA kilitleme denetimi
// olmadan bekler. Tamamlanma çağrısı havuz iş parçacığında çalışır, sonucu
// paylaşılan duruma yazar ve olayı çeker.
template <class T>
struct WaitState {
    HANDLE event = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    T value{nullptr};
    HRESULT code = S_OK;
    bool finished = false;
    bool cancelled = false;

    WaitState() = default;
    WaitState(const WaitState&) = delete;
    WaitState& operator=(const WaitState&) = delete;
    ~WaitState() {
        if (event) CloseHandle(event);
    }
};

template <class Operation, class T>
bool block_on(const Operation& operation, T& out, std::string& error) {
    auto state = std::make_shared<WaitState<T>>();
    if (!state->event) {
        error = "OCR bekletme olayı oluşturulamadı.";
        return false;
    }
    using winrt::Windows::Foundation::AsyncStatus;
    operation.Completed([state](Operation const& source, AsyncStatus status) {
        if (status == AsyncStatus::Completed) {
            try {
                state->value = source.GetResults();
            } catch (const winrt::hresult_error& failure) {
                state->code = failure.code();
            }
        } else {
            state->cancelled = (status == AsyncStatus::Canceled);
            state->code = E_FAIL;
        }
        state->finished = true;
        SetEvent(state->event);
    });
    const DWORD wait = WaitForSingleObject(state->event, static_cast<DWORD>(kOcrWaitMilliseconds));
    if (wait == WAIT_TIMEOUT) {
        error = "Windows OCR zaman aşımına uğradı; görüntü çok büyük ya da OCR servisi yanıt vermiyor.";
        return false;
    }
    if (wait != WAIT_OBJECT_0 || !state->finished) {
        error = "Windows OCR beklemesi yarım kaldı.";
        return false;
    }
    if (state->cancelled) {
        error = "OCR tanımı iptal edildi.";
        return false;
    }
    if (FAILED(state->code)) {
        error = to_utf8(winrt::hresult_error(state->code).message());
        return false;
    }
    out = std::move(state->value);
    return true;
}

struct EngineEntry {
    media::OcrEngine engine{nullptr};
    std::string tag;
};

// Python _ENGINE_CACHE karşılığı, ama iş parçacığı ölçeğinde: motor başka bir
// daireden gelen çağrıda kullanılamaz hale gelebildiği için paylaşım yapılmıyor.
std::map<std::string, EngineEntry>& engine_cache() {
    static thread_local std::map<std::string, EngineEntry> cache;
    return cache;
}

// Python ScreenOCR._create_engine: istenen dil, sonra tr-TR ve en-US denenir;
// hiçbiri yoksa profil dillerine düşülür.
EngineEntry create_engine(const std::string& language) {
    std::vector<std::string> candidates;
    const std::string requested = ocr_language_tag(language);
    if (!requested.empty()) candidates.push_back(requested);
    candidates.push_back("tr-TR");
    candidates.push_back("en-US");

    std::set<std::string> tried;
    for (const std::string& tag : candidates) {
        if (tag.empty() || !tried.insert(tag).second) continue;
        try {
            const winrt::Windows::Globalization::Language locale{to_wide(tag)};
            const media::OcrEngine engine = media::OcrEngine::TryCreateFromLanguage(locale);
            if (engine) return EngineEntry{engine, tag};
        } catch (const winrt::hresult_error&) {
            // Dil yüklenmemiş ya da etiket geçersiz: sıradaki adaya geç.
        }
    }
    try {
        const media::OcrEngine engine = media::OcrEngine::TryCreateFromUserProfileLanguages();
        if (engine) {
            std::string tag;
            try {
                tag = to_utf8(engine.RecognizerLanguage().LanguageTag());
            } catch (const winrt::hresult_error&) {
                tag.clear();
            }
            return EngineEntry{engine, tag};
        }
    } catch (const winrt::hresult_error&) {
        // Profil dilleri de okunamadı; aşağıda dürüst hata verilir.
    }
    throw ai::ActionForbidden(
        "Windows OCR motoru bulunamadı; Windows dil paketlerinden Türkçe veya İngilizce OCR ekleyin.");
}

std::string cache_key(const std::string& language) {
    const std::string requested = ocr_language_tag(language);
    return requested.empty() ? "auto" : requested;
}

EngineEntry engine_for(const std::string& language, bool refresh) {
    const std::string key = cache_key(language);
    if (!refresh) {
        const auto found = engine_cache().find(key);
        if (found != engine_cache().end()) return found->second;
    }
    EngineEntry entry = create_engine(language);
    engine_cache()[key] = entry;
    return entry;
}

imaging::SoftwareBitmap decode_bitmap(const std::filesystem::path& path, std::string& error) {
    imaging::SoftwareBitmap bitmap{nullptr};
    storage::StorageFile file{nullptr};
    if (!block_on(storage::StorageFile::GetFileFromPathAsync(path.native()), file, error)) {
        error = "OCR dosyası açılamadı: " + path.string() + " (" + error + ")";
        return bitmap;
    }
    storage::Streams::IRandomAccessStream stream{nullptr};
    if (!block_on(file.OpenAsync(storage::FileAccessMode::Read), stream, error)) {
        error = "OCR dosyası okunamadı: " + path.string() + " (" + error + ")";
        return bitmap;
    }
    imaging::BitmapDecoder decoder{nullptr};
    if (!block_on(imaging::BitmapDecoder::CreateAsync(stream), decoder, error)) {
        error = "Görsel OCR için çözülemedi: " + error;
        stream.Close();
        return bitmap;
    }
    // OcrEngine yalnızca Bgra8 kabul eder; PNG/JPEG kaynaklı biçim farkını burada gideririz.
    if (!block_on(decoder.GetSoftwareBitmapAsync(imaging::BitmapPixelFormat::Bgra8,
                                                 imaging::BitmapAlphaMode::Premultiplied),
                  bitmap, error)) {
        error = "Görsel piksel tamponuna dönüştürülemedi: " + error;
        stream.Close();
        return bitmap;
    }
    stream.Close();
    return bitmap;
}

std::vector<OcrLine> collect_lines(const media::OcrResult& result) {
    std::vector<OcrLine> lines;
    for (const media::OcrLine& source_line : result.Lines()) {
        OcrLine line;
        line.text = util::trim(to_utf8(source_line.Text()));
        for (const media::OcrWord& source_word : source_line.Words()) {
            const std::string text = util::trim(to_utf8(source_word.Text()));
            if (text.empty()) continue;
            const winrt::Windows::Foundation::Rect rect = source_word.BoundingRect();
            line.words.push_back(OcrWord{text, rect.X, rect.Y, rect.Width, rect.Height});
        }
        if (!line.words.empty()) lines.push_back(std::move(line));
    }
    return lines;
}

}  // namespace

bool ocr_engine_available() {
    ensure_com();
    try {
        // Boş dil: tr-TR → en-US → profil dilleri sırası denenir; any engine = available.
        return static_cast<bool>(engine_for(std::string{}, false).engine);
    } catch (const ai::ActionForbidden&) {
        return false;
    } catch (const winrt::hresult_error&) {
        return false;
    }
}

std::pair<std::vector<OcrLine>, std::string> ocr_recognize_file(
    const std::filesystem::path& path, const std::string& language) {
    ensure_com();
    std::string error;
    const imaging::SoftwareBitmap bitmap = decode_bitmap(path, error);
    if (!bitmap) throw ai::ActionForbidden("Windows OCR çalıştırılamadı: " + error);

    media::OcrResult result{nullptr};
    EngineEntry entry = engine_for(language, false);
    std::string failure = "OCR tanımı sonuç döndürmedi.";
    if (!block_on(entry.engine.RecognizeAsync(bitmap), result, failure) || !result) {
        // Python'daki refresh=True yolu: motor başka bir iş parçacığında
        // kurulduysa tanım kullanılamaz hale gelebiliyor; önbelleği tazeleyip
        // bir kez daha deniyoruz. thread_local önbellek bunu normalde engeller.
        entry = engine_for(language, true);
        failure = "OCR tanımı sonuç döndürmedi.";
        if (!block_on(entry.engine.RecognizeAsync(bitmap), result, failure) || !result) {
            throw ai::ActionForbidden("Windows OCR çalıştırılamadı: " + failure);
        }
    }
    return {collect_lines(result), entry.tag};
}

#else  // !_WIN32

bool ocr_engine_available() { return false; }

std::pair<std::vector<OcrLine>, std::string> ocr_recognize_file(
    const std::filesystem::path&, const std::string&) {
    throw ai::ActionForbidden("Windows OCR yalnızca Windows'ta bağlı.");
}

#endif  // _WIN32

// ------------------------------------------------------------------ ScreenOcr
namespace {

// Python'a birebir uyan satır satırı: metin, görüntü-piksel kutusu ve gerçek
// ekran merkezi (origin + ölçek).
struct LineRow {
    std::string text;
    std::array<long long, 4> box{};
    long long center_x = 0;
    long long center_y = 0;

    json to_json() const {
        return json{{"text", text},
                    {"box", json::array({box[0], box[1], box[2], box[3]})},
                    {"center_x", center_x},
                    {"center_y", center_y}};
    }
};

json match_to_json(const OcrMatch& match, long long center_x, long long center_y) {
    return json{{"text", match.text},
                {"line", match.line},
                {"box", json::array({match.box[0], match.box[1], match.box[2], match.box[3]})},
                {"score", match.score},
                {"center_x", center_x},
                {"center_y", center_y}};
}

}  // namespace

json ScreenOcr::read_lines(const std::filesystem::path& image, long long origin_x,
                           long long origin_y, const std::string& language, long long limit,
                           double scale) {
    const auto [lines, language_tag] = ocr_recognize_file(image, language);
    std::vector<LineRow> rows;
    rows.reserve(lines.size());
    for (const OcrLine& line : lines) {
        const std::string text = util::utf8_take(line.text, 200);
        if (text.empty() || line.words.empty()) continue;
        // Python gibi önce ham çiftlerde ölçek uygulanır, sonra piksele yuvarlanır.
        const std::array<double, 4> extent = words_extent(line.words, 0, line.words.size());
        LineRow row;
        row.text = text;
        row.box = {round_pixel(extent[0] * scale), round_pixel(extent[1] * scale),
                   round_pixel(extent[2] * scale), round_pixel(extent[3] * scale)};
        row.center_x = origin_x + round_pixel((extent[0] + extent[2] / 2.0) * scale);
        row.center_y = origin_y + round_pixel((extent[1] + extent[3] / 2.0) * scale);
        rows.push_back(std::move(row));
    }
    // Yukarıdan aşağı, soldan sağa — Python'ın kararlı sıralaması.
    std::stable_sort(rows.begin(), rows.end(), [](const LineRow& a, const LineRow& b) {
        if (a.center_y != b.center_y) return a.center_y < b.center_y;
        return a.center_x < b.center_x;
    });
    const std::size_t keep = static_cast<std::size_t>(std::max<long long>(1, limit));
    json payload;
    payload["language"] = language_tag;
    payload["count"] = static_cast<long long>(rows.size());
    payload["lines"] = json::array();
    for (std::size_t i = 0; i < rows.size() && i < keep; ++i) payload["lines"].push_back(rows[i].to_json());
    payload["truncated"] = rows.size() > keep;
    payload["note"] =
        "Metin satırları gerçek ekran koordinatlarıyla listelendi; tıklama için uygun "
        "center_x/center_y kullan.";
    return payload;
}

json ScreenOcr::find(const std::filesystem::path& image, const std::string& query,
                     long long origin_x, long long origin_y, const std::string& language) {
    const std::string wanted = util::trim(query);
    if (wanted.empty()) throw ai::ActionForbidden("Aranacak ekran metni boş olamaz.");
    const auto [lines, language_tag] = ocr_recognize_file(image, language);
    const std::vector<OcrMatch> matches = match_ocr_lines(lines, wanted);

    json rows = json::array();
    json best = nullptr;
    for (std::size_t i = 0; i < matches.size(); ++i) {
        const OcrMatch& match = matches[i];
        const std::array<long long, 2> center = ocr_screen_center(match.box, origin_x, origin_y);
        if (i >= 10) continue;  // matches[:10] — model yalnızca ilk 10unu görür
        rows.push_back(match_to_json(match, center[0], center[1]));
        if (best.is_null()) best = rows.back();
    }

    std::string note =
        "OCR kelime kutuları gerçek ekran koordinatına çevrildi. Tıklamadan önce hedefin doğru "
        "kelime olduğunu metinden doğrula; tıklama sonrası capture_screen ile sonucu denetle.";
    json nearest = nullptr;
    if (best.is_null()) {
        // Python'da olmayan ek: model aynı turda kendini düzeltsin diye OCR'daki
        // en benzer üç satır da döner.
        nearest = ocr_nearest_lines(lines, wanted, 3);
        note = "Bu metin OCR sonucunda bulunamadı; farklı bir kelime dene veya capture_screen ile "
               "görüntüyü incele.";
        if (!nearest.empty()) note += " OCR'nin en çok benzettiği satırlar nearest içinde.";
    }
    json payload;
    payload["query"] = wanted;
    payload["language"] = language_tag;
    payload["matches"] = rows;
    payload["best"] = best;
    payload["nearest"] = nearest;
    payload["note"] = note;
    return payload;
}

}  // namespace jarvis::tools
