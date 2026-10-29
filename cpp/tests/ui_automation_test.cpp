// UiAutomation birim testi — klavye sözdizimi ayrıştırıcısı, eylem dağıtım
// sırası (sahte UiTarget/InputSink ile), parola geçidi ve güvenli masaüstü
// gerekçesi masaüstünden bağımsız doğrulanır. Canlı kısımda yalnızca okuma
// (inspect_ui) yapılır; Not Defteri açık değilse test atlanır, CI'da patlamaz.
#include <array>
#include <cstdio>
#include <functional>
#include <set>
#include <string>
#include <variant>
#include <vector>

#include "jarvis/ai/safety.hpp"
#include "jarvis/tools/ui_automation.hpp"
#include "jarvis/tools/window_tools.hpp"

static int failures = 0;
#define CHECK(cond)                                                      \
    do {                                                                 \
        if (!(cond)) {                                                   \
            std::printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                  \
        }                                                                \
    } while (0)

using jarvis::ai::ActionForbidden;
using jarvis::tools::ActionResult;
using jarvis::tools::ControlOutcome;
using jarvis::tools::InputSink;
using jarvis::tools::KeyToken;
using jarvis::tools::UiAutomation;
using jarvis::tools::UiTarget;
using jarvis::tools::apply_control_action;
using jarvis::tools::parse_key_sequence;
using nlohmann::json;

// --- sahte öğe ve giriş ------------------------------------------------------

// Desen desteği kümesine göre davranan öğe: hangi desenin denendiğini calls
// listesine yazar, böylece Python'daki "önce UIA deseni" sırası doğrulanır.
class FakeTarget : public UiTarget {
public:
    std::set<std::string> supported;
    std::string label = "Gönder";
    std::string type = "Button";
    std::string value;
    bool password = false;
    bool focus_ok = true;
    std::array<long long, 4> rect{100, 200, 300, 400};
    std::vector<std::string> calls;

    std::string name() const override { return label; }
    std::string control_type() const override { return type; }
    std::string automation_id() const override { return "id1"; }
    std::string class_name() const override { return "Edit"; }
    bool enabled() const override { return true; }
    bool is_password() const override { return password; }
    bool offscreen() const override { return false; }
    std::optional<std::array<long long, 4>> bounds() const override { return rect; }
    std::vector<std::string> patterns() const override {
        return {supported.begin(), supported.end()};
    }
    bool supports(const std::string& name) const override { return supported.count(name) > 0; }
    std::string read_back() const override { return value; }

    bool set_focus() override {
        calls.push_back("focus");
        return focus_ok;
    }

    std::optional<ActionResult> invoke() override {
        return gate("invoke", ActionResult{"uia_invoke", false, "Invoke gönderildi."});
    }
    std::optional<ActionResult> toggle(const std::string&) override {
        return gate("toggle", ActionResult{"uia_toggle", true, "Durum geri okundu."});
    }
    std::optional<ActionResult> expand(const std::string& mode) override {
        return gate("expand_collapse", ActionResult{"uia_" + mode, true, "Durum geri okundu."});
    }
    std::optional<ActionResult> select() override {
        return gate("selection_item", ActionResult{"uia_select", true, "Seçili doğrulandı."});
    }
    std::optional<ActionResult> set_value(const std::string& text) override {
        auto result = gate("value", ActionResult{"uia_value", true, "Değer geri okundu."});
        if (result) value = text;
        return result;
    }
    std::optional<ActionResult> clear() override {
        auto result = gate("value", ActionResult{"uia_clear", true, "Alan boş geri okundu."});
        if (result) value.clear();
        return result;
    }
    std::optional<ActionResult> scroll(const std::string& direction, long long amount) override {
        auto result = gate("scroll", ActionResult{"uia_scroll", true, "Konum geri okundu."});
        if (result) calls.push_back("scroll:" + direction + ":" + std::to_string(amount));
        return result;
    }
    std::optional<ActionResult> scroll_into_view() override {
        return gate("scroll_item", ActionResult{"uia_scroll_into_view", false, "Kaydırma gönderildi."});
    }

private:
    std::optional<ActionResult> gate(const std::string& pattern_name, ActionResult action) {
        if (!supports(pattern_name)) return std::nullopt;
        calls.push_back(pattern_name);
        return action;
    }
};

