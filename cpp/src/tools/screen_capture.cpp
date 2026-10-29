#include "jarvis/tools/screen_capture.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <map>
#include <string>
#include <thread>
#include <vector>

#include "jarvis/ai/safety.hpp"
#include "jarvis/core/util.hpp"
#include "jarvis/core/win_path.hpp"
#include "jarvis/tools/ocr.hpp"
#include "jarvis/tools/window_tools.hpp"

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <wincodec.h>
#include <ole2.h>
#endif

using nlohmann::json;

namespace jarvis::tools {
namespace {

constexpr std::size_t kMaxPreviewSide = 1200;
constexpr long long kMaxImageBytes = 25'000'000;

std::string ascii_lower(std::string value) {
    for (char& c : value) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return value;
}

std::string fixed3(double value) {
    char buffer[32];
    std::snprintf(buffer, sizeof(buffer), "%.3f", value);
    return buffer;
}

std::string arg_str(const json& args, const char* key, const std::string& fallback = "") {
    if (!args.is_object() || !args.contains(key) || args[key].is_null()) return fallback;
    const json& value = args[key];
    if (value.is_string()) return value.get<std::string>();
    if (value.is_number_integer()) return std::to_string(value.get<long long>());
    if (value.is_number_unsigned()) return std::to_string(value.get<unsigned long long>());
    return fallback;
}

bool arg_flag(const json& args, const char* key) {
    if (!args.is_object() || !args.contains(key) || args[key].is_null()) return false;
    const json& value = args[key];
    if (value.is_boolean()) return value.get<bool>();
    if (value.is_string()) {
        const std::string text = ascii_lower(util::trim(value.get<std::string>()));
        return text == "true" || text == "1" || text == "evet" || text == "yes";
    }
    if (value.is_number()) return value.get<double>() != 0.0;
    return false;
}

long long timestamp_ns() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
               std::chrono::system_clock::now().time_since_epoch())
        .count();
}

std::filesystem::path local_app_data() {
#ifdef _WIN32
    if (const char* local = std::getenv("LOCALAPPDATA"); local && *local) {
        return std::filesystem::path(local);
    }
    if (const char* profile = std::getenv("USERPROFILE"); profile && *profile) {
        return std::filesystem::path(profile) / "AppData" / "Local";
    }
#endif
    std::error_code ec;
    const std::filesystem::path temp = std::filesystem::temp_directory_path(ec);
    return ec ? std::filesystem::path(".") : temp;
}

#ifdef _WIN32

// WIC arayüzleri için küçük bir sahiplik kabuğu: erken dönüşlerde sızıntı olmasın.
template <class T>
class ComPtr {
public:
    ComPtr() = default;
    ComPtr(const ComPtr&) = delete;
    ComPtr& operator=(const ComPtr&) = delete;
    ~ComPtr() {
        if (object_) object_->Release();
    }
    T** put() { return &object_; }
    T* get() const { return object_; }
    T* operator->() const { return object_; }
    template <class U>
    HRESULT as(ComPtr<U>* out) const {
        if (!object_) return E_POINTER;
        return object_->QueryInterface(__uuidof(U), reinterpret_cast<void**>(out->put()));
    }

private:
    T* object_ = nullptr;
};

// COM iş parçacığı başlatması: WIC de WinRT de daire ister. UIAutomation kendi
// başlatmasını kendi yönetir; burada yinelenen çağrı S_FALSE döner ve zararsızdır.
// Daire bilinçli olarak kapatılmaz — iş parçacığı sonlanırken Windows toplar.
void ensure_com() {
    static thread_local bool attempted = false;
    if (attempted) return;
    attempted = true;
    CoInitializeEx(nullptr, COINIT_MULTITHREADED);
}

// SDK başlığı makinesine göre DPI_AWARENESS_CONTEXT'u farklı bildirir; çağrı
// dinamik çözüldüğü için void* yeter. PER_MONITOR_AWARE_V2 = (void*)-4.
using DpiAwarenessContext = void*;
using SetProcessDpiAwarenessContextFn = BOOL(WINAPI*)(DpiAwarenessContext);

