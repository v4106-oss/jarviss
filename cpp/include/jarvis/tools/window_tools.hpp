#pragma once

// WindowTools — Python app/core/window_tools.py karşılığı.
//
// Yalnızca Win32 kullanır (Qt, pywinauto, ağ yok): EnumWindows ile pencereleri
// toplar, handle/başlık ile çözer, öne getirir, küçültür/büyütür/geri yükler,
// taşır, boyutlandırır ve WM_CLOSE ile nazikçe kapatır. Zorla sonlandırma
// (TerminateProcess) yoktur; kapanmayan pencere "kapanmadı, onay istiyor
// olabilir" diye dürüst bildirilir.
//
// Python'dan tek bilinçli daraltma: pencere listesi *görünür* ve başlıklı
// pencerelerle sınırlıdır — gizli pencerelerin handle'ları modele verilmez.

#include <nlohmann/json.hpp>

#include <optional>
#include <string>
#include <variant>
#include <vector>

namespace jarvis::tools {

// --- Python modül sabitleri (birebir) ---------------------------------------
inline constexpr std::size_t kMaxWindows = 100;
inline constexpr long long kMaxCoordinate = 30000;
inline constexpr long long kMinWindowSize = 120;
inline constexpr long long kMaxWindowSize = 20000;
inline constexpr double kCloseWaitSeconds = 2.5;

// --- pencere satırı (Python _describe sözlüğü) ------------------------------
struct WindowInfo {
    long long handle = 0;
    std::string title;
    std::string process;  // küçük harfli exe adı; okunamıyor boş
    std::string window_class;  // Win32 sınıf adı; yalnızca süzme için (JSON'a yazılmaz)
    long long process_id = 0;
    std::vector<long long> rect;  // [left, top, right, bottom]; geçersizse boş
    bool minimized = false;
    bool maximized = false;
    bool foreground = false;

    nlohmann::json to_json() const;
};

// normalize_text (app/core/screen_agent.py) karşılığı: İ/I/ı → i, aksanları
// katlar, küçük harfe indirir, ardışık boşlukları tekilleştirir.
std::string normalize_text(const std::string& value);

// UTF-8 metni kod noktalarına çözer (geçersiz bayt U+FFFD olur). Uzunluk ve
// difflib benzerliği bayt değil kod noktası üzerinden sayılır.
std::vector<unsigned int> code_points(const std::string& value);

// secure_desktop_reason karşılığı: UAC/güvenli masaüstü engeli varsa nedeni.
std::optional<std::string> secure_desktop_reason(const std::string& title,
                                                 const std::string& process);

// ACTION_ALIASES tablosu; bilinmeyen eylemde nullopt.
std::optional<std::string> resolve_action(const std::string& raw);

// CRITICAL_PROCESSES kümesi (Python listesi aynen alındı).
bool is_critical_process(const std::string& process);

// Python _ensure_safe: işlem güvenli mi; engel varsa Türkçe neden.
std::optional<std::string> safety_reason(const std::string& title, const std::string& process,
                                        const std::string& action, long long process_id);

// Python _int_arg: alan yoksa/tam sayı değilse/aralık dışındaysa hata metni,
// değilse sayı. (Aralık dışı değer kırpılmaz — Python da reddediyor.)
std::variant<long long, std::string> checked_int(const nlohmann::json& args, const std::string& key,
                                                long long minimum, long long maximum);

// WindowsUI._window eşleşmesi: önce casefold tam, olmazsa casefold alt dizi.
// Eşleşme yoksa/birden çoksa Türkçe hata metni, tekse satır indeksi.
std::variant<std::size_t, std::string> select_title_match(const std::vector<std::string>& titles,
                                                         const std::string& wanted);

// Canlı masaüstünden görünür + başlıklı pencereleri toplar (EnumWindows).
std::vector<WindowInfo> collect_windows();

// Handle'ı olan pencereyi öne getirir (WindowsUI.focus) ve gerçekten etkin
// olduğunu geri okuyarak doğrular. Uygulama başlatıcı da aynı yolu kullanır.
bool focus_window_handle(long long handle);

// list_windows/run — yürütücünün çağırdığı iki giriş noktası. Hata durumunda
// jarvis::ai::ActionForbidden fırlatır (Python ActionForbidden'ın karşılığı).
class WindowTools {
public:
    std::string run(const nlohmann::json& args);
    std::string list_windows(const nlohmann::json& args);
};

}  // namespace jarvis::tools