// editor bağlanırsa gönderilen tuşlar öğenin geri okunan değerine işlenir;
// böylece yazma sonrası geri okuma doğrulaması gerçekten sınanır.
class FakeSink : public InputSink {
public:
    std::vector<std::string> events;
    FakeTarget* editor = nullptr;
    void move(long long x, long long y) override {
        events.push_back("move:" + std::to_string(x) + "," + std::to_string(y));
    }
    void click(long long x, long long y) override {
        events.push_back("click:" + std::to_string(x) + "," + std::to_string(y));
    }
    void wheel(long long, long long, long long steps) override {
        events.push_back("wheel:" + std::to_string(steps));
    }
    void type(const std::string& utf8) override {
        events.push_back("type:" + utf8);
        if (editor) editor->value += utf8;
    }
    void shortcut(const std::string& spec) override {
        events.push_back("shortcut:" + spec);
        if (editor && spec == "^a{DEL}") editor->value.clear();
    }
};

static void expect_forbidden(const std::function<void()>& run, const std::string& fragment) {
    try {
        run();
        std::printf("FAIL beklenen red yaşanmadı (%s bekleniyordu)\n", fragment.c_str());
        ++failures;
    } catch (const ActionForbidden& error) {
        const std::string message = error.what();
        if (message.find(fragment) == std::string::npos) {
            std::printf("FAIL red metninde '%s' yok: %s\n", fragment.c_str(), message.c_str());
            ++failures;
        }
    }
}

static std::vector<KeyToken> tokens_of(const std::string& spec) {
    const auto parsed = parse_key_sequence(spec);
    if (const auto* error = std::get_if<std::string>(&parsed)) {
        std::printf("FAIL ayrıştırma hatası '%s': %s\n", spec.c_str(), error->c_str());
        ++failures;
        return {};
    }
    return std::get<std::vector<KeyToken>>(parsed);
}

static std::string error_of(const std::string& spec) {
    const auto parsed = parse_key_sequence(spec);
    if (const auto* text = std::get_if<std::string>(&parsed)) return *text;
    std::printf("FAIL ayrıştırıcı hata vermeliydi: %s\n", spec.c_str());
    ++failures;
    return {};
}