void apply_dpi_awareness() {
    // SetProcessDpiAwarenessContext Windows 10 1607+; olmayan makinede sessizce
    // SetProcessDPIAware'a (sistem ölçeği) düşülür. Başarısızlık yutulur: Qt
    // daire farkındalığını zaten kurmuş olabilir ve Windows bunu değiştirmez.
    const HMODULE user32 = GetModuleHandleW(L"user32.dll");
    if (user32) {
        const auto procedure = reinterpret_cast<SetProcessDpiAwarenessContextFn>(
            GetProcAddress(user32, "SetProcessDpiAwarenessContext"));
        if (procedure && procedure(reinterpret_cast<DpiAwarenessContext>(-4))) return;
    }
    SetProcessDPIAware();
}

bool grab_win32(const std::optional<std::array<long long, 4>>& region, PixelBuffer& out,
                std::string& error) {
    long long left = 0;
    long long top = 0;
    long long width = 0;
    long long height = 0;
    const std::array<long long, 4> desktop = virtual_desktop_rect();
    if (region) {
        left = (*region)[0];
        top = (*region)[1];
        width = (*region)[2];
        height = (*region)[3];
        // Ekran dışına taşan kenarlar kırpılır; hiç ortak alan yoksa başarısız.
        const long long shared_left = std::max(left, desktop[0]);
        const long long shared_top = std::max(top, desktop[1]);
        const long long shared_right = std::min(left + width, desktop[0] + desktop[2]);
        const long long shared_bottom = std::min(top + height, desktop[1] + desktop[3]);
        if (shared_right <= shared_left || shared_bottom <= shared_top) {
            error = "Pencere ekranın görünür alanında değil; görüntü alınamadı.";
            return false;
        }
        left = shared_left;
        top = shared_top;
        width = shared_right - shared_left;
        height = shared_bottom - shared_top;
    } else {
        left = desktop[0];
        top = desktop[1];
        width = desktop[2];
        height = desktop[3];
    }
    if (width <= 0 || height <= 0) {
        error = "Sanal masaüstü ölçüsü alınamadı.";
        return false;
    }

    const HDC screen = GetDC(nullptr);
    const HDC memory = CreateCompatibleDC(screen);
    BITMAPINFO info{};
    info.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    info.bmiHeader.biWidth = static_cast<LONG>(width);
    info.bmiHeader.biHeight = -static_cast<LONG>(height);  // üstten alta satır sırası
    info.bmiHeader.biPlanes = 1;
    info.bmiHeader.biBitCount = 32;
    info.bmiHeader.biCompression = BI_RGB;
    void* bits = nullptr;
    const HBITMAP bitmap = CreateDIBSection(screen, &info, DIB_RGB_COLORS, &bits, nullptr, 0);
    if (!bitmap || !bits) {
        error = "Ekran tamponu (CreateDIBSection) oluşturulamadı.";
        if (bitmap) DeleteObject(bitmap);
        DeleteDC(memory);
        ReleaseDC(nullptr, screen);
        return false;
    }
    const HGDIOBJ previous = SelectObject(memory, bitmap);
    const BOOL copied = BitBlt(memory, 0, 0, static_cast<INT>(width), static_cast<INT>(height),
                               screen, static_cast<INT>(left), static_cast<INT>(top), SRCCOPY);
    SelectObject(memory, previous);
    const std::size_t bytes = static_cast<std::size_t>(width) * static_cast<std::size_t>(height) * 4;
    out.width = width;
    out.height = height;
    out.origin_x = left;
    out.origin_y = top;
    out.bgra.assign(static_cast<unsigned char*>(bits),
                    static_cast<unsigned char*>(bits) + bytes);
    DeleteObject(bitmap);
    DeleteDC(memory);
    ReleaseDC(nullptr, screen);
    if (!copied) {
        error = "BitBlt başarısız oldu.";
        return false;
    }
    for (std::size_t i = 3; i < out.bgra.size(); i += 4) out.bgra[i] = 0xFF;
    return true;
}

