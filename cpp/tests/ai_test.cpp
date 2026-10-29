// AI motoru birim testi — ağa çıkmadan salt karar/derleme mantığını doğrular.
#include <chrono>
#include <cstdio>
#include <filesystem>
#include <string>
#include <vector>

#include "jarvis/ai/ai.hpp"
#include "jarvis/core/settings.hpp"
#include "jarvis/core/skill_library.hpp"
#include "jarvis/storage/storage.hpp"

static int failures = 0;
#define CHECK(cond)                                                        \
    do {                                                                   \
        if (!(cond)) {                                                     \
            std::printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond);    \
            ++failures;                                                    \
        }                                                                  \
    } while (0)

using namespace jarvis;
using namespace jarvis::ai;

int main() {
    // ---- model_rank / thinking_level_for ----
    CHECK(model_rank("gemini-3-flash").has_value());
    CHECK(!model_rank("gemini-2.5-flash-image").has_value());  // image => sohbet değil
    CHECK(!model_rank("gpt-4o").has_value());
    auto pro = model_rank("gemini-3-pro");
    CHECK(pro.has_value());
    if (pro) CHECK((*pro)[0] == 0);  // pro tier rank 0
    auto flash = model_rank("gemini-3-flash");
    if (flash) CHECK((*flash)[0] == 2);  // flash tier rank 2

    // thinking_level_for: 3.7/3.8 Flash "minimal" desteklemez => "low"
    CHECK(thinking_level_for("gemini-3.8-flash").value_or("") == "low");
    CHECK(thinking_level_for("gemini-3-flash").value_or("") == "minimal");
    CHECK(!thinking_level_for("gemini-3-flash-lite").has_value());
    CHECK(!thinking_level_for("gemini-2.5-flash").has_value());  // major<3 => ayar gönderme

    // ranked_models: en iyi üstte, exclude çalışır
    std::vector<std::string> models = {"gemini-2.5-flash", "gemini-3-pro", "gemini-3-flash",
                                       "gemini-3.8-flash", "gemini-2.5-flash-image"};
    auto ordered = ranked_models(models);
    CHECK(!ordered.empty());
    CHECK(ordered.front() == "gemini-3.8-flash");  // en yüksek (tier flash, major 3, minor 8)
    auto ordered2 = ranked_models(models, {"gemini-3.8-flash"});
    CHECK(ordered2.front() == "gemini-3-flash");
    CHECK(best_model(models, "gemini-3.8-flash").value_or("") == "gemini-3-flash");

    // ---- fold_tr ----
    CHECK(fold_tr("Çğı İÖŞü") == "cgi iosu");
    CHECK(fold_tr("MerhabA") == "merhaba");

    // ---- requires_action / handoff / gave_up / reflect ----
    CHECK(requires_action("Not Defteri'ni aç"));
    CHECK(!requires_action("Bu nasıl yapılır?"));  // bilgi sorusu => eylem değil
    CHECK(!requires_action("günaydın"));           // hiç eylem kökü yok
    CHECK(is_handoff("Bunu kendin yapman gerek."));
    CHECK(is_handoff("Lütfen manuel olarak dene."));
    CHECK(!is_handoff("Hemen hallediyorum."));
    CHECK(gave_up("Bu uygulama kurulu değil."));
    CHECK(!gave_up("Kurulum tamam."));
    CHECK(should_reflect("\xE2\x9D\x8C dosya bulunamadı"));  // ❌ öneki
    CHECK(!should_reflect("her şey yolunda"));
    CHECK(!should_reflect("\xE2\x9D\x8C işlem durduruldu"));  // kullanıcı kararı => reflect yok

    // ---- tool declarations gömülü ----
    const auto& tools = AI::tool_declarations();
    CHECK(tools.is_array());
    CHECK(tools.size() == 2);  // iki grup
    int decl_count = 0;
    for (const auto& g : tools) decl_count += static_cast<int>(g["functionDeclarations"].size());
    CHECK(decl_count == 61);

    // ---- izole Settings/Storage/SkillLibrary ile birleştirme ----
    namespace fs = std::filesystem;
    auto stamp = std::chrono::steady_clock::now().time_since_epoch().count();
    fs::path dir = fs::temp_directory_path() / ("jarvis_ai_test_" + std::to_string(stamp));
    fs::create_directories(dir);

    {
        core::Settings settings((dir / "settings.json").string());
        storage::Storage store((dir / "jarvis.db").string());
        core::SkillLibrary skills(dir / "skills", false);
        AI ai(settings, store, skills);

        // boş prompt erken çıkar
        CHECK(ai.ask("") == "");

        // api anahtarı yok => Gemini uyarı metni (ağ yok)
        std::string nokey = ai.ask("Merhaba Jarvis");
        CHECK(nokey.find("Gemini API anahtarı yok") != std::string::npos);

        // build_system_prompt varsayılanı içerir
        std::string sys = ai.build_system_prompt();
        CHECK(sys.find("Sen Jarvis") != std::string::npos);

        // bellek notu sistem istemine girer
        store.save_memory("kullanici_adi", "Mert");
        std::string sys2 = ai.build_system_prompt();
        CHECK(sys2.find("Kalıcı kullanıcı notları") != std::string::npos);
        CHECK(sys2.find("kullanici_adi") != std::string::npos);

        // build_contents yeni mesajı taşır
        store.add_message("user", "Önceki soru");
        store.add_message("assistant", "Önceki yanıt");
        std::string contents = ai.build_contents("Yeni istek", {});
        CHECK(contents.find("Kullanıcının yeni mesajı:\nYeni istek") != std::string::npos);
        CHECK(contents.find("Önceki konuşma") != std::string::npos);

        // "hatırla" komutu belleğe yazar (ağ gitmeden)
        std::string mem = ai.ask("hatırla sevilen_renk = turkuaz");
        CHECK(mem.find("Bilgi kaydedildi") != std::string::npos);
        CHECK(store.get_memory("sevilen_renk").value_or("") == "turkuaz");

        // Ollama sağlayıcısı: bağlantı yok => hata metni, çökme yok
        settings.save("ollama-test-key", "gemini-3-flash", "ollama", "http://127.0.0.1:1",
                      "yok-boyle-bir-model");
        std::string ollama_err = ai.ask("merhaba");
        CHECK(!ollama_err.empty());
        CHECK(ollama_err.find("Ollama") != std::string::npos);
    }

    fs::remove_all(dir);

    if (failures == 0) {
        std::printf("ai_test: TÜM TESTLER GEÇTİ\n");
        return 0;
    }
    std::printf("ai_test: %d HATA\n", failures);
    return 1;
}
