#include <chrono>
#include <cstdio>
#include <filesystem>
#include <string>

#include <jarvis/core/history.hpp>
#include <jarvis/core/settings.hpp>
#include <jarvis/core/skill_library.hpp>
#include <jarvis/core/util.hpp>

static int failures = 0;

#define CHECK(cond)                                                          \
    do {                                                                     \
        if (!(cond)) {                                                       \
            std::printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond);      \
            ++failures;                                                      \
        }                                                                    \
    } while (0)

int main() {
    using namespace jarvis::core;
    namespace fs = std::filesystem;

    const auto stamp = std::chrono::steady_clock::now().time_since_epoch().count();
    const fs::path dir = fs::temp_directory_path() / ("jarvis_cpp_test_" + std::to_string(stamp));
    fs::create_directories(dir);
    const fs::path file = dir / "settings.json";

    // --- varsayılanlar ---
    {
        Settings s(file);
        CHECK(s.provider() == "gemini");
        CHECK(s.model() == std::string(kDefaultModel));
        CHECK(s.voice_replies());
        CHECK(!s.screen_context());
        CHECK(s.access_mode() == "approval");
        CHECK(s.companion_interval() == 90);
        CHECK(s.voice_style() == "jarvis");
        CHECK(s.theme() == "black");
    }

    // --- sesli yanıt kalıcılığı ---
    {
        Settings s(file);
        s.set_voice_replies(false);
        Settings reloaded(file);
        CHECK(!reloaded.voice_replies());
        s.set_voice_replies(true);
        CHECK(Settings(file).voice_replies());
    }

    // --- ses/kamera sıkıştırma ---
    {
        Settings s(file);
        s.set_audio("  USB Mikrofon  ", "Kulaklık", 99, "bilinmeyen", -99, 999, false);
        CHECK(s.mic_device() == "USB Mikrofon");
        CHECK(s.speaker_device() == "Kulaklık");
        CHECK(s.camera_index() == 16);
        CHECK(s.voice_style() == "jarvis");
        CHECK(s.voice_rate() == -20);
        CHECK(s.voice_volume() == 100);
        CHECK(!s.voice_headphones());
    }

    // --- eşlikçi sınırı ---
    {
        Settings s(file);
        s.set_companion(std::nullopt, 999);
        CHECK(s.companion_interval() == 480);
    }

    // --- saat normalizasyonu ---
    {
        CHECK(jarvis::util::normalize_clock_text("25:00", "23:00") == "23:00");
        CHECK(jarvis::util::normalize_clock_text("07:05", "23:00") == "07:05");
        CHECK(jarvis::util::normalize_clock_text("garbage", "23:00") == "23:00");
    }

    // --- tema normalizasyonu ---
    {
        CHECK(jarvis::util::normalize_theme("kırmızı") == "black");
        CHECK(jarvis::util::normalize_theme("forest") == "forest");
        CHECK(jarvis::util::normalize_theme("yok") == "black");
        CHECK(jarvis::util::normalize_theme("beyaz") == "white");
    }

    // --- ev konumu ---
    {
        Settings s(file);
        bool thrown = false;
        try {
            s.set_home(1000.0, 0.0);
        } catch (const std::invalid_argument&) {
            thrown = true;
        }
        CHECK(thrown);

        const HomeLocation home = s.set_home(40.757, 29.815, "Derince", "Kocaeli");
        CHECK(home.lat == 40.757);
        CHECK(home.lon == 29.815);
        CHECK(s.home_is_set());
        CHECK(Settings(file).home_location().name == "Derince");
    }

    // --- ChatMessage ---
    {
        ChatMessage message("user", "Merhaba", "chat");
        const nlohmann::json row = message.as_dict();
        const ChatMessage roundtrip = ChatMessage::from_row(row);
        CHECK(roundtrip.role == "user");
        CHECK(roundtrip.content == "Merhaba");
        CHECK(roundtrip.source == "chat");
    }

    // --- SkillLibrary ---
    {
        CHECK(slugify("Hata Ayikla!") == "hata-ayikla");
        CHECK(slugify("Çğı İÖŞü Test") == "cgi-iosu-test");
        CHECK(slugify("Günlük Plan") == "gunluk-plan");
        CHECK(slugify("...") == "");

        const auto parsed = parse_skill(
            "---\nname: test-skill\ndescription: Bir açıklama\n---\n\nGövde metni burada.\n",
            dir / "test-skill" / "SKILL.md");
        CHECK(parsed.has_value());
        CHECK(parsed->name == "test-skill");
        CHECK(parsed->description == "Bir açıklama");
        CHECK(parsed->body == "Gövde metni burada.");
        // Geçersiz: name kurala uymuyor.
        CHECK(!parse_skill("---\nname: A B\ndescription: x\n---\n\ngövde\n", dir).has_value());
        // Geçersiz: kapaticı '---' yok.
        CHECK(!parse_skill("---\nname: ok\ndescription: x\n\ngövde\n", dir).has_value());

        const fs::path skill_root = dir / "skills";
        SkillLibrary lib(skill_root, /*seed=*/true);
        // Başlangıç yetenekleri yazılmış olmalı.
        auto starters = lib.list_skills();
        CHECK(starters.size() == 4);
        CHECK(lib.get("hata-ayikla").has_value());

        const Skill created = lib.create("Yeni Yetenek", "Açıklama metni", "Adım 1\nAdım 2");
        CHECK(created.name == "yeni-yetenek");
        CHECK(lib.get("yeni yetenek").has_value());

        bool dup_threw = false;
        try {
            lib.create("Yeni Yetenek", "Açıklama", "Gövde", /*overwrite=*/false);
        } catch (const SkillError&) {
            dup_threw = true;
        }
        CHECK(dup_threw);

        CHECK(lib.list_skills().size() == 5);
        // index_text(2): ilk 2 yetenek + "daha var" işareti.
        CHECK(lib.index_text(2).find("daha var") != std::string::npos);
        // index_text(5): tümü, yeni-yetenek dahil.
        CHECK(lib.index_text(5).find("yeni-yetenek") != std::string::npos);

        const std::string read_out =
            lib.run(nlohmann::json{{"action", "read"}, {"name", "hata-ayikla"}});
        CHECK(read_out.find("# YETENEK: hata-ayikla") != std::string::npos);
        CHECK(lib.run(nlohmann::json{{"action", "list"}}).find("yetenek") != std::string::npos);

        bool bad_action = false;
        try {
            lib.run(nlohmann::json{{"action", "sil"}});
        } catch (const SkillError&) {
            bad_action = true;
        }
        CHECK(bad_action);
    }

    // --- UTF-8 sağlamlaştırması ---
    {
        using namespace jarvis::util;

        // Zaten geçerli olan UTF-8 aynen kalmalı (Türkçe ve emoji).
        const std::string tr = "Türkiye'nin başkenti Ankara'dır. 🤖";
        CHECK(utf8_sanitize(tr) == tr);

        // cp1254'ten sızmış tek baytlık Türkçe karakterler U+FFFD olmalı.
        const std::string poisoned = std::string("Tek c") + static_cast<char>(0xFC) +
                                     "mleyle s" + static_cast<char>(0xF6) + "yle";
        const std::string clean = utf8_sanitize(poisoned);
        CHECK(clean.find('\xFC') == std::string::npos);
        CHECK(clean.find('\xF6') == std::string::npos);
        CHECK(clean.find("\xEF\xBF\xBD") != std::string::npos);
        CHECK(utf8_sanitize(clean) == clean);  // ikinci geçişte değişmemeli

        // Aşırı kısa kodlama ve vekil aralığı bayt bayt reddedilmeli.
        const std::string replacement = "\xEF\xBF\xBD";
        CHECK(utf8_sanitize(std::string("\xC0\x80", 2)) == replacement + replacement);
        CHECK(utf8_sanitize(std::string("\xED\xA0\x80", 3)) ==
              replacement + replacement + replacement);
        // Kopuk devam baytı tek başına bozulmuş sayılır.
        CHECK(utf8_sanitize(std::string("a\xB2", 2)) == std::string("a\xEF\xBF\xBD", 4));

        // Komut satırı her koşulda geçerli UTF-8 döndürmeli.
        const std::vector<std::string> args = utf8_arguments(0, nullptr);
        for (const std::string& arg : args) CHECK(utf8_sanitize(arg) == arg);
    }

    std::error_code ec;
    fs::remove_all(dir, ec);

    if (failures == 0) {
        std::printf("core_test: TÜM TESTLER GEÇTİ\n");
        return 0;
    }
    std::printf("core_test: %d HATA\n", failures);
    return 1;
}