bool encode_win32(const PixelBuffer& data, const std::filesystem::path& path, std::string& error) {
    ensure_com();
    if (data.width <= 0 || data.height <= 0 || data.bgra.size() < static_cast<std::size_t>(data.stride()) * data.height) {
        error = "PNG için piksel tamponu geçersiz.";
        return false;
    }
    ComPtr<IWICImagingFactory> factory;
    if (FAILED(CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER,
                                IID_PPV_ARGS(factory.put())))) {
        error = "Windows Imaging Component başlatılamadı.";
        return false;
    }
    ComPtr<IWICStream> stream;
    if (FAILED(factory->CreateStream(stream.put())) ||
        FAILED(stream->InitializeFromFilename(path.native().c_str(), GENERIC_WRITE))) {
        error = "Görüntü dosyası açılamadı: " + path.string();
        return false;
    }
    ComPtr<IWICBitmapEncoder> encoder;
    if (FAILED(factory->CreateEncoder(GUID_ContainerFormatPng, nullptr, encoder.put())) ||
        FAILED(encoder->Initialize(stream.get(), WICBitmapEncoderNoCache))) {
        error = "PNG kodlayıcı hazırlanamadı.";
        return false;
    }
    ComPtr<IWICBitmap> bitmap;
    if (FAILED(factory->CreateBitmap(static_cast<UINT>(data.width), static_cast<UINT>(data.height),
                                     GUID_WICPixelFormat32bppBGRA, WICBitmapCacheOnLoad,
                                     bitmap.put()))) {
        error = "WIC bit mapinesi oluşturulamadı.";
        return false;
    }
    // WIC bu sürümde IWICBitmapLock::Unlock taşımıyor: kilidi bırakmak
    // (ComPtr kapsamı sonu) bitmap'i de serbest bırakır.
    {
        const WICRect area{0, 0, static_cast<INT>(data.width), static_cast<INT>(data.height)};
        ComPtr<IWICBitmapLock> lock;
        if (FAILED(bitmap->Lock(&area, WICBitmapLockWrite, lock.put()))) {
            error = "Bitmap kilidi alınamadı.";
            return false;
        }
        UINT locked_stride = 0;
        UINT byte_count = 0;
        unsigned char* mapped = nullptr;
        const std::size_t row_bytes = static_cast<std::size_t>(data.width) * 4;
        if (FAILED(lock->GetStride(&locked_stride)) ||
            FAILED(lock->GetDataPointer(&byte_count, &mapped)) || !mapped) {
            error = "Bitmap belleği kilitten alınamadı.";
            return false;
        }
        if (locked_stride < row_bytes ||
            static_cast<std::size_t>(locked_stride) * static_cast<std::size_t>(data.height) >
                static_cast<std::size_t>(byte_count)) {
            error = "Bitmap satır adımı görüntüye sığmıyor.";
            return false;
        }
        for (long long row = 0; row < data.height; ++row) {
            std::memcpy(mapped + static_cast<std::size_t>(row) * locked_stride,
                        data.bgra.data() + static_cast<std::size_t>(row) * row_bytes, row_bytes);
        }
    }

    ComPtr<IWICBitmapFrameEncode> frame;
    if (FAILED(encoder->CreateNewFrame(frame.put(), nullptr)) || FAILED(frame->Initialize(nullptr)) ||
        FAILED(frame->SetSize(static_cast<UINT>(data.width), static_cast<UINT>(data.height)))) {
        error = "PNG karesi hazırlanamadı.";
        return false;
    }
    ComPtr<IWICBitmapSource> source;
    if (FAILED(bitmap.as<IWICBitmapSource>(&source))) {
        error = "Kaynak görüntü arayüzü alınamadı.";
        return false;
    }
    if (FAILED(frame->WriteSource(source.get(), nullptr)) || FAILED(frame->Commit()) ||
        FAILED(encoder->Commit())) {
        error = "PNG kodlama başarısız.";
        return false;
    }
    return true;
}

