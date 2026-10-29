#include "jarvis/tools/ui_automation.hpp"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <map>
#include <optional>
#include <regex>
#include <set>
#include <thread>

#include "jarvis/ai/safety.hpp"
#include "jarvis/core/util.hpp"
#include "jarvis/core/win_path.hpp"
#include "jarvis/tools/ocr.hpp"
#include "jarvis/tools/screen_capture.hpp"
#include "jarvis/tools/window_tools.hpp"

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

#include <ole2.h>
#include <oleauto.h>
#include <uiautomation.h>
#endif

using nlohmann::json;

namespace jarvis::tools {
namespace {

std::string ascii_lower(std::string value) {
    for (char& c : value) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return value;
}

std::string arg_str(const json& args, const char* key, const std::string& fallback = "") {
    if (!args.is_object() || !args.contains(key) || args[key].is_null()) return fallback;
    const json& value = args[key];
    if (value.is_string()) return value.get<std::string>();
    if (value.is_number_integer()) return std::to_string(value.get<long long>());
    if (value.is_number_unsigned()) return std::to_string(value.get<unsigned long long>());
    if (value.is_boolean()) return value.get<bool>() ? "true" : "false";
    return fallback;
}

long long arg_num(const json& args, const char* key, long long fallback) {
    if (!args.is_object() || !args.contains(key) || args[key].is_null()) return fallback;
    const json& value = args[key];
    if (value.is_number_integer()) return value.get<long long>();
    if (value.is_number_unsigned()) return static_cast<long long>(value.get<unsigned long long>());
    if (value.is_number_float()) return static_cast<long long>(value.get<double>());
    if (value.is_string()) {
        try {
            return std::stoll(value.get<std::string>());
        } catch (const std::exception&) {
            return fallback;
        }
    }
    return fallback;
}

// Python `bool(args.get(...))` eşdeğeri: doğru/yanlış, yoksa yanlış.
bool arg_flag(const json& args, const char* key) {
    if (!args.is_object() || !args.contains(key) || args[key].is_null()) return false;
    const json& value = args[key];
    if (value.is_boolean()) return value.get<bool>();
    if (value.is_number()) return value.get<double>() != 0.0;
    if (value.is_string()) {
        const std::string folded = ascii_lower(util::trim(value.get<std::string>()));
        return folded == "true" || folded == "1" || folded == "evet" || folded == "açık";
    }
    return !value.is_null();
}

// Python `float(args.get(key, fallback))` — sayı değilse yut, varsayayana dön.
double arg_real(const json& args, const char* key, double fallback) {
    if (!args.is_object() || !args.contains(key) || args[key].is_null()) return fallback;
    const json& value = args[key];
    if (value.is_number()) return value.get<double>();
    if (value.is_string()) {
        try {
            return std::stod(value.get<std::string>());
        } catch (const std::exception&) {
            return fallback;
        }
    }
    return fallback;
}

// Python screen_agent.INTERACTIVE_TYPES
const std::set<std::string>& interactive_types() {
    static const std::set<std::string> kTypes = {
        "Button",   "ListItem",    "SplitButton", "TabItem",   "MenuItem",  "Edit",
        "ComboBox", "CheckBox",    "RadioButton", "Slider",    "TreeItem",  "Hyperlink",
        "DataItem", "HeaderItem",
    };
    return kTypes;
}

// Python WindowsUI.inspect'in etkileşimli kümesi + screen_agent.UIA_RICH_ACTIONS.
// click/type/focus WindowsUI.act'te ayrıca tanımlı; hepsi tek tabloda.
const std::set<std::string>& ui_actions() {
    static const std::set<std::string> kActions = {
        "click",  "type",    "focus",     "set_value", "clear",   "toggle",  "check",
        "uncheck", "expand", "collapse",  "select",    "scroll",  "scroll_to", "hover",
    };
    return kActions;
}

// Python SECURE_NAME_RE (normalize_text üzerinde çalışır).
const std::regex& secure_name_re() {
    static const std::regex kRe(
        R"(\b(parola|sifre|password|passwd|passcode|pin|secret|gizli)\b)",
        std::regex_constants::icase);
    return kRe;
}

// --- klavye sözdizimi --------------------------------------------------------
struct VkEntry {
    const char* name;
    unsigned short vk;
};

// Sanal tuş kodları Windows sabitleriyle birebir aynıdır (test edilebilirlik için
// başlık dosyasında tutulur; Win32'siz derlemede de ayrıştırıcı çalışır).
constexpr unsigned short kVkBack = 0x08;
constexpr unsigned short kVkTab = 0x09;
constexpr unsigned short kVkClear = 0x0C;
constexpr unsigned short kVkReturn = 0x0D;
constexpr unsigned short kVkShift = 0x10;
constexpr unsigned short kVkControl = 0x11;
constexpr unsigned short kVkMenu = 0x12;
constexpr unsigned short kVkPause = 0x13;
constexpr unsigned short kVkCapital = 0x14;
constexpr unsigned short kVkEscape = 0x1B;
constexpr unsigned short kVkSpace = 0x20;
constexpr unsigned short kVkPrior = 0x21;
constexpr unsigned short kVkNext = 0x22;
constexpr unsigned short kVkEnd = 0x23;
constexpr unsigned short kVkHome = 0x24;
constexpr unsigned short kVkLeft = 0x25;
constexpr unsigned short kVkUp = 0x26;
constexpr unsigned short kVkRight = 0x27;
constexpr unsigned short kVkDown = 0x28;
constexpr unsigned short kVkSelect = 0x29;
constexpr unsigned short kVkPrint = 0x2A;
constexpr unsigned short kVkExecute = 0x2B;
constexpr unsigned short kVkSnapshot = 0x2C;
constexpr unsigned short kVkInsert = 0x2D;
constexpr unsigned short kVkDelete = 0x2E;
constexpr unsigned short kVkHelp = 0x2F;
constexpr unsigned short kVkLwin = 0x5B;
constexpr unsigned short kVkRwin = 0x5C;
constexpr unsigned short kVkApps = 0x5D;
constexpr unsigned short kVkNumpad0 = 0x60;
constexpr unsigned short kVkMultiply = 0x6A;
constexpr unsigned short kVkAdd = 0x6B;
constexpr unsigned short kVkDivide = 0x6F;
constexpr unsigned short kVkDecimal = 0x6E;
constexpr unsigned short kVkSubtract = 0x6D;
constexpr unsigned short kVkF1 = 0x70;
constexpr unsigned short kVkNumlock = 0x90;

const std::map<std::string, unsigned short>& virtual_key_names() {
    static const std::map<std::string, unsigned short> kNames = {
        {"BACKSPACE", kVkBack},      {"BKSP", kVkBack},         {"BACK", kVkBack},
        {"TAB", kVkTab},             {"CLEAR", kVkClear},       {"ENTER", kVkReturn},
        {"RETURN", kVkReturn},       {"PAUSE", kVkPause},       {"CAPSLOCK", kVkCapital},
        {"ESC", kVkEscape},          {"ESCAPE", kVkEscape},     {"SPACE", kVkSpace},
        {"PGUP", kVkPrior},          {"PAGEUP", kVkPrior},      {"PGDN", kVkNext},
        {"PAGEDOWN", kVkNext},       {"END", kVkEnd},           {"HOME", kVkHome},
        {"LEFT", kVkLeft},           {"UP", kVkUp},             {"RIGHT", kVkRight},
        {"DOWN", kVkDown},           {"SELECT", kVkSelect},     {"PRINT", kVkPrint},
        {"EXECUTE", kVkExecute},     {"PRTSC", kVkSnapshot},    {"PRINTSCREEN", kVkSnapshot},
        {"INS", kVkInsert},          {"INSERT", kVkInsert},     {"DEL", kVkDelete},
        {"DELETE", kVkDelete},       {"HELP", kVkHelp},         {"WIN", kVkLwin},
        {"LWIN", kVkLwin},           {"RWIN", kVkRwin},         {"APPS", kVkApps},
        {"ADD", kVkAdd},             {"SUBTRACT", kVkSubtract}, {"MULTIPLY", kVkMultiply},
        {"DIVIDE", kVkDivide},       {"DECIMAL", kVkDecimal},   {"NUMLOCK", kVkNumlock},
    };
    return kNames;
}

std::optional<unsigned short> lookup_virtual_key(const std::string& raw) {
    std::string name = ascii_lower(raw);
    for (char& c : name) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
    const auto& table = virtual_key_names();
    if (const auto found = table.find(name); found != table.end()) return found->second;
    // F1..F24: F1-F10 0x70-0x79, F11-F24 0x7A-0x87.
    if (name.size() >= 2 && name[0] == 'F' && std::isdigit(static_cast<unsigned char>(name[1])) != 0) {
        bool digits_only = true;
        for (std::size_t i = 1; i < name.size(); ++i) {
            if (std::isdigit(static_cast<unsigned char>(name[i])) == 0) digits_only = false;
        }
        if (digits_only) {
            const long long index = std::stoll(name.substr(1));
            if (index >= 1 && index <= 10) {
                return static_cast<unsigned short>(kVkF1 + (index - 1));
            }
            if (index >= 11 && index <= 24) {
                return static_cast<unsigned short>(0x7A + (index - 11));
            }
        }
    }
    // NUMPAD0..NUMPAD9
    if (name.rfind("NUMPAD", 0) == 0 && name.size() == 7 &&
        std::isdigit(static_cast<unsigned char>(name[6])) != 0) {
        return static_cast<unsigned short>(kVkNumpad0 + (name[6] - '0'));
    }
    return std::nullopt;
}

std::string utf8_code_point(unsigned int code) {
    std::string out;
    if (code < 0x80) {
        out.push_back(static_cast<char>(code));
    } else if (code < 0x800) {
        out.push_back(static_cast<char>(0xC0 | (code >> 6)));
        out.push_back(static_cast<char>(0x80 | (code & 0x3F)));
    } else if (code < 0x10000) {
        out.push_back(static_cast<char>(0xE0 | (code >> 12)));
        out.push_back(static_cast<char>(0x80 | ((code >> 6) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | (code & 0x3F)));
    } else {
        out.push_back(static_cast<char>(0xF0 | (code >> 18)));
        out.push_back(static_cast<char>(0x80 | ((code >> 12) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | ((code >> 6) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | (code & 0x3F)));
    }
    return out;
}

// Düz metin aralığının sonu: değiştirici işareti veya '{' e kadar.
std::size_t plain_run_end(const std::string& spec, std::size_t start) {
    for (std::size_t i = start; i < spec.size(); ++i) {
        const char c = spec[i];
        if (c == '^' || c == '%' || c == '+' || c == '{') return i;
    }
    return spec.size();
}

}  // namespace

bool is_interactive_type(const std::string& control_type) {
    return interactive_types().count(control_type) != 0;
}

bool is_ui_action(const std::string& action) {
    return ui_actions().count(ascii_lower(util::trim(action))) != 0;
}

bool is_secure_element(const std::string& name, const std::string& control_type, bool is_password) {
    if (is_password) return true;
    const std::string kind = normalize_text(control_type);
    if (kind != "edit" && kind != "document") return false;
    return std::regex_search(normalize_text(name), secure_name_re()) ? true : false;
}

std::optional<std::string> control_block_reason(const std::string& action,
                                                const std::string& name,
                                                const std::string& control_type, bool is_password) {
    // Odaklanma ve fare imleç taşıma hiçbir değer değiştirmez; izin verilir.
    const std::string key = ascii_lower(util::trim(action));
    const bool writes = key == "click" || key == "type" || key == "set_value" || key == "clear" ||
                        key == "toggle" || key == "check" || key == "uncheck";
    if (!writes) return std::nullopt;
    if (!is_secure_element(name, control_type, is_password)) return std::nullopt;
    const std::string label = name.empty() ? "(gizli alan)" : name;
    return "Parola/gizli alan: " + label +
           ". Güvenlik gereği bu alana tıklanmaz, yazılmaz veya işaretlenmez.";
}

// ------------------------------------------------------------------ ayrıştırıcı

std::variant<std::vector<KeyToken>, std::string> parse_key_sequence(const std::string& spec) {
    std::vector<KeyToken> tokens;
    bool ctrl = false;
    bool alt = false;
    bool shift = false;
    std::size_t i = 0;
    const std::size_t n = spec.size();

    auto flush_modifier = [&](char marker) {
        switch (marker) {
            case '^': ctrl = true; break;
            case '%': alt = true; break;
            case '+': shift = true; break;
            default: break;
        }
    };

    while (i < n) {
        const char c = spec[i];
        if (c == '^' || c == '%' || c == '+') {
            flush_modifier(c);
            ++i;
            continue;
        }
        if (c == '{') {
            const std::size_t close = spec.find('}', i + 1);
            if (close == std::string::npos) {
                return std::string("Kapanmayan klavye belirteci: { kalan metin");
            }
            std::string body = spec.substr(i + 1, close - i - 1);
            i = close + 1;
            if (body == "{" || body == "}") {
                KeyToken token;
                token.utf8 = body;
                token.ctrl = ctrl;
                token.alt = alt;
                token.shift = shift;
                ctrl = alt = shift = false;
                tokens.push_back(token);
                continue;
            }
            // "{ENTER 3}" yineleme desteklenir.
            long long repeat = 1;
            const std::size_t space = body.find(' ');
            if (space != std::string::npos) {
                const std::string tail = util::trim(body.substr(space + 1));
                bool digits = !tail.empty();
                for (const char digit : tail) {
                    if (std::isdigit(static_cast<unsigned char>(digit)) == 0) digits = false;
                }
                if (!digits) {
                    return std::string("Klavye belirteci yineleme sayısı geçersiz: {") + body + "}";
                }
                repeat = std::stoll(tail);
                if (repeat < 1 || repeat > 100) {
                    return std::string("Klavye belirteci yineleme sayısı 1-100 arasında olmalı: {") +
                           body + "}";
                }
                body = util::trim(body.substr(0, space));
            }
            const auto vk = lookup_virtual_key(body);
            if (!vk) {
                return std::string("Bilinmeyen klavye belirteci: {") + body + "}";
            }
            for (long long k = 0; k < repeat; ++k) {
                KeyToken token;
                token.virtual_key = true;
                token.vk = *vk;
                token.ctrl = ctrl;
                token.alt = alt;
                token.shift = shift;
                tokens.push_back(token);
            }
            ctrl = alt = shift = false;
            continue;
        }
        // Düz metin: kod noktası başına bir belirteç. Kod noktaları window_tools'taki
        // ortak çözücünden gelir (normalize_text ile aynı UTF-8 davranışı).
        const std::size_t end = plain_run_end(spec, i);
        for (const unsigned int code : code_points(spec.substr(i, end - i))) {
            KeyToken token;
            token.utf8 = utf8_code_point(code);
            token.ctrl = ctrl;
            token.alt = alt;
            token.shift = shift;
            tokens.push_back(token);
            ctrl = alt = shift = false;
        }
        i = end;
    }
    if (ctrl || alt || shift) {
        return std::string("Klavye değiştiricisi (^, %, +) bir tuş olmadan bırakılamaz.");
    }
    return tokens;
}

// ------------------------------------------------------------------ eylem dağıtımı

namespace {

std::string delivery_for(bool verified) {
    return verified ? "delivered" : "delivered_unverified";
}

std::optional<std::pair<long long, long long>> target_center(UiTarget& target) {
    const auto bounds = target.bounds();
    if (!bounds) return std::nullopt;
    const long long x = ((*bounds)[0] + (*bounds)[2]) / 2;
    const long long y = ((*bounds)[1] + (*bounds)[3]) / 2;
    return std::make_pair(x, y);
}

ControlOutcome outcome(ActionResult action, std::string retry) {
    ControlOutcome result;
    result.mechanism = std::move(action.mechanism);
    result.verified = action.verified;
    result.note = std::move(action.note);
    result.delivery = delivery_for(result.verified);
    result.retry = std::move(retry);
    return result;
}

// click: Invoke → Toggle/SelectionItem → SetFocus + SendInput (Python act sırası).
ControlOutcome do_click(UiTarget& target, InputSink& input) {
    if (target.supports(pattern::kInvoke)) {
        if (auto action = target.invoke(); action) return outcome(*action, "unsafe");
    }
    if (target.supports(pattern::kToggle)) {
        if (auto action = target.toggle("toggle"); action) return outcome(*action, "unsafe");
    }
    if (target.supports(pattern::kSelectionItem)) {
        if (auto action = target.select(); action) return outcome(*action, "unsafe");
    }
    const auto center = target_center(target);
    if (!center) throw ai::ActionForbidden("Öğenin ekran konumu okunamadı.");
    if (!target.set_focus()) {
        throw ai::ActionForbidden("Öğe odaklanamadı; yanlış yere tıklamayı önlemek için durduruldu.");
    }
    input.click(center->first, center->second);
    return outcome({"mouse_click", false,
                    "Fareyle tıklama gönderildi; sonucu ekran görüntüsüyle doğrula."},
                   "unsafe");
}

// type/set_value: ValuePattern.SetValue → SetFocus + Unicode SendInput.
ControlOutcome do_write(UiTarget& target, InputSink& input, const std::string& text) {
    if (target.supports(pattern::kValue)) {
        if (auto action = target.set_value(text); action) {
            if (action->verified) return outcome(*action, "safe");
            return outcome(*action, "unsafe");
        }
    }
    if (!target.set_focus()) {
        throw ai::ActionForbidden("Öğe odaklanamadı; yanlış yere yazmayı önlemek için durduruldu.");
    }
    input.type(text);
    const bool verified = target.read_back() == text;
    return outcome({"keyboard_unicode", verified,
                    verified ? "Yazılan değer arayüzden geri okunarak doğrulandı."
                             : "Yazma gönderildi; değer geri okunamadı."},
                   "unsafe");
}

ControlOutcome do_scroll(UiTarget& target, InputSink& input, const std::string& direction,
                         long long amount) {
    const std::string key = ascii_lower(util::trim(direction));
    if (key != "up" && key != "down") {
        throw ai::ActionForbidden("scroll yönü up veya down olmalı; yatay kaydırma desteklenmiyor.");
    }
    amount = std::min<long long>(std::max<long long>(amount, 1), 20);
    if (target.supports(pattern::kScroll)) {
        if (auto action = target.scroll(key, amount); action) {
            return outcome(*action, "safe");
        }
    }
    const auto center = target_center(target);
    if (!center) throw ai::ActionForbidden("Öğenin ekran konumu okunamadı.");
    input.wheel(center->first, center->second, key == "up" ? amount : -amount);
    return outcome({"mouse_wheel", false,
                    std::to_string(amount) + " adım " + key +
                        " kaydırıldı; görünürlüğü ekran görüntüsüyle doğrula."},
                   "safe");
}

}  // namespace

ControlOutcome apply_control_action(UiTarget& target, InputSink& input, const std::string& action,
                                    const std::string& text, const std::string& direction,
                                    long long amount) {
    const std::string key = ascii_lower(util::trim(action));
    if (!ui_actions().count(key)) {
        throw ai::ActionForbidden("Desteklenmeyen arayüz eylemi: " + action);
    }
    if (key == "click") return do_click(target, input);
    if (key == "type" || key == "set_value") {
        if (text.empty()) throw ai::ActionForbidden("type/set_value için text gerekli.");
        return do_write(target, input, text);
    }
    if (key == "clear") {
        if (target.supports(pattern::kValue)) {
            if (auto cleared = target.clear(); cleared) return outcome(*cleared, "safe");
        }
        if (!target.set_focus()) {
            throw ai::ActionForbidden("Bu alan temizlenemedi; önce alanı odakla.");
        }
        input.shortcut("^a{DEL}");
        const bool verified = target.read_back().empty();
        return outcome({"keyboard_clear", verified,
                        verified ? "Alan boş olarak geri okundu."
                                 : "Temizleme gönderildi; değer geri okunamadı."},
                       "safe");
    }
    if (key == "focus") {
        const bool focused = target.set_focus();
        return outcome({"uia_set_focus", focused,
                        focused ? "Öğe odaklandı." : "Odaklama isteği gönderildi; doğrulanamadı."},
                       "safe");
    }
    if (key == "hover") {
        const auto center = target_center(target);
        if (!center) throw ai::ActionForbidden("Öğenin ekran konumu okunamadı.");
        input.move(center->first, center->second);
        return outcome({"mouse_hover", false, "Fare öğenin üzerine getirildi."}, "safe");
    }
    if (key == "toggle" || key == "check" || key == "uncheck") {
        if (!target.supports(pattern::kToggle)) {
            throw ai::ActionForbidden("Bu öğe işaretleme/açma-kapama desteklemiyor.");
        }
        auto action_result = target.toggle(key);
        if (!action_result) throw ai::ActionForbidden("Bu öğe işaretleme/açma-kapama desteklemiyor.");
        return outcome(*action_result, key == "toggle" ? "unsafe" : "safe");
    }
    if (key == "expand" || key == "collapse") {
        if (!target.supports(pattern::kExpandCollapse)) {
            throw ai::ActionForbidden("Bu öğe açma/kapama desteklemiyor.");
        }
        auto expanded = target.expand(key);
        if (!expanded) throw ai::ActionForbidden("Bu öğe açma/kapama desteklemiyor.");
        return outcome(*expanded, "safe");
    }
    if (key == "select") {
        if (!target.supports(pattern::kSelectionItem)) {
            throw ai::ActionForbidden("Bu öğe seçim desteklemiyor.");
        }
        auto selected = target.select();
        if (!selected) throw ai::ActionForbidden("Bu öğe seçim desteklemiyor.");
        return outcome(*selected, "safe");
    }
    if (key == "scroll_to") {
        if (!target.supports(pattern::kScrollItem)) {
            throw ai::ActionForbidden("Bu öğe görünür alana kaydırmayı desteklemiyor.");
        }
        auto scrolled = target.scroll_into_view();
        if (!scrolled) throw ai::ActionForbidden("Bu öğe görünür alana kaydırmayı desteklemiyor.");
        return outcome(*scrolled, "safe");
    }
    return do_scroll(target, input, direction, amount);
}

#ifdef _WIN32

namespace {

// --- Win32 / UI Automation ---------------------------------------------------
//
// GUID'ler yerelde tanımlanır: uuid.lib'e bağlanmadan CoCreateInstance
// çağrılabilsin. Değerler Windows SDK başlıklarıyla birebir aynıdır.
const CLSID kClsidUiAutomation = {0xff48dba4, 0x60ef, 0x4201,
                                  {0xaa, 0x87, 0x54, 0x10, 0x3e, 0xef, 0x59, 0x4e}};
const IID kIIDUiAutomation = {0x30cbe57d, 0xd9d0, 0x452a,
                              {0xab, 0x13, 0x7a, 0xc5, 0xac, 0x48, 0x25, 0xee}};

// Python'daki gibi tek seferlik başlatma: iş parçacığı ölçeğinde, önbellekli örnek.
struct ThreadCom {
    HRESULT init = E_FAIL;
    bool attempted = false;
    bool owns_apartment = false;
    IUIAutomation* api = nullptr;

    ~ThreadCom() {
        if (api) {
            api->Release();
            api = nullptr;
        }
        if (owns_apartment) CoUninitialize();
    }
};

ThreadCom& thread_com() {
    thread_local ThreadCom state;
    if (!state.attempted) {
        state.attempted = true;
        state.init = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
        // RPC_E_CHANGED_MODE: başka bir model zaten kurulu; COM kullanılabilir ama
        // biz başlatmadığımız için kapatma borcumuz yok.
        state.owns_apartment = state.init == S_OK || state.init == S_FALSE;
    }
    return state;
}

IUIAutomation* uia_api() {
    ThreadCom& state = thread_com();
    if (FAILED(state.init) && state.init != RPC_E_CHANGED_MODE) return nullptr;
    if (!state.api) {
        IUIAutomation* created = nullptr;
        const HRESULT hr = CoCreateInstance(kClsidUiAutomation, nullptr, CLSCTX_INPROC_SERVER,
                                           kIIDUiAutomation, reinterpret_cast<void**>(&created));
        state.api = SUCCEEDED(hr) ? created : nullptr;
    }
    return state.api;
}

std::string bstr_text(BSTR value) {
    if (!value) return {};
    const int count = SysStringLen(value);
    return util::wide_to_utf8(std::wstring(value, static_cast<std::size_t>(count)));
}

// pywinauto friendly_class_name karşılığı: UIA_CONTROLTYPE_id → İngilizce ad.
std::string control_type_name(long long id) {
    static const std::map<long long, const char*> kNames = {
        {50000, "Button"},        {50001, "Calendar"},      {50002, "CheckBox"},
        {50003, "ComboBox"},      {50004, "Edit"},          {50005, "Hyperlink"},
        {50006, "Image"},         {50007, "ListItem"},      {50008, "List"},
        {50009, "Menu"},          {50010, "MenuBar"},       {50011, "MenuItem"},
        {50012, "ProgressBar"},   {50013, "RadioButton"},   {50014, "ScrollBar"},
        {50015, "Slider"},        {50016, "Spinner"},       {50017, "StatusBar"},
        {50018, "Tab"},           {50019, "TabItem"},       {50020, "Text"},
        {50021, "ToolBar"},       {50022, "ToolTip"},       {50023, "Tree"},
        {50024, "TreeItem"},      {50025, "Custom"},        {50026, "Group"},
        {50027, "Thumb"},         {50028, "DataGrid"},      {50029, "DataItem"},
        {50030, "Document"},      {50031, "SplitButton"},   {50032, "Window"},
        {50033, "Pane"},          {50034, "Header"},        {50035, "HeaderItem"},
        {50036, "Table"},         {50037, "TitleBar"},      {50038, "Separator"},
        {50039, "SemanticZoom"},  {50040, "AppBar"},
    };
    const auto found = kNames.find(id);
    return found == kNames.end() ? "Unknown" : found->second;
}

long long pattern_id(const std::string& name) {
    if (name == pattern::kInvoke) return UIA_InvokePatternId;
    if (name == pattern::kValue) return UIA_ValuePatternId;
    if (name == pattern::kScroll) return UIA_ScrollPatternId;
    if (name == pattern::kExpandCollapse) return UIA_ExpandCollapsePatternId;
    if (name == pattern::kSelectionItem) return UIA_SelectionItemPatternId;
    if (name == pattern::kToggle) return UIA_TogglePatternId;
    if (name == pattern::kScrollItem) return UIA_ScrollItemPatternId;
    return 0;
}

// --- fare/klavye girişi ------------------------------------------------------

// Sanal ekranın tamamı kapsanır: çok monitörde mutlak koordinatlar buraya göre
// normalize edilir (SendInput MOUSEEVENTF_ABSOLUTE sözleşmesi).
void normalized_point(long long x, long long y, long& nx, long& ny) {
    const int left = GetSystemMetrics(SM_XVIRTUALSCREEN);
    const int top = GetSystemMetrics(SM_YVIRTUALSCREEN);
    const int width = GetSystemMetrics(SM_CXVIRTUALSCREEN);
    const int height = GetSystemMetrics(SM_CYVIRTUALSCREEN);
    const int span_x = width > 1 ? width - 1 : 1;
    const int span_y = height > 1 ? height - 1 : 1;
    nx = static_cast<long>((static_cast<double>(x - left) * 65535.0) / span_x);
    ny = static_cast<long>((static_cast<double>(y - top) * 65535.0) / span_y);
}

void send_inputs(std::vector<INPUT>& events) {
    if (events.empty()) return;
    SendInput(static_cast<UINT>(events.size()), events.data(), sizeof(INPUT));
    events.clear();
}

void pause_input() {
    // pywinauto send_keys/mouse varsayılanı pause=0.08 — her olaydan sonra aynı bekleme.
    std::this_thread::sleep_for(std::chrono::milliseconds(80));
}

// Pano yazması executor'daki yardımçıdan ayrı: orası jarvis_ai içinde ve dosyaya
// özel. UIA katmanı kendi yazıcısını kullanır.
bool write_clipboard(const std::string& utf8) {
    const std::wstring wide = util::utf8_to_wide(utf8);
    const std::size_t bytes = (wide.size() + 1) * sizeof(wchar_t);
    if (!OpenClipboard(nullptr)) return false;
    bool written = false;
    HGLOBAL memory = GlobalAlloc(GMEM_MOVEABLE, bytes);
    if (memory) {
        void* block = GlobalLock(memory);
        if (block) {
            std::memcpy(block, wide.data(), bytes);
            GlobalUnlock(memory);
            EmptyClipboard();
            written = SetClipboardData(CF_UNICODETEXT, memory) != nullptr;
        }
        if (!written) GlobalFree(memory);
    }
    CloseClipboard();
    return written;
}

class WinInput : public InputSink {
public:
    void move(const long long x, const long long y) override { point(x, y); }

    void click(const long long x, const long long y) override { click_button("left", x, y); }

    // pywinauto mouse.click(button=...) karşılığı. InputSink arayüzüne eklenmedi:
    // test sahteleri sol tıklamayı kullanır, sağ/orta yalnızca find_text_on_screen
    // ve mouse_control hattından gelir.
    void click_button(const std::string& button, long long x, long long y) {
        DWORD down = MOUSEEVENTF_LEFTDOWN;
        DWORD up = MOUSEEVENTF_LEFTUP;
        if (button == "right") {
            down = MOUSEEVENTF_RIGHTDOWN;
            up = MOUSEEVENTF_RIGHTUP;
        } else if (button == "middle") {
            down = MOUSEEVENTF_MIDDLEDOWN;
            up = MOUSEEVENTF_MIDDLEUP;
        }
        point(x, y);
        std::vector<INPUT> events = {make_mouse(down), make_mouse(up)};
        send_inputs(events);
        pause_input();
    }

    void wheel(const long long x, const long long y, const long long steps) override {
        point(x, y);
        INPUT event = make_mouse(MOUSEEVENTF_WHEEL);
        event.mi.mouseData = static_cast<DWORD>(steps * 120);
        std::vector<INPUT> events{event};
        send_inputs(events);
        pause_input();
    }

    void type(const std::string& utf8) override {
        if (utf8.empty()) return;
        const std::wstring wide = util::utf8_to_wide(utf8);
        std::vector<INPUT> events;
        events.reserve(wide.size() * 2);
        for (const wchar_t unit : wide) events.push_back(make_unicode(unit));
        send_inputs(events);
        pause_input();
    }

    // Alan temizleme kısayolu: ayrıştırılıp token token gönderilir.
    void shortcut(const std::string& pywinauto_spec) override {
        const auto parsed = parse_key_sequence(pywinauto_spec);
        if (const auto* sequence = std::get_if<std::vector<KeyToken>>(&parsed)) tokens(*sequence);
    }

    // Ayrıştırılmış pywinauto belirteçlerini gönderir: değiştiriciler + sanal tuş
    // ya da Unicode düz metin.
    void tokens(const std::vector<KeyToken>& tokens) {
        for (const KeyToken& token : tokens) {
            std::vector<INPUT> events;
            const unsigned short ctrl = token.ctrl ? kVkControl : 0;
            const unsigned short alt = token.alt ? kVkMenu : 0;
            const unsigned short shift = token.shift ? kVkShift : 0;
            for (const unsigned short modifier : {ctrl, alt, shift}) {
                if (modifier) events.push_back(make_key(modifier, false));
            }
            if (token.virtual_key) {
                events.push_back(make_key(token.vk, false));
                events.push_back(make_key(token.vk, true));
            } else {
                const std::wstring wide = util::utf8_to_wide(token.utf8);
                const std::optional<unsigned short> scanned = scanned_key(wide);
                if (scanned && (ctrl || alt || shift)) {
                    // Değiştiriciyle eşleşen kısayol tuşu (ör. ^k) sanal tuşla gider.
                    events.push_back(make_key(*scanned, false));
                    events.push_back(make_key(*scanned, true));
                } else {
                    for (const wchar_t unit : wide) events.push_back(make_unicode(unit));
                }
            }
            for (const unsigned short modifier : {shift, alt, ctrl}) {
                if (modifier) events.push_back(make_key(modifier, true));
            }
            send_inputs(events);
            pause_input();
        }
    }

    // Metni panoya yazar ve ^v ile yapıştırır (Python keys()'deki yol).
    void paste(const std::string& utf8) {
        if (write_clipboard(utf8)) tokens(make_paste_tokens());
    }

private:
    static std::vector<KeyToken> make_paste_tokens() {
        KeyToken token;
        token.virtual_key = true;
        token.vk = static_cast<unsigned short>('V');
        token.ctrl = true;
        return {token};
    }

    static std::optional<unsigned short> scanned_key(const std::wstring& wide) {
        if (wide.size() != 1) return std::nullopt;
        const SHORT scanned = VkKeyScanW(wide[0]);
        if (scanned == -1) return std::nullopt;
        return static_cast<unsigned short>(LOBYTE(scanned));
    }

    static INPUT make_mouse(DWORD flags) {
        INPUT event{};
        event.type = INPUT_MOUSE;
        event.mi.dwFlags = flags;
        return event;
    }

    static INPUT make_unicode(wchar_t unit) {
        INPUT event{};
        event.type = INPUT_KEYBOARD;
        event.ki.wScan = static_cast<WORD>(unit);
        event.ki.dwFlags = KEYEVENTF_UNICODE;
        return event;
    }

    static INPUT make_key(unsigned short vk, bool up) {
        INPUT event{};
        event.type = INPUT_KEYBOARD;
        event.ki.wVk = vk;
        const UINT scan = MapVirtualKeyW(vk, MAPVK_VK_TO_VSC);
        event.ki.wScan = static_cast<WORD>(scan);
        if (uses_extended_key(vk)) event.ki.dwFlags |= KEYEVENTF_EXTENDEDKEY;
        if (up) event.ki.dwFlags |= KEYEVENTF_KEYUP;
        return event;
    }

    static bool uses_extended_key(unsigned short vk) {
        switch (vk) {
            case kVkPrior: case kVkNext: case kVkEnd: case kVkHome:
            case kVkLeft: case kVkUp: case kVkRight: case kVkDown:
            case kVkInsert: case kVkDelete: case kVkRwin: case kVkLwin:
                return true;
            default: return false;
        }
    }

    void point(long long x, long long y) {
        long nx = 0;
        long ny = 0;
        normalized_point(x, y, nx, ny);
        INPUT event = make_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE);
        event.mi.dx = nx;
        event.mi.dy = ny;
        std::vector<INPUT> events{event};
        send_inputs(events);
    }
};

// --- UIA öğe sarmalayıcısı ---------------------------------------------------

class WinElement : public UiTarget {
public:
    explicit WinElement(IUIAutomationElement* raw) : raw_(raw) {}
    WinElement(const WinElement&) = delete;
    WinElement& operator=(const WinElement&) = delete;
    ~WinElement() override { release(); }

    std::string name() const override { return string_property(&IUIAutomationElement::get_CurrentName); }
    std::string automation_id() const override {
        return string_property(&IUIAutomationElement::get_CurrentAutomationId);
    }
    std::string class_name() const override {
        return string_property(&IUIAutomationElement::get_CurrentClassName);
    }

    std::string control_type() const override {
        int id = 0;
        if (raw_ && SUCCEEDED(raw_->get_CurrentControlType(&id))) return control_type_name(id);
        return "Unknown";
    }

    bool enabled() const override { return bool_property(&IUIAutomationElement::get_CurrentIsEnabled); }
    bool offscreen() const override { return bool_property(&IUIAutomationElement::get_CurrentIsOffscreen); }

    bool is_password() const override {
        if (!raw_) return false;
        VARIANT value{};
        value.vt = VT_BOOL;
        value.boolVal = VARIANT_FALSE;
        const HRESULT hr = raw_->GetCurrentPropertyValue(UIA_IsPasswordPropertyId, &value);
        const bool result = SUCCEEDED(hr) && value.vt == VT_BOOL && value.boolVal == VARIANT_TRUE;
        VariantClear(&value);
        return result;
    }

    std::optional<std::array<long long, 4>> bounds() const override {
        if (!raw_) return std::nullopt;
        RECT rect{};
        if (FAILED(raw_->get_CurrentBoundingRectangle(&rect))) return std::nullopt;
        if (rect.right <= rect.left || rect.bottom <= rect.top) return std::nullopt;
        return std::array<long long, 4>{static_cast<long long>(rect.left),
                                       static_cast<long long>(rect.top),
                                       static_cast<long long>(rect.right),
                                       static_cast<long long>(rect.bottom)};
    }

    std::vector<std::string> patterns() const override {
        static const std::pair<const char*, long long> kProbed[] = {
            {pattern::kInvoke, UIA_InvokePatternId},
            {pattern::kValue, UIA_ValuePatternId},
            {pattern::kScroll, UIA_ScrollPatternId},
            {pattern::kExpandCollapse, UIA_ExpandCollapsePatternId},
            {pattern::kSelectionItem, UIA_SelectionItemPatternId},
            {pattern::kToggle, UIA_TogglePatternId},
            {pattern::kScrollItem, UIA_ScrollItemPatternId},
        };
        std::vector<std::string> out;
        for (const auto& entry : kProbed) {
            if (holds_pattern(entry.second)) out.push_back(entry.first);
        }
        return out;
    }

    bool supports(const std::string& name) const override {
        const long long id = pattern_id(name);
        return id != 0 && holds_pattern(id);
    }

    std::string read_back() const override {
        IUIAutomationValuePattern* value = nullptr;
        if (!query(&value, UIA_ValuePatternId)) return name();
        BSTR raw = nullptr;
        std::string out;
        if (SUCCEEDED(value->get_CurrentValue(&raw))) out = bstr_text(raw);
        if (raw) SysFreeString(raw);
        value->Release();
        return out;
    }

    bool set_focus() override { return raw_ && SUCCEEDED(raw_->SetFocus()); }

    std::optional<ActionResult> invoke() override {
        IUIAutomationInvokePattern* pattern = nullptr;
        if (!query(&pattern, UIA_InvokePatternId)) return std::nullopt;
        const HRESULT hr = pattern->Invoke();
        pattern->Release();
        if (FAILED(hr)) return std::nullopt;
        return outcome_for("uia_invoke", hr);
    }

    std::optional<ActionResult> toggle(const std::string& mode) override {
        IUIAutomationTogglePattern* pattern = nullptr;
        if (!query(&pattern, UIA_TogglePatternId)) return std::nullopt;
        const long long before = toggle_state();
        if (mode == "check" && before == 1) {
            pattern->Release();
            return ActionResult{"already_checked", true, "Öğe zaten işaretli."};
        }
        if (mode == "uncheck" && before == 0) {
            pattern->Release();
            return ActionResult{"already_unchecked", true, "Öğe zaten işaretsiz."};
        }
        const HRESULT hr = pattern->Toggle();
        pattern->Release();
        if (FAILED(hr)) return std::nullopt;
        const long long after = toggle_state();
        if (mode == "check") {
            const bool verified = after == 1;
            return ActionResult{"uia_toggle", verified,
                                verified ? "İşaretli durumu arayüzden doğrulandı."
                                         : "İşaretleme gönderildi; durum okunamadı."};
        }
        if (mode == "uncheck") {
            const bool verified = after == 0;
            return ActionResult{"uia_toggle", verified,
                                verified ? "İşaretsiz durumu arayüzden doğrulandı."
                                         : "İşaret kaldırma gönderildi; durum okunamadı."};
        }
        const bool verified = after >= 0 && before >= 0 && after != before;
        return ActionResult{"uia_toggle", verified,
                            verified ? "Durum değişti (" + std::to_string(before) + " → " +
                                           std::to_string(after) + ")."
                                     : "Açma/kapama gönderildi; durum doğrulanamadı."};
    }

    std::optional<ActionResult> expand(const std::string& mode) override {
        IUIAutomationExpandCollapsePattern* pattern = nullptr;
        if (!query(&pattern, UIA_ExpandCollapsePatternId)) return std::nullopt;
        const HRESULT hr = mode == "expand" ? pattern->Expand() : pattern->Collapse();
        pattern->Release();
        if (FAILED(hr)) return std::nullopt;
        long long state = -1;
        IUIAutomationExpandCollapsePattern* again = nullptr;
        if (query(&again, UIA_ExpandCollapsePatternId)) {
            ExpandCollapseState value = ExpandCollapseState_Collapsed;
            if (SUCCEEDED(again->get_CurrentExpandCollapseState(&value))) {
                state = static_cast<long long>(value);
            }
            again->Release();
        }
        if (state < 0) {
            return ActionResult{"uia_" + mode, false,
                                "Komut gönderildi; açık/kapalı durumu okunamadı."};
        }
        const bool expected = mode == "expand" ? state == 1 : state == 0;
        return ActionResult{"uia_" + mode, expected,
                            expected ? (mode == "expand" ? "Açık durumu doğrulandı."
                                                         : "Kapalı durumu doğrulandı.")
                                     : "Durum beklenen değerde değil."};
    }

    std::optional<ActionResult> select() override {
        IUIAutomationSelectionItemPattern* pattern = nullptr;
        if (!query(&pattern, UIA_SelectionItemPatternId)) return std::nullopt;
        const HRESULT hr = pattern->Select();
        pattern->Release();
        if (FAILED(hr)) return std::nullopt;
        IUIAutomationSelectionItemPattern* again = nullptr;
        bool selected = false;
        if (query(&again, UIA_SelectionItemPatternId)) {
            BOOL value = VARIANT_FALSE;
            if (SUCCEEDED(again->get_CurrentIsSelected(&value))) selected = value == VARIANT_TRUE;
            again->Release();
            return ActionResult{"uia_select", selected,
                                selected ? "Seçili durumu doğrulandı." : "Öğe seçili görünmüyor."};
        }
        return ActionResult{"uia_select", false, "Seçim gönderildi; seçili durumu okunamadı."};
    }

    std::optional<ActionResult> set_value(const std::string& text) override {
        IUIAutomationValuePattern* pattern = nullptr;
        if (!query(&pattern, UIA_ValuePatternId)) return std::nullopt;
        BOOL readonly = VARIANT_FALSE;
        pattern->get_CurrentIsReadOnly(&readonly);
        if (readonly == VARIANT_TRUE) {
            pattern->Release();
            return std::nullopt;
        }
        const std::wstring wide = util::utf8_to_wide(text);
        BSTR value = SysAllocString(wide.c_str());
        const HRESULT hr = value ? pattern->SetValue(value) : E_OUTOFMEMORY;
        if (value) SysFreeString(value);
        pattern->Release();
        if (FAILED(hr)) return std::nullopt;
        const bool verified = read_back() == text;
        return ActionResult{"uia_value", verified,
                            verified ? "Yazılan değer arayüzden geri okunarak doğrulandı."
                                     : "Yazma gönderildi; değer geri okunamadı."};
    }

    std::optional<ActionResult> clear() override {
        IUIAutomationValuePattern* pattern = nullptr;
        if (!query(&pattern, UIA_ValuePatternId)) return std::nullopt;
        BOOL readonly = VARIANT_FALSE;
        pattern->get_CurrentIsReadOnly(&readonly);
        if (readonly == VARIANT_TRUE) {
            pattern->Release();
            return std::nullopt;
        }
        BSTR empty = SysAllocString(L"");
        const HRESULT hr = empty ? pattern->SetValue(empty) : E_OUTOFMEMORY;
        if (empty) SysFreeString(empty);
        pattern->Release();
        if (FAILED(hr)) return std::nullopt;
        const bool verified = read_back().empty();
        return ActionResult{"uia_clear", verified,
                            verified ? "Alan boş olarak geri okundu."
                                     : "Temizleme gönderildi; değer geri okunamadı."};
    }

    std::optional<ActionResult> scroll(const std::string& direction, long long amount) override {
        IUIAutomationScrollPattern* pattern = nullptr;
        if (!query(&pattern, UIA_ScrollPatternId)) return std::nullopt;
        const bool vertical = direction == "up" || direction == "down";
        const double before = scroll_percent(*pattern, vertical);
        const ScrollAmount step = (direction == "down" || direction == "right")
                                      ? ScrollAmount_SmallIncrement
                                      : ScrollAmount_SmallDecrement;
        // Kullanılmayan eksen -1 (NoScroll) gönderilir; UIA o eksene dokunmaz.
        const ScrollAmount idle = static_cast<ScrollAmount>(-1);
        HRESULT last = S_OK;
        for (long long i = 0; i < amount; ++i) {
            last = pattern->Scroll(vertical ? idle : step, vertical ? step : idle);
            if (FAILED(last)) break;
        }
        const double after = scroll_percent(*pattern, vertical);
        pattern->Release();
        if (FAILED(last)) return std::nullopt;
        const bool verified = before >= 0 && after >= 0 && before != after;
        return ActionResult{"uia_scroll", verified,
                            std::to_string(amount) + " adım " + direction + " kaydırıldı" +
                                (verified ? "; konum arayüzden doğrulandı."
                                          : "; görünürlüğü ekran görüntüsüyle doğrula.")};
    }

    std::optional<ActionResult> scroll_into_view() override {
        IUIAutomationScrollItemPattern* pattern = nullptr;
        if (!query(&pattern, UIA_ScrollItemPatternId)) return std::nullopt;
        const HRESULT hr = pattern->ScrollIntoView();
        pattern->Release();
        if (FAILED(hr)) return std::nullopt;
        set_focus();
        return ActionResult{"uia_scroll_into_view", false,
                            "Kaydırma isteği gönderildi; görünürlüğü ekran görüntüsüyle doğrula."};
    }

private:
    void release() {
        if (raw_) {
            raw_->Release();
            raw_ = nullptr;
        }
    }

    static ActionResult outcome_for(const std::string& mechanism, HRESULT) {
        return ActionResult{mechanism, false,
                            "Komut arayüz desenine gönderildi; sonucu ekran görüntüsüyle doğrula."};
    }

    template <typename Pattern>
    bool query(Pattern** out, long long id) const {
        *out = nullptr;
        if (!raw_) return false;
        IUnknown* base = nullptr;
        const HRESULT hr =
            raw_->GetCurrentPattern(static_cast<PATTERNID>(id), reinterpret_cast<IUnknown**>(&base));
        if (FAILED(hr) || !base) return false;
        const HRESULT q = base->QueryInterface(IID_PPV_ARGS(out));
        base->Release();
        return SUCCEEDED(q) && *out;
    }

    bool holds_pattern(long long id) const {
        if (!raw_) return false;
        IUnknown* base = nullptr;
        const HRESULT hr =
            raw_->GetCurrentPattern(static_cast<PATTERNID>(id), reinterpret_cast<IUnknown**>(&base));
        if (base) base->Release();
        return SUCCEEDED(hr) && base != nullptr;
    }

    template <typename Getter>
    std::string string_property(Getter getter) const {
        if (!raw_) return {};
        BSTR raw = nullptr;
        std::string out;
        if (SUCCEEDED((raw_->*getter)(&raw))) out = util::trim(bstr_text(raw));
        if (raw) SysFreeString(raw);
        return out;
    }

    template <typename Getter>
    bool bool_property(Getter getter) const {
        if (!raw_) return false;
        BOOL value = VARIANT_FALSE;
        if (SUCCEEDED((raw_->*getter)(&value))) return value == VARIANT_TRUE;
        return false;
    }

    long long toggle_state() const {
        IUIAutomationTogglePattern* pattern = nullptr;
        if (!query(&pattern, UIA_TogglePatternId)) return -1;
        ToggleState state = ToggleState_Off;
        const HRESULT hr = pattern->get_CurrentToggleState(&state);
        pattern->Release();
        return FAILED(hr) ? -1 : static_cast<long long>(state);
    }

    // Kaydırma yüzdesi okunamazsa -1 döner; doğrulama ancak iki okuma da
    // geçerliyse ve farklıysa yapılır.
    static double scroll_percent(IUIAutomationScrollPattern& pattern, bool vertical) {
        double percent = -1.0;
        const HRESULT hr =
            vertical ? pattern.get_CurrentVerticalScrollPercent(&percent)
                     : pattern.get_CurrentHorizontalScrollPercent(&percent);
        return FAILED(hr) ? -1.0 : percent;
    }

    IUIAutomationElement* raw_ = nullptr;
};

// --- ağaç gezimi -------------------------------------------------------------

constexpr std::size_t kMaxScan = 6000;

// ControlViewWalker ile özyinelemesiz ön sırada gezim; Python descendants()
// sırasına en yakın olan budur. Tarama tavanı aşılırsa durur. screen_wait kendi
// daha küçük tavanıyla (400) gelir; Python'daki `index >= 400` kırpmasıdır.
std::vector<WinElement*> walk(IUIAutomationElement* root, std::size_t limit = kMaxScan) {
    std::vector<WinElement*> out;
    if (!root) return out;
    IUIAutomationTreeWalker* walker = nullptr;
    IUIAutomation* api = uia_api();
    if (!api || FAILED(api->get_ControlViewWalker(&walker)) || !walker) return out;
    std::vector<IUIAutomationElement*> pending;
    IUIAutomationElement* first = nullptr;
    if (SUCCEEDED(walker->GetFirstChildElement(root, &first)) && first) pending.push_back(first);
    while (!pending.empty() && out.size() < limit) {
        IUIAutomationElement* current = pending.back();
        pending.pop_back();
        out.push_back(new WinElement(current));
        IUIAutomationElement* sibling = nullptr;
        if (SUCCEEDED(walker->GetNextSiblingElement(current, &sibling)) && sibling)
            pending.push_back(sibling);
        IUIAutomationElement* child = nullptr;
        if (SUCCEEDED(walker->GetFirstChildElement(current, &child)) && child)
            pending.push_back(child);
    }
    for (IUIAutomationElement* leftover : pending) leftover->Release();
    walker->Release();
    return out;
}

// Python key'ında left//2, top//2 tabana böler; C++ '/' sıfıra yuvarlar. Çok
// monitörlü negatif koordinatta ayrımı korumak için taban bölüş kullanılır.
long long floor_half(long long value) { return value >= 0 ? value / 2 : -(((-value) + 1) / 2); }

// Ayırt anahtarı: normalize edilmiş ad + tür + yarı-konum (Python _collect_uia).
std::string element_key(const std::string& name, const std::string& control_type,
                        const std::array<long long, 4>& rect) {
    return normalize_text(name) + "\x1f" + control_type + "\x1f" +
           std::to_string(floor_half(rect[0])) + "\x1f" + std::to_string(floor_half(rect[1]));
}

// Pencereyi çözer ve öne getirir; engellerde ActionForbidden fırlatır.
struct ResolvedWindow {
    WindowInfo info;
    IUIAutomationElement* root = nullptr;
    std::vector<WinElement*> elements;

    ResolvedWindow() = default;
    ResolvedWindow(const ResolvedWindow&) = delete;
    ResolvedWindow& operator=(const ResolvedWindow&) = delete;
    ResolvedWindow(ResolvedWindow&& other) noexcept
        : info(std::move(other.info)), root(other.root), elements(std::move(other.elements)) {
        other.root = nullptr;
    }
    ResolvedWindow& operator=(ResolvedWindow&&) = delete;
    ~ResolvedWindow() {
        for (WinElement* element : elements) delete element;
        if (root) root->Release();
    }
};

void require_foreground(const WindowInfo& info) {
    if (const auto reason = secure_desktop_reason(info.title, info.process)) {
        throw ai::ActionForbidden(*reason);
    }
    if (!focus_window_handle(info.handle)) {
        throw ai::ActionForbidden(
            "Pencere öne getirilemedi; yanlış uygulamaya tıklayıp yazmayı önlemek için durduruldu.");
    }
}

ResolvedWindow resolve_window(const std::string& wanted) {
    const std::vector<WindowInfo> windows = collect_windows();
    std::vector<std::string> titles;
    titles.reserve(windows.size());
    for (const WindowInfo& window : windows) titles.push_back(window.title);
    const auto selection = select_title_match(titles, wanted);
    if (const auto* error = std::get_if<std::string>(&selection)) {
        throw ai::ActionForbidden(*error);
    }
    ResolvedWindow resolved;
    resolved.info = windows[std::get<std::size_t>(selection)];
    require_foreground(resolved.info);
    IUIAutomation* api = uia_api();
    if (!api) throw ai::ActionForbidden("UI Automation başlatılamadı; Windows UIA servisi kapalı.");
    const HRESULT hr =
        api->ElementFromHandle(reinterpret_cast<HWND>(resolved.info.handle), &resolved.root);
    if (FAILED(hr) || !resolved.root) {
        throw ai::ActionForbidden("Pencerenin arayüz ağacı okunamadı (UIA ElementFromHandle " +
                                 std::to_string(static_cast<long>(hr)) + ").");
    }
    resolved.elements = walk(resolved.root);
    return resolved;
}

// Ada göre öğe seçimi: önce tam (aksan katlanmış), olmazsa alt dizi; belirsizse hata.
WinElement* pick_element(ResolvedWindow& window, const std::string& wanted) {
    const std::string needle = normalize_text(wanted);
    std::vector<WinElement*> exact;
    std::vector<WinElement*> partial;
    for (WinElement* element : window.elements) {
        const std::string name = element->name();
        if (name.empty()) continue;
        const std::string folded = normalize_text(name);
        if (folded == needle)
            exact.push_back(element);
        else if (folded.find(needle) != std::string::npos)
            partial.push_back(element);
    }
    const std::vector<WinElement*>& matches = exact.empty() ? partial : exact;
    if (matches.empty()) throw ai::ActionForbidden("Arayüz öğesi bulunamadı: " + wanted);
    if (matches.size() > 1) {
        std::string names;
        for (std::size_t i = 0; i < matches.size() && i < 4; ++i) {
            if (!names.empty()) names += ", ";
            names += matches[i]->name();
        }
        throw ai::ActionForbidden("Arayüz öğesi belirsiz, daha tam ad kullan: " + names);
    }
    return matches.front();
}

// --- ekran metni bekleme ve OCR araçlarının yardımcıları ----------------------

// Python MAX_WAIT_SECONDS / WAIT_POLL_SECONDS / DESCENDANT_SCAN_SECONDS.
constexpr double kMaxWaitSeconds = 20.0;
constexpr auto kWaitPollInterval = std::chrono::milliseconds(250);
constexpr auto kDescendantScanInterval = std::chrono::seconds(1);
constexpr std::size_t kMaxWaitScan = 400;

double seconds_since(const std::chrono::steady_clock::time_point& from) {
    return std::chrono::duration<double>(std::chrono::steady_clock::now() - from).count();
}

// Python f"{timeout:.1f}" karşılığı: tek onalık, ondalık nokta ile.
std::string fixed1(double value) {
    char buffer[32];
    std::snprintf(buffer, sizeof(buffer), "%.1f", value);
    return buffer;
}

// self.ui._window(title) / _active_window() eşdeğeri. Python bulunamadığında
// istisna atar ve wait_for bunu yutarak turu atlar; burada hata yerine nullopt
// döner, böylece pencere açılana kadar beklemek mümkündür.
std::optional<WindowInfo> window_for_wait(const std::string& wanted) {
    const std::vector<WindowInfo> windows = collect_windows();
    if (windows.empty()) return std::nullopt;
    if (!wanted.empty()) {
        std::vector<std::string> titles;
        titles.reserve(windows.size());
        for (const WindowInfo& window : windows) titles.push_back(window.title);
        const auto selection = select_title_match(titles, wanted);
        if (std::holds_alternative<std::string>(selection)) return std::nullopt;
        return windows[std::get<std::size_t>(selection)];
    }
    for (const WindowInfo& window : windows) {
        if (window.foreground) return window;
    }
    return std::nullopt;
}

// Ekran görüntüsü/OCR açmadan UIA adlarında metin arar — Python'ın ilk 400
// descendants() taraması. Pencere ya da UIA ağacı okunamazsa "yok" der.
bool window_contains_text(long long handle, const std::string& query) {
    if (handle == 0 || query.empty()) return false;
    IUIAutomation* api = uia_api();
    if (!api) return false;
    IUIAutomationElement* root = nullptr;
    if (FAILED(api->ElementFromHandle(reinterpret_cast<HWND>(handle), &root)) || !root) return false;
    const std::vector<WinElement*> elements = walk(root, kMaxWaitScan);
    bool found = false;
    for (WinElement* element : elements) {
        const std::string name = normalize_text(element->name());
        if (!name.empty() && name.find(query) != std::string::npos) {
            found = true;
            break;
        }
    }
    for (WinElement* element : elements) delete element;
    root->Release();
    return found;
}

// grab_region'ın geçici PNG'si: OCR bittikten (ya da patladıktan) sonra silinir;
// cache_screen'in aksine model bu dosyayı görmez.
struct ScopedTempFile {
    std::filesystem::path path;

    ~ScopedTempFile() {
        std::error_code ec;
        std::filesystem::remove(path, ec);
    }
};

std::filesystem::path temp_ocr_path() {
    std::error_code ec;
    std::filesystem::path directory = std::filesystem::temp_directory_path(ec);
    if (ec || directory.empty()) directory = screen_cache_directory();
    const auto stamp = std::chrono::duration_cast<std::chrono::nanoseconds>(
                           std::chrono::system_clock::now().time_since_epoch())
                           .count();
    return directory / ("jarvis_ocr_" + std::to_string(stamp) + ".png");
}

// Denetim türü adına karşılık gelen UIA kimliği; sıralama süzgeci için sayıya
// gerek yok, ad kümesi yeter — ama satırda türü tutmak için kimlik de saklanır.
std::size_t control_type_id(const std::string& type) {
    static const std::map<std::string, std::size_t> kIds = {
        {"Button", 50000},     {"Calendar", 50001},   {"CheckBox", 50002},
        {"ComboBox", 50003},   {"Edit", 50004},       {"Hyperlink", 50005},
        {"Image", 50006},      {"ListItem", 50007},   {"List", 50008},
        {"Menu", 50009},       {"MenuBar", 50010},    {"MenuItem", 50011},
        {"ProgressBar", 50012}, {"RadioButton", 50013}, {"ScrollBar", 50014},
        {"Slider", 50015},     {"Spinner", 50016},    {"StatusBar", 50017},
        {"Tab", 50018},        {"TabItem", 50019},    {"Text", 50020},
        {"ToolBar", 50021},    {"ToolTip", 50022},    {"Tree", 50023},
        {"TreeItem", 50024},   {"Custom", 50025},     {"Group", 50026},
        {"Thumb", 50027},      {"DataGrid", 50028},   {"DataItem", 50029},
        {"Document", 50030},   {"SplitButton", 50031}, {"Window", 50032},
        {"Pane", 50033},       {"Header", 50034},     {"HeaderItem", 50035},
        {"Table", 50036},      {"TitleBar", 50037},   {"Separator", 50038},
        {"SemanticZoom", 50039}, {"AppBar", 50040},
    };
    const auto found = kIds.find(type);
    return found == kIds.end() ? 0 : found->second;
}

// Bir öğenin JSON satırı — yalnızca listeye giren satırlar için kurulur, böylece
// desen sorguları (öğe başına 7 COM çağrısı) kırpma sonrası sayıya iner.
struct InspectRow {
    WinElement* element = nullptr;
    std::array<long long, 4> rect{};
    std::string name;
    std::string type;
    std::size_t type_id = 0;
    bool secure = false;
    bool password = false;

    bool interactive() const { return is_interactive_type(type); }

    json to_json() const {
        json row{
            {"name", name},
            {"type", type},
            {"control_type_id", type_id},
            {"automation_id", element->automation_id()},
            {"class_name", element->class_name()},
            {"center_x", (rect[0] + rect[2]) / 2},
            {"center_y", (rect[1] + rect[3]) / 2},
            {"enabled", element->enabled()},
            {"is_password", password},
            {"secure", secure},
        };
        json patterns = json::array();
        for (const std::string& pattern_name : element->patterns()) {
            patterns.push_back(pattern_name);
        }
        row["patterns"] = patterns;
        return row;
    }
};

}  // namespace

// ------------------------------------------------------------------ inspect_ui

std::string UiAutomation::inspect_ui(const std::string& window_title, long long limit,
                                    const std::string& name_filter) {
    if (util::trim(window_title).empty()) {
        throw ai::ActionForbidden("inspect_ui için window_title gerekli.");
    }
    limit = std::min<long long>(std::max<long long>(limit, 1), 220);
    ResolvedWindow window = resolve_window(window_title);

    std::array<long long, 4> content{0, 0, 0, 0};
    for (std::size_t i = 0; i < content.size() && i < window.info.rect.size(); ++i) {
        content[i] = window.info.rect[i];
    }
    const long long window_area =
        std::max<long long>(1, (content[2] - content[0]) * (content[3] - content[1]));
    const std::string needle = normalize_text(name_filter);

    std::vector<InspectRow> rows;
    std::set<std::string> seen;

    for (WinElement* element : window.elements) {
        const std::string name = element->name();
        const std::string type = element->control_type();
        const std::size_t type_id = control_type_id(type);
        const bool password = type == "Edit" || type == "Document" ? element->is_password() : false;
        const bool secure = is_secure_element(name, type, password);
        if (!is_interactive_type(type) && !secure) continue;
        if (element->offscreen()) continue;
        const auto bounds = element->bounds();
        if (!bounds) continue;
        std::string shown = name;
        if (shown.empty()) {
            if (!secure) continue;
            shown = "(gizli alan)";
        }
        if (!needle.empty() && normalize_text(shown).find(needle) == std::string::npos) continue;
        if (!seen.insert(element_key(shown, type, *bounds)).second) continue;
        rows.push_back(InspectRow{
            element, *bounds, util::utf8_take(shown, 200), util::utf8_take(type, 80), type_id, secure,
            password});
    }

    // Python inspect: content_rect = adi boş en büyük Pane, pencere alanının
    // en az %20'si kadar. Etkileşim satırlarından bağımsız ikinci tur.
    long long best_area = 0;
    std::array<long long, 4> content_rect{};
    bool have_content_rect = false;
    for (WinElement* element : window.elements) {
        if (!element->name().empty() || element->control_type() != "Pane") continue;
        const auto bounds = element->bounds();
        if (!bounds) continue;
        const long long width = (*bounds)[2] - (*bounds)[0];
        const long long height = (*bounds)[3] - (*bounds)[1];
        const long long area = width > 0 && height > 0 ? width * height : 0;
        if (area > best_area && area * 5 >= window_area) {
            best_area = area;
            content_rect = *bounds;
            have_content_rect = true;
        }
    }

    // Etkileşimli öğeler önce: Python rows.sort(key=type not in interactive).
    std::stable_sort(rows.begin(), rows.end(), [](const InspectRow& a, const InspectRow& b) {
        return a.interactive() && !b.interactive();
    });
    if (rows.size() > static_cast<std::size_t>(limit)) rows.resize(static_cast<std::size_t>(limit));

    json controls = json::array();
    for (const InspectRow& row : rows) controls.push_back(row.to_json());

    json details{
        {"window", window.info.title},
        {"window_rect", json::array({content[0], content[1], content[2], content[3]})},
    };
    std::string note =
        "Tıklama için center_x/center_y gerçek ekran koordinatlarıdır; etkileşimli öğeler önce "
        "listelenir.";
    if (have_content_rect) {
        details["content_rect"] =
            json::array({content_rect[0], content_rect[1], content_rect[2], content_rect[3]});
        note += " content_rect büyük içerik/çizim alanıdır; çizimi onun içinde yap.";
    }
    if (rows.empty()) {
        details["uia_empty"] = true;
        note +=
            " Bu pencere UIA denetimleri sunmuyor (Steam, tarayıcı veya oyun olabilir)."
            " Metin düğmeler için find_text_on_screen kullan; görsel hedefler için capture_screen + "
            "mouse_control kullan ve her tıklamadan sonra yeniden capture_screen ile doğrula.";
    }
    details["note"] = note;
    details["controls"] = controls;
    return details.dump();
}

// ------------------------------------------------------------------ ui_control

std::string UiAutomation::control(const json& args) {
    const std::string action = ascii_lower(util::trim(arg_str(args, "action")));
    const std::string title = arg_str(args, "window_title");
    const std::string wanted = arg_str(args, "control_name");
    const std::string text = arg_str(args, "text");
    if (title.empty()) throw ai::ActionForbidden("ui_control için window_title gerekli.");
    if (wanted.empty()) throw ai::ActionForbidden("ui_control için control_name gerekli.");
    if (!is_ui_action(action)) {
        throw ai::ActionForbidden("Desteklenmeyen arayüz eylemi: " + arg_str(args, "action"));
    }

    ResolvedWindow window = resolve_window(title);
    WinElement* target = pick_element(window, wanted);

    // Güvenlik geçidi: parola alanına hiçbir yazma/tıklama gitmez; güvenli
    // masaüstü engelini zaten resolve_window tüm araçtan önce reddetti.
    const std::string kind = target->control_type();
    if (const auto blocked =
            control_block_reason(action, target->name(), kind, target->is_password())) {
        throw ai::ActionForbidden(*blocked);
    }
    if (action == "type" || action == "set_value") {
        if (text.empty()) throw ai::ActionForbidden("type/set_value için text gerekli.");
    }

    WinInput input;
    std::array<long long, 4> rect{};
    if (const auto bounds = target->bounds()) rect = *bounds;
    const std::string direction = arg_str(args, "direction", "down");
    const long long amount = arg_num(args, "amount", 3);

    ControlOutcome result =
        apply_control_action(*target, input, action, text, direction.empty() ? "down" : direction,
                             amount);

    json out{
        {"window", window.info.title},
        {"control", target->name()},
        {"type", kind},
        {"action", action},
        {"mechanism", result.mechanism},
        {"verified", result.verified},
        {"delivery", result.delivery},
        {"retry", result.retry},
        {"control_rect", json::array({rect[0], rect[1], rect[2], rect[3]})},
        {"center_x", (rect[0] + rect[2]) / 2},
        {"center_y", (rect[1] + rect[3]) / 2},
        {"note", result.note},
    };
    if (!target->read_back().empty()) out["value"] = target->read_back();
    return out.dump();
}

// ------------------------------------------------------------------ send_keys

std::string UiAutomation::keys(const json& args) {
    const std::string title = arg_str(args, "window_title");
    const std::string spec = arg_str(args, "keys");
    const std::string text = arg_str(args, "text");
    if (title.empty()) throw ai::ActionForbidden("send_keys için window_title gerekli.");
    if (spec.empty() && text.empty()) {
        throw ai::ActionForbidden("Gönderilecek kısayol veya metin gerekli.");
    }
    std::vector<KeyToken> tokens;
    if (!spec.empty()) {
        const auto parsed = parse_key_sequence(spec);
        if (const auto* error = std::get_if<std::string>(&parsed)) {
            throw ai::ActionForbidden(*error);
        }
        tokens = std::get<std::vector<KeyToken>>(parsed);
    }

    ResolvedWindow window = resolve_window(title);
    WinInput input;
    if (!tokens.empty()) input.tokens(tokens);
    std::string delivery = tokens.empty() ? "none" : "sendinput_unicode";
    if (!text.empty()) {
        // Python gibi: uzun metin panoyla yapıştırılır, tek tek yazılmaz.
        input.paste(text);
        delivery = tokens.empty() ? "clipboard_paste" : "sendinput_unicode+clipboard_paste";
    }
    json out{
        {"window", window.info.title},
        {"keys", spec},
        {"characters", util::utf8_length(text)},
        {"delivery", delivery},
        {"verified", false},
        {"retry", "unsafe"},
        {"note", "Klavye girişi pencereye gönderildi; sonucu ekran görüntüsüyle veya inspect_ui ile "
                 "doğrula."},
    };
    return out.dump();
}

// ---------------------------------------------------------- find_text_on_screen

std::string UiAutomation::find_text(const json& args) {
    const std::string query = util::trim(arg_str(args, "text"));
    if (query.empty()) throw ai::ActionForbidden("OCR için aranacak metin gerekli.");
    const bool click = arg_flag(args, "click");
    const std::string title = util::trim(arg_str(args, "window_title"));
    const std::string button = ascii_lower(util::trim(arg_str(args, "button", "left")));
    if (button != "left" && button != "right" && button != "middle") {
        throw ai::ActionForbidden("OCR tıklaması için button left/right/middle olmalı.");
    }

    // Python: başlık varsa pencere öne getirilir ve yalnızca dikdörtgeni yakalanır;
    // başlık yoksa bütün ekran taranır ama tıklama reddedilir.
    std::optional<std::array<long long, 4>> region;
    if (!title.empty()) {
        const ResolvedWindow window = resolve_window(title);
        const std::vector<long long>& rect = window.info.rect;
        if (rect.size() == 4 && rect[2] > rect[0] && rect[3] > rect[1]) {
            region = std::array<long long, 4>{rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1]};
        }
    } else if (click) {
        throw ai::ActionForbidden("OCR ile tıklamak için window_title zorunlu.");
    }

    if (!ocr_engine_available()) {
        throw ai::ActionForbidden(
            "Windows OCR motoru bulunamadı; Windows dil paketlerinden Türkçe veya İngilizce OCR ekleyin.");
    }

    PixelBuffer shot;
    std::string error;
    if (!grab_screen(region, shot, error)) throw ai::ActionForbidden(error);
    const ScopedTempFile temp{temp_ocr_path()};
    if (!write_png(shot, temp.path, error)) throw ai::ActionForbidden(error);
    json result =
        ScreenOcr::find(temp.path, query, shot.origin_x, shot.origin_y, arg_str(args, "language"));
    result["image_size"] = json::array({shot.width, shot.height});

    const std::optional<std::size_t> selected =
        ocr_selected_index(result["matches"], arg_num(args, "index", 0));
    result["selected_index"] = selected ? json(static_cast<long long>(*selected)) : json(nullptr);
    if (!click) return result.dump();
    if (!selected) {
        result["clicked"] = false;
        return result.dump();
    }
    const json& chosen = result["matches"][*selected];
    WinInput input;
    input.click_button(button, chosen["center_x"].get<long long>(),
                       chosen["center_y"].get<long long>());
    result["clicked"] = {{"x", chosen["center_x"]},
                         {"y", chosen["center_y"]},
                         {"text", chosen["text"]}};
    result["note"] = result["note"].get<std::string>() +
                     " Tıklama uygulandı; capture_screen ile sonucu doğrula.";
    return result.dump();
}

// ------------------------------------------------------------------ screen_wait

std::string UiAutomation::wait_for(const json& args) {
    const std::string title = util::trim(arg_str(args, "window_title"));
    const std::string text = util::trim(arg_str(args, "text"));
    if (title.empty() && text.empty()) {
        throw ai::ActionForbidden("screen_wait için text veya window_title gerekli.");
    }
    if (util::utf8_length(text) > 200) {
        throw ai::ActionForbidden("screen_wait metni en fazla 200 karakter olabilir.");
    }
    double timeout = arg_real(args, "timeout", 5.0);
    if (timeout == 0.0) timeout = 5.0;  // Python `or 5`: sıfır yanlış değer sayılır
    timeout = std::min(std::max(timeout, 0.5), kMaxWaitSeconds);
    const std::string query = normalize_text(text);

    const auto started = std::chrono::steady_clock::now();
    std::string current_title;
    long long checked_handle = 0;
    std::optional<std::chrono::steady_clock::time_point> last_scan;
    bool found = false;
    std::string detail;
    while (true) {
        const std::optional<WindowInfo> window = window_for_wait(title);
        if (window) {
            current_title = window->title;
            // Güvenli masaüstü denetimi her yeni pencerede bir kez; bulunursa
            // araç durur (Python da burada ActionForbidden atar).
            if (window->handle != 0 && window->handle != checked_handle) {
                if (const auto reason = secure_desktop_reason(window->title, window->process)) {
                    throw ai::ActionForbidden(*reason);
                }
                checked_handle = window->handle;
            }
            if (query.empty()) {
                found = true;
                detail = "Pencere bulundu: " + current_title;
            } else if (!current_title.empty() &&
                       normalize_text(current_title).find(query) != std::string::npos) {
                found = true;
                detail = "Metin pencere başlığında görüldü: " + text;
            } else if (!last_scan || std::chrono::steady_clock::now() - *last_scan >=
                                        kDescendantScanInterval) {
                last_scan = std::chrono::steady_clock::now();
                if (window_contains_text(window->handle, query)) {
                    found = true;
                    detail = "Metin arayüzde görüldü: " + text;
                }
            }
        }
        if (found || seconds_since(started) >= timeout) break;
        std::this_thread::sleep_for(kWaitPollInterval);
    }

    const long long elapsed_ms = std::llround(seconds_since(started) * 1000.0);
    json payload{
        {"window", current_title},
        {"text", text},
        {"found", found},
        {"elapsed_ms", elapsed_ms},
        {"timeout", timeout},
        {"detail",
         detail.empty() ? "'" + text + "' " + fixed1(timeout) + " saniye içinde bulunamadı." : detail},
        {"note",
         "screen_wait yalnızca UIA metnini okur; ekran görüntüsü/OCR açmaz. Bulunduysa eylemi "
         "yapmadan önce inspect_ui ile öğeyi doğrula. Bulunamadıysa süreyi artır veya "
         "find_text_on_screen ile görsel denetim yap."},
    };
    return payload.dump();
}

#else  // _WIN32

// UIA yalnızca Windows'ta vardır; diğer derlemelerde dürüstçe reddedilir.
std::string UiAutomation::inspect_ui(const std::string&, long long, const std::string&) {
    throw ai::ActionForbidden("Arayüz otomasyonu yalnızca Windows'ta çalışır.");
}
std::string UiAutomation::control(const json&) {
    throw ai::ActionForbidden("Arayüz otomasyonu yalnızca Windows'ta çalışır.");
}
std::string UiAutomation::keys(const json&) {
    throw ai::ActionForbidden("Arayüz otomasyonu yalnızca Windows'ta çalışır.");
}
std::string UiAutomation::find_text(const json&) {
    throw ai::ActionForbidden("Arayüz otomasyonu yalnızca Windows'ta çalışır.");
}
std::string UiAutomation::wait_for(const json&) {
    throw ai::ActionForbidden("Arayüz otomasyonu yalnızca Windows'ta çalışır.");
}

#endif  // _WIN32

}  // namespace jarvis::tools
