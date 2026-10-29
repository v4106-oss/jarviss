#pragma once

// UiAutomation — Python app/core/screen_agent.py (UIA kısmı) ve
// app/core/agent.py WindowsUI.inspect/act/keys karşılığı.
//
// Qt ve pywinauto yoktur: doğrudan IUIAutomation COM arayüzü + Win32 SendInput
// kullanılır. Çağrılar AIWorker'ın iş parçacığında yürür; COM o iş parçacığında
// COINIT_MULTITHREADED ile bir kez başlatılır ve iş parçacığı biterken
// kapatılır. IUIAutomation örneği iş parçacığına özyinelemeli önbelrektir.
//
// Eylem sırası Python ile aynı: önce UIA deseni, olmazsa SetFocus + SendInput.
// Her eylemden sonra öğe geri okunur ve yanıt delivery/retry taşır.

#include <nlohmann/json.hpp>

#include <array>
#include <optional>
#include <string>
#include <variant>
#include <vector>

namespace jarvis::tools {

// --- UIA desen adları (öğe sorgulama ve dağıtım tablosu aynı sözcükleri kullanır)
namespace pattern {
inline constexpr const char* kInvoke = "invoke";
inline constexpr const char* kToggle = "toggle";
inline constexpr const char* kSelectionItem = "selection_item";
inline constexpr const char* kValue = "value";
inline constexpr const char* kScroll = "scroll";
inline constexpr const char* kScrollItem = "scroll_item";
inline constexpr const char* kExpandCollapse = "expand_collapse";
}  // namespace pattern

// inspect_ui'nin etkileşimli saydığı denetim türleri (Python INTERACTIVE_TYPES).
bool is_interactive_type(const std::string& control_type);

// ui_control'ün tanıdığı eylemler (Python UIA_RICH_ACTIONS + click/type/focus).
bool is_ui_action(const std::string& action);

// Python _is_secure_control: IsPassword işareti ya da edit/document türünde
// parola çağrıştıran ad (parola, password, pin, gizli …).
bool is_secure_element(const std::string& name, const std::string& control_type, bool is_password);

// ui_control güvenlik geçidi: yazma/tıklama taşıyan eylemler parola alanında
// reddedilir. Engel yoksa nullopt; varsa Türkçe neden. Pencere çözümünden bağımsız
// olduğu için tek başına test edilir.
std::optional<std::string> control_block_reason(const std::string& action,
                                                const std::string& name,
                                                const std::string& control_type, bool is_password);

// --- eylem sonuçları ---------------------------------------------------------
struct ActionResult {
    std::string mechanism;
    bool verified = false;
    std::string note;
};

struct ControlOutcome {
    std::string mechanism;
    bool verified = false;
    std::string note;
    std::string delivery;  // delivered | delivered_unverified
    std::string retry;     // safe | unsafe
};

// Fare/klavye girişi kenarı. Gerçek uygulamada SendInput, testte sahte nesne.
class InputSink {
public:
    virtual ~InputSink() = default;
    virtual void move(long long x, long long y) = 0;
    virtual void click(long long x, long long y) = 0;
    virtual void wheel(long long x, long long y, long long steps) = 0;
    virtual void type(const std::string& utf8) = 0;
    // Alan temizlemede kullanılan kısayol gönderimi (Python "^a{DEL}").
    virtual void shortcut(const std::string& pywinauto_spec) = 0;
};

// Tek bir arayüz öğesi: gerçek UIA sarmalayıcısı ya da test sahtesi.
class UiTarget {
public:
    virtual ~UiTarget() = default;

    virtual std::string name() const = 0;
    virtual std::string control_type() const = 0;
    virtual std::string automation_id() const = 0;
    virtual std::string class_name() const = 0;
    virtual bool enabled() const = 0;
    virtual bool is_password() const = 0;
    virtual bool offscreen() const = 0;
    virtual std::optional<std::array<long long, 4>> bounds() const = 0;
    virtual std::vector<std::string> patterns() const = 0;
    virtual bool supports(const std::string& name) const = 0;

    // Yazma sonrası geri okuma: Value deseni değeri, olmazsa öğe adı.
    virtual std::string read_back() const = 0;
    virtual bool set_focus() = 0;

    // Desen eylemleri — öğe deseni desteklemiyorsa std::nullopt döner ve
    // çağıran SetFocus + SendInput düşüşünü seçer.
    virtual std::optional<ActionResult> invoke() = 0;
    virtual std::optional<ActionResult> toggle(const std::string& mode) = 0;
    virtual std::optional<ActionResult> expand(const std::string& mode) = 0;
    virtual std::optional<ActionResult> select() = 0;
    virtual std::optional<ActionResult> set_value(const std::string& text) = 0;
    virtual std::optional<ActionResult> clear() = 0;
    virtual std::optional<ActionResult> scroll(const std::string& direction, long long amount) = 0;
    virtual std::optional<ActionResult> scroll_into_view() = 0;
};

// Python apply_control_action + WindowsUI.act dağıtımı. Engel durumunda
// jarvis::ai::ActionForbidden fırlatır.
ControlOutcome apply_control_action(UiTarget& target, InputSink& input, const std::string& action,
                                    const std::string& text = "",
                                    const std::string& direction = "down", long long amount = 3);

// --- klavye sözdizimi (pywinauto send_keys) ----------------------------------
struct KeyToken {
    bool virtual_key = false;
    unsigned short vk = 0;
    std::string utf8;  // virtual_key değilse yazılacak tek karakter
    bool ctrl = false;
    bool alt = false;
    bool shift = false;
};

// "^k", "a{ENTER}b", "{ESC 2}" ayrıştırılır. Bilinmeyen {...} belirteci hata
// metni üretir (sessizce yutulmaz).
std::variant<std::vector<KeyToken>, std::string> parse_key_sequence(const std::string& spec);

// --- ajan araçları ------------------------------------------------------------
class UiAutomation {
public:
    // inspect_ui: pencereyi öne getirir, öğeleri merkez koordinatlarla listeler.
    std::string inspect_ui(const std::string& window_title, long long limit,
                           const std::string& name_filter);

    // ui_control: adla öğe bulur, eylemi uygular, geri okuyarak doğrular.
    std::string control(const nlohmann::json& args);

    // send_keys: pencereyi öne getirip kısayol/metin gönderir.
    std::string keys(const nlohmann::json& args);

    // find_text_on_screen: pencereyi (ya da tüm ekranı) PNG'ye yakar, Windows OCR
    // ile arar ve kutu merkezini gerçek ekran koordinatı olarak verir; click=true
    // ise SendInput ile tam merkeze tıklar.
    std::string find_text(const nlohmann::json& args);

    // screen_wait: ekran görüntüsü/OCR açmadan yalnızca pencere başlığı ve UIA
    // metnine bakarak bekler; bulunamazsa found=false (hata değil).
    std::string wait_for(const nlohmann::json& args);
};

}  // namespace jarvis::tools