bool decode_win32(const std::filesystem::path& path, PixelBuffer& out, std::string& error) {
    ensure_com();
    ComPtr<IWICImagingFactory> factory;
    if (FAILED(CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER,
                                IID_PPV_ARGS(factory.put())))) {
        error = "Windows Imaging Component başlatılamadı.";
        return false;
    }
    ComPtr<IWICBitmapDecoder> decoder;
    if (FAILED(factory->CreateDecoderFromFilename(path.native().c_str(), nullptr, GENERIC_READ,
                                                  WICDecodeMetadataCacheOnLoad, decoder.put()))) {
        error = "Görsel açılamadı: " + path.string();
        return false;
    }
    ComPtr<IWICBitmapFrameDecode> frame;
    if (FAILED(decoder->GetFrame(0, frame.put()))) {
        error = "Görsel karesi okunamadı: " + path.string();
        return false;
    }
    ComPtr<IWICFormatConverter> converter;
    if (FAILED(factory->CreateFormatConverter(converter.put())) ||
        FAILED(converter->Initialize(frame.get(), GUID_WICPixelFormat32bppBGRA,
                                     WICBitmapDitherTypeNone, nullptr, 0.0,
                                     WICBitmapPaletteTypeCustom))) {
        error = "Görsel 32 bit BGRA'ya çevrilemedi.";
        return false;
    }
    UINT width = 0;
    UINT height = 0;
    if (FAILED(converter->GetSize(&width, &height)) || width == 0 || height == 0) {
        error = "Görsel boyutu okunamadı.";
        return false;
    }
    const std::size_t stride = static_cast<std::size_t>(width) * 4;
    out.width = static_cast<long long>(width);
    out.height = static_cast<long long>(height);
    out.origin_x = 0;
    out.origin_y = 0;
    out.bgra.assign(stride * height, 0);
    if (FAILED(converter->CopyPixels(nullptr, static_cast<LONG>(stride),
                                     static_cast<UINT>(out.bgra.size()), out.bgra.data()))) {
        error = "Görsel pikselleri okunamadı.";
        return false;
    }
    return true;
}

#else  // !_WIN32

bool grab_win32(const std::optional<std::array<long long, 4>>&, PixelBuffer&, std::string& error) {
    error = "Ekran yakalama yalnızca Windows'ta bağlı.";
    return false;
}

bool encode_win32(const PixelBuffer&, const std::filesystem::path&, std::string& error) {
    error = "PNG kodlama yalnızca Windows'ta bağlı.";
    return false;
}

bool decode_win32(const std::filesystem::path&, PixelBuffer&, std::string& error) {
    error = "Görsel çözme yalnızca Windows'ta bağlı.";
    return false;
}

#endif  // _WIN32

bool path_exists(const std::filesystem::path& path) {
    std::error_code ec;
    return std::filesystem::exists(path, ec);
}

std::vector<std::filesystem::path> list_pattern(const std::filesystem::path& directory,
                                                const std::string& prefix) {
    std::vector<std::filesystem::path> found;
    std::error_code ec;
    if (!directory.is_absolute() || !std::filesystem::is_directory(directory, ec)) return found;
    for (std::filesystem::directory_iterator it(directory, ec), end; it != end; it.increment(ec)) {
        const std::string name = it->path().filename().string();
        if (name.rfind(prefix, 0) == 0 && it->is_regular_file(ec)) found.push_back(it->path());
    }
    std::sort(found.begin(), found.end(),
              [](const std::filesystem::path& a, const std::filesystem::path& b) {
                  return a.filename().string() < b.filename().string();
              });
    return found;
}

void keep_newest(const std::vector<std::filesystem::path>& sorted, std::size_t keep) {
    if (sorted.size() <= keep) return;
    std::error_code ec;
    for (std::size_t i = 0; i + keep < sorted.size(); ++i) std::filesystem::remove(sorted[i], ec);
}

std::filesystem::path unique_cache_path(const std::string& prefix, const std::string& suffix) {
    return screen_cache_directory() /
           std::filesystem::path(prefix + std::to_string(timestamp_ns()) + suffix);
}

// Python round(x, 3) — yarım değerlerde yuvarlama farkı testleri etkilemez.
double round3(double value) {
    const double scaled = value * 1000.0;
    const double nearest = scaled >= 0.0 ? std::floor(scaled + 0.5) : std::ceil(scaled - 0.5);
    return nearest / 1000.0;
}

}  // namespace

// --------------------------------------------------------------- süreç / ölçüm

void make_process_dpi_aware() {
#ifdef _WIN32
    static bool done = false;
    if (done) return;
    done = true;
    apply_dpi_awareness();
#endif
}