int main() {
    // --- normalize_text (pencere ve öğe adlarında ortak kullanılıyor) ---
    CHECK(jarvis::tools::normalize_text("  Not Defteri \t ") == "not defteri");
    // Aksanlar Python ile aynı şekilde düşer: ü→u, ı→i (NFKD + casefold).
    CHECK(jarvis::tools::normalize_text("Dosya\tDüzenle  Yardım") == "dosya duzenle yardim");
    CHECK(jarvis::tools::normalize_text("PAROLA  Alanı") == "parola alani");

    // --- parola / güvenli masaüstü geçitleri ---
    CHECK(jarvis::tools::is_secure_element("Herhangi", "Edit", true));  // IsPassword işareti
    CHECK(jarvis::tools::is_secure_element("Parola", "Edit", false));   // addan
    CHECK(jarvis::tools::is_secure_element("Şifre", "Document", false));
    CHECK(jarvis::tools::is_secure_element("PIN Kodu", "Edit", false));
    CHECK(!jarvis::tools::is_secure_element("Arama", "Edit", false));   // normal alan
    CHECK(!jarvis::tools::is_secure_element("Parola", "Button", false));  // tür edit değil
    CHECK(jarvis::tools::control_block_reason("type", "Parola", "Edit", true).has_value());
    CHECK(jarvis::tools::control_block_reason("click", "Parola", "Edit", false).has_value());
    CHECK(jarvis::tools::control_block_reason("check", "Parola", "Edit", false).has_value());
    CHECK(!jarvis::tools::control_block_reason("focus", "Parola", "Edit", true).has_value());
    CHECK(!jarvis::tools::control_block_reason("hover", "Parola", "Edit", true).has_value());
    CHECK(!jarvis::tools::control_block_reason("type", "Dosya Adı", "Edit", false).has_value());
    // güvenli masaüstü: UAC başlığı (TR/EN), onay süreci ve normal pencere üçlüsü
    CHECK(jarvis::tools::secure_desktop_reason("Kullanıcı Hesabı Denetimi", "notepad.exe").has_value());
    CHECK(jarvis::tools::secure_desktop_reason("User Account Control", "explorer.exe").has_value());
    CHECK(jarvis::tools::secure_desktop_reason("Herhangi", "consent.exe").has_value());
    CHECK(!jarvis::tools::secure_desktop_reason("Adsız - Not Defteri", "notepad.exe").has_value());

    // --- send_keys sözdizimi ---
    const auto ctrl_k = tokens_of("^k");
    CHECK(ctrl_k.size() == 1);
    CHECK(!ctrl_k.empty() && ctrl_k.front().ctrl && ctrl_k.front().utf8 == "k");
    CHECK(!ctrl_k.empty() && !ctrl_k.front().virtual_key);

    const auto enter = tokens_of("{ENTER}");
    CHECK(enter.size() == 1);
    CHECK(!enter.empty() && enter.front().virtual_key && enter.front().vk == 0x0D);

    const auto mixed = tokens_of("a{ENTER}b");
    CHECK(mixed.size() == 3);
    CHECK(mixed.size() == 3 && mixed[0].utf8 == "a" && mixed[1].virtual_key && mixed[2].utf8 == "b");

    const auto three = tokens_of("{TAB 3}");
    CHECK(three.size() == 3);
    CHECK(!three.empty() && three.front().vk == 0x09);

    const auto shifted = tokens_of("+{F5}");
    CHECK(shifted.size() == 1);
    CHECK(!shifted.empty() && shifted.front().shift && shifted.front().vk == 0x74);

    const auto turkish = tokens_of("ş");
    CHECK(turkish.size() == 1);
    CHECK(!turkish.empty() && turkish.front().utf8 == "ş");

    CHECK(error_of("{FOO}").find("Bilinmeyen") != std::string::npos);
    CHECK(error_of("^{").find("Kapanmayan") != std::string::npos);
    CHECK(error_of("a^").find("değiştiricisi") != std::string::npos);
    CHECK(error_of("{ENTER 0}").find("yineleme") != std::string::npos);
    CHECK(error_of("{ENTER abc}").find("yineleme") != std::string::npos);

    // --- eylem dağıtım sırası: UIA deseni önce, olmazsa SetFocus + SendInput ---
    {
        FakeTarget target;
        FakeSink sink;
        target.supported = {"invoke"};
        const ControlOutcome outcome = apply_control_action(target, sink, "click");
        CHECK(outcome.mechanism == "uia_invoke");
        CHECK(outcome.delivery == "delivered_unverified");
        CHECK(outcome.retry == "unsafe");
        CHECK(sink.events.empty());  // desen yeterli, fareye gerek yok
    }
    {
        FakeTarget target;
        FakeSink sink;
        target.supported = {"toggle"};  // invoke yok → toggle denenir
        const ControlOutcome outcome = apply_control_action(target, sink, "click");
        CHECK(outcome.mechanism == "uia_toggle");
        CHECK(target.calls == std::vector<std::string>{"toggle"});
    }
    {
        FakeTarget target;
        FakeSink sink;
        target.supported = {"selection_item"};
        const ControlOutcome outcome = apply_control_action(target, sink, "click");
        CHECK(outcome.mechanism == "uia_select");
    }
    {
        FakeTarget target;
        FakeSink sink;  // hiç desen yok → odak + merkez tıklaması
        const ControlOutcome outcome = apply_control_action(target, sink, "click");
        CHECK(outcome.mechanism == "mouse_click");
        CHECK(target.calls == std::vector<std::string>{"focus"});
        CHECK(sink.events == std::vector<std::string>{"click:200,300"});
    }
    {
        FakeTarget target;
        FakeSink sink;
        target.focus_ok = false;
        expect_forbidden([&] { apply_control_action(target, sink, "click"); }, "odaklanamadı");
        expect_forbidden([&] { apply_control_action(target, sink, "type", "x"); }, "odaklanamadı");
    }
    {
        FakeTarget target;
        FakeSink sink;
        target.type = "Edit";
        target.supported = {"value"};
        const ControlOutcome outcome = apply_control_action(target, sink, "type", "merhaba");
        CHECK(outcome.mechanism == "uia_value");
        CHECK(outcome.verified);
        CHECK(outcome.delivery == "delivered");
        CHECK(outcome.retry == "safe");
        CHECK(sink.events.empty());
    }
    {
        FakeTarget target;
        FakeSink sink;  // Value deseni yok → klavye düşüşü
        target.type = "Edit";
        target.supported = {"invoke"};
        sink.editor = &target;
        const ControlOutcome outcome = apply_control_action(target, sink, "type", "merhaba");
        CHECK(outcome.mechanism == "keyboard_unicode");
        CHECK(outcome.verified);  // sahte öğe yazılan değeri geri okuyor
        CHECK(sink.events == std::vector<std::string>{"type:merhaba"});
        CHECK(target.calls == std::vector<std::string>{"focus"});
    }
    {
        FakeTarget target;
        FakeSink sink;
        sink.editor = &target;
        target.value = "dolu";  // Value deseni yok → kısayolla temizleme
        const ControlOutcome outcome = apply_control_action(target, sink, "clear");
        CHECK(outcome.mechanism == "keyboard_clear");
        CHECK(outcome.verified);
        CHECK(outcome.retry == "safe");
        CHECK(sink.events == std::vector<std::string>{"shortcut:^a{DEL}"});
    }
    {
        FakeTarget target;
        FakeSink sink;
        target.supported = {"scroll"};
        const ControlOutcome outcome = apply_control_action(target, sink, "scroll", "", "down", 4);
        CHECK(outcome.mechanism == "uia_scroll");
        CHECK((target.calls == std::vector<std::string>{"scroll", "scroll:down:4"}));
        CHECK(sink.events.empty());
    }
    {
        FakeTarget target;
        FakeSink sink;  // ScrollPattern yok → fare tekerleği
        const ControlOutcome outcome = apply_control_action(target, sink, "scroll", "", "up", 2);
        CHECK(outcome.mechanism == "mouse_wheel");
        CHECK(sink.events == std::vector<std::string>{"wheel:2"});
    }
    {
        FakeTarget target;
        FakeSink sink;
        expect_forbidden([&] { apply_control_action(target, sink, "scroll", "", "left"); },
                         "up veya down");
        expect_forbidden([&] { apply_control_action(target, sink, "dubble_click"); },
                         "Desteklenmeyen arayüz eylemi");
        expect_forbidden([&] { apply_control_action(target, sink, "type", ""); }, "text gerekli");
        expect_forbidden([&] { apply_control_action(target, sink, "check"); }, "işaretleme");
        expect_forbidden([&] { apply_control_action(target, sink, "expand"); },
                         "açma/kapama desteklemiyor");
        expect_forbidden([&] { apply_control_action(target, sink, "select"); },
                         "seçim desteklemiyor");
        expect_forbidden([&] { apply_control_action(target, sink, "scroll_to"); },
                         "görünür alana kaydırmayı desteklemiyor");
    }
    {
        FakeTarget target;
        FakeSink sink;
        const ControlOutcome outcome = apply_control_action(target, sink, "hover");
        CHECK(outcome.mechanism == "mouse_hover");
        CHECK(outcome.retry == "safe");
        CHECK(sink.events == std::vector<std::string>{"move:200,300"});  // tıklama yok
    }
    {
        CHECK(jarvis::tools::is_ui_action("Set_Value"));
        CHECK(jarvis::tools::is_ui_action(" scroll_to "));
        CHECK(!jarvis::tools::is_ui_action("kill_process"));
        CHECK(jarvis::tools::is_interactive_type("Edit"));
        CHECK(!jarvis::tools::is_interactive_type("Pane"));
    }

    // --- canlı okuma: Not Defteri yoksa atlanır, asla başarısız sayılmaz ---
    try {
        const std::string result = UiAutomation().inspect_ui("Not Defteri", 10, "");
        const json details = json::parse(result, nullptr, false);
        if (details.is_discarded()) {
            std::printf("FAIL inspect_ui JSON üretmedi\n");
            ++failures;
        } else {
            CHECK(details.contains("window"));
            CHECK(details["controls"].is_array());
            // content_rect Python kuralıyla üretilir: adi boş en büyük Pane.
            // Not Defteri'nin içerik Pane'inin adı metnin kendisi olduğu için burada
            // üretilmeyebilir; var ise şeklini denetliyoruz.
            if (details.contains("content_rect")) {
                CHECK(details["content_rect"].is_array());
                CHECK(details["content_rect"].size() == 4);
            }
            for (const auto& row : details["controls"]) {
                CHECK(row.contains("center_x"));
                CHECK(row.contains("center_y"));
                CHECK(row["patterns"].is_array());
            }
            std::printf("inspect_ui canlı: %zu öğe listelendi, content_rect=%s\n",
                        static_cast<std::size_t>(details["controls"].size()),
                        details.contains("content_rect") ? "var" : "yok");
        }
    } catch (const ActionForbidden& error) {
        std::printf("inspect_ui atlandı (pencere yok ya da öne gelmedi): %s\n", error.what());
    } catch (const std::exception& error) {
        std::printf("inspect_ui atlandı: %s\n", error.what());
    }

    if (failures == 0) std::printf("ui_automation_test OK\n");
    return failures == 0 ? 0 : 1;
}