std::array<long long, 4> virtual_desktop_rect() {
#ifdef _WIN32
    return {GetSystemMetrics(SM_XVIRTUALSCREEN), GetSystemMetrics(SM_YVIRTUALSCREEN),
            GetSystemMetrics(SM_CXVIRTUALSCREEN), GetSystemMetrics(SM_CYVIRTUALSCREEN)};
#else
    return {0, 0, 0, 0};
#endif
}

std::filesystem::path screen_cache_directory() {
    const std::filesystem::path directory =
        local_app_data() / "Jarvis" / "cache" / "screen";
    std::error_code ec;
    if (!path_exists(directory)) std::filesystem::create_directories(directory, ec);
    return directory;
}

void prune_cache(const std::filesystem::path& directory, std::size_t keep) {
    keep_newest(list_pattern(directory, "screen_"), keep);
    keep_newest(list_pattern(directory, "inspect_"), keep / 2);
}

bool grab_screen(const std::optional<std::array<long long, 4>>& region, PixelBuffer& out,
                std::string& error) {
    make_process_dpi_aware();
    return grab_win32(region, out, error);
}

bool write_png(const PixelBuffer& data, const std::filesystem::path& path, std::string& error) {
    return encode_win32(data, path, error);
}

bool read_image(const std::filesystem::path& path, PixelBuffer& out, std::string& error) {
    return decode_win32(path, out, error);
}

PixelBuffer shrink_image(const PixelBuffer& data, long long max_side) {
    PixelBuffer result;
    if (data.width <= 0 || data.height <= 0 || data.bgra.empty()) return result;
    const long long longest = std::max(data.width, data.height);
    const long long side = std::max<long long>(1, max_side);
    long long width = data.width;
    long long height = data.height;
    if (longest > side) {
        width = std::max<long long>(1, (data.width * side + longest / 2) / longest);
        height = std::max<long long>(1, (data.height * side + longest / 2) / longest);
    }
    result.width = width;
    result.height = height;
    result.origin_x = data.origin_x;
    result.origin_y = data.origin_y;
    result.bgra.assign(static_cast<std::size_t>(width) * static_cast<std::size_t>(height) * 4, 0);
    for (long long row = 0; row < height; ++row) {
        const long long source_row = row * data.height / height;
        for (long long column = 0; column < width; ++column) {
            const long long source_column = column * data.width / width;
            const std::size_t from =
                (static_cast<std::size_t>(source_row) * static_cast<std::size_t>(data.width) +
                 static_cast<std::size_t>(source_column)) *
                4;
            const std::size_t to =
                (static_cast<std::size_t>(row) * static_cast<std::size_t>(width) +
                 static_cast<std::size_t>(column)) *
                4;
            for (std::size_t channel = 0; channel < 4; ++channel) {
                result.bgra[to + channel] = data.bgra[from + channel];
            }
        }
    }
    return result;
}

ImageReport analyze_image(const PixelBuffer& data, long long sample_side) {
    ImageReport report;
    report.width = data.width;
    report.height = data.height;
    if (data.width <= 0 || data.height <= 0 || data.bgra.empty()) return report;
    const PixelBuffer sample = shrink_image(data, sample_side);
    report.sample_width = sample.width;
    report.sample_height = sample.height;
    const std::size_t total = static_cast<std::size_t>(sample.width) * static_cast<std::size_t>(sample.height);
    if (total == 0) return report;

    std::vector<std::pair<std::array<long long, 3>, std::size_t>> buckets;  // ilk görünüm sırası korunur
    std::array<std::array<std::size_t, 3>, 3> grid_counts{};
    std::array<std::array<std::size_t, 3>, 3> grid_sizes{};
    long long min_x = sample.width;
    long long min_y = sample.height;
    long long max_x = -1;
    long long max_y = -1;
    std::size_t non_white = 0;

    for (std::size_t index = 0; index < total; ++index) {
        const std::size_t offset = index * 4;
        const long long blue = sample.bgra[offset];
        const long long green = sample.bgra[offset + 1];
        const long long red = sample.bgra[offset + 2];
        const std::array<long long, 3> color{red / 32 * 32, green / 32 * 32, blue / 32 * 32};
        const long long column_position = static_cast<long long>(index % static_cast<std::size_t>(sample.width));
        const long long row_position = static_cast<long long>(index / static_cast<std::size_t>(sample.width));
        const std::size_t column =
            static_cast<std::size_t>(std::min<long long>(2, column_position * 3 / sample.width));
        const std::size_t row =
            static_cast<std::size_t>(std::min<long long>(2, row_position * 3 / sample.height));
        grid_sizes[row][column] += 1;

        auto found = std::find_if(buckets.begin(), buckets.end(),
                                 [&color](const auto& entry) { return entry.first == color; });
        if (found == buckets.end())
            buckets.emplace_back(color, 1);
        else
            ++found->second;

        if (red >= 245 && green >= 245 && blue >= 245) continue;
        non_white += 1;
        grid_counts[row][column] += 1;
        min_x = std::min(min_x, column_position);
        min_y = std::min(min_y, row_position);
        max_x = std::max(max_x, column_position);
        max_y = std::max(max_y, row_position);
    }

    std::stable_sort(buckets.begin(), buckets.end(),
                     [](const auto& a, const auto& b) { return a.second > b.second; });
    for (std::size_t i = 0; i < buckets.size() && i < 5; ++i) {
        report.dominant.push_back(
            ColorShare{buckets[i].first, round3(static_cast<double>(buckets[i].second) / total)});
    }
    if (max_x >= 0 && data.width > 0 && data.height > 0) {
        const double scale_x = static_cast<double>(data.width) / static_cast<double>(sample.width);
        const double scale_y = static_cast<double>(data.height) / static_cast<double>(sample.height);
        report.content_bbox = std::array<long long, 4>{
            static_cast<long long>(std::llround(min_x * scale_x)),
            static_cast<long long>(std::llround(min_y * scale_y)),
            static_cast<long long>(std::llround((max_x + 1) * scale_x)),
            static_cast<long long>(std::llround((max_y + 1) * scale_y))};
    }
    for (std::size_t row = 0; row < 3; ++row) {
        for (std::size_t column = 0; column < 3; ++column) {
            const std::size_t size = grid_sizes[row][column];
            report.grid[row][column] = size == 0 ? 0.0
                                                 : round3(static_cast<double>(grid_counts[row][column]) /
                                                          static_cast<double>(size));
        }
    }
    report.non_white_ratio = round3(static_cast<double>(non_white) / static_cast<double>(total));
    report.blank = report.non_white_ratio < 0.005;
    return report;
}

// ---------------------------------------------------------------- capture_screen

std::string capture_screen(const json& args, const std::function<void(const std::string&)>& sink) {
    make_process_dpi_aware();
    std::string note;
    const std::string title = util::trim(arg_str(args, "window_title"));
    if (!title.empty()) {
        try {
            const std::vector<WindowInfo> windows = collect_windows();
            std::vector<std::string> titles;
            titles.reserve(windows.size());
            for (const WindowInfo& window : windows) titles.push_back(window.title);
            const auto selection = select_title_match(titles, title);
            if (const auto* error = std::get_if<std::string>(&selection)) {
                note = "\n⚠️ Pencere öne getirilemedi: " + *error;
            } else if (!focus_window_handle(windows[std::get<std::size_t>(selection)].handle)) {
                note = "\n⚠️ Pencere öne getirilemedi: etkin pencere doğrulanamadı.";
            } else {
                std::this_thread::sleep_for(std::chrono::milliseconds(200));
            }
        } catch (const ai::ActionForbidden& error) {
            note = "\n⚠️ Pencere öne getirilemedi: ";
            note += error.what();
        }
    }

    PixelBuffer shot;
    std::string error;
    if (!grab_screen(std::nullopt, shot, error)) throw ai::ActionForbidden(error);
    const std::filesystem::path path = unique_cache_path("screen_", ".png");
    if (!write_png(shot, path, error)) {
        std::error_code ec;
        std::filesystem::remove(path, ec);
        throw ai::ActionForbidden(error);
    }
    prune_cache(path.parent_path());

    // Görüntü ölçeklenmeden yazıldığı için görüntü→ekran ölçeği daima 1.000'dir;
    // mesaj, Python'ın küçültülmüş JPEG akışındaki uyarının yerine geçer.
    std::string message = "✅ Ekran görüntüsü alındı ve sonraki model adımı için hazırlandı: " +
                          path.string() + "\nGörüntü: " + std::to_string(shot.width) + "x" +
                          std::to_string(shot.height) + "; gerçek ekran: " +
                          std::to_string(shot.width) + "x" + std::to_string(shot.height) +
                          "; görüntüden ekrana ölçek: " + fixed3(1.0) +
                          ". Ekran düzlemi başlangıcı: (" + std::to_string(shot.origin_x) + ", " +
                          std::to_string(shot.origin_y) +
                          "). Koordinatlar doğrudan gerçek ekran pikselidir.";

    if (sink) sink(path.string());

    if (arg_flag(args, "read_text")) {
        try {
            json lines = ScreenOcr::read_lines(path, shot.origin_x, shot.origin_y,
                                               arg_str(args, "language"), 60, 1.0);
            message += "\nOCR metin haritası:\n" + lines.dump();
        } catch (const std::exception& error) {
            message += std::string("\n⚠️ OCR metni okunamadı: ") + error.what();
        }
    }
    return message + note;
}

// ---------------------------------------------------------------- inspect_image

std::string inspect_image(const std::filesystem::path& path,
                          const std::function<void(const std::string&)>& sink) {
    if (!path_exists(path) || std::filesystem::is_directory(path)) {
        throw ai::ActionForbidden("Görsel bulunamadı: " + path.string());
    }
    std::error_code ec;
    const std::uintmax_t size = std::filesystem::file_size(path, ec);
    if (ec) throw ai::ActionForbidden("Görsel okunamadı: " + path.string());
    if (static_cast<long long>(size) > kMaxImageBytes) {
        throw ai::ActionForbidden("Görsel 25 MB'tan büyük; doğrulama için çok büyük.");
    }
    PixelBuffer image;
    std::string error;
    if (!read_image(path, image, error)) throw ai::ActionForbidden(error);

    ImageReport report = analyze_image(image);
    json payload{
        {"path", path.string()},
        {"size", json::array({report.width, report.height})},
        {"sample", json::array({report.sample_width, report.sample_height})},
        {"non_white_ratio", report.non_white_ratio},
        {"blank", report.blank},
        {"dominant_colors", json::array()},
        {"grid", json::array()},
    };
    for (const ColorShare& color : report.dominant) {
        payload["dominant_colors"].push_back(
            json{{"rgb", json::array({color.rgb[0], color.rgb[1], color.rgb[2]})},
                 {"ratio", color.ratio}});
    }
    for (const auto& row : report.grid) {
        json line = json::array();
        for (double value : row) line.push_back(value);
        payload["grid"].push_back(line);
    }
    if (report.content_bbox) {
        payload["content_bbox"] = json::array({(*report.content_bbox)[0], (*report.content_bbox)[1],
                                               (*report.content_bbox)[2], (*report.content_bbox)[3]});
    } else {
        payload["content_bbox"] = nullptr;
    }

    const PixelBuffer preview = shrink_image(image, static_cast<long long>(kMaxPreviewSide));
    const std::filesystem::path preview_path = unique_cache_path("inspect_", ".png");
    std::string write_error;
    if (sink && write_png(preview, preview_path, write_error)) {
        prune_cache(preview_path.parent_path());
        payload["preview"] = preview_path.string();
        sink(preview_path.string());
    } else {
        std::error_code remove_error;
        std::filesystem::remove(preview_path, remove_error);
        payload["preview"] = nullptr;
        payload["preview_note"] =
            "Önizleme yazılamadı; görsel modele gönderilemedi: " +
            (write_error.empty() ? std::string("kanalı kapalı") : write_error);
    }
    payload["note"] =
        "Görsel ölçüleri ve baskın renkler raporlandı; blank=true ise neredeyse tümüyle beyaz "
        "demektir. Çizim/ekran çıktısını doğrulamak için preview görüntüsünü incele.";
    return payload.dump();
}

}  // namespace jarvis::tools
