// Ses hattı karar testi — ağa/Qt'ye çıkmadan metin biçimleme ve mikrofon mantığını doğrular.
#include <bit>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <string>
#include <utility>
#include <vector>

#include <nlohmann/json.hpp>

#include "jarvis/speech/pcm.hpp"
#include "jarvis/speech/stt.hpp"
#include "jarvis/speech/synth.hpp"
#include "jarvis/speech/text.hpp"
#include "jarvis/speech/vad.hpp"
#include "jarvis/speech/voice.hpp"

static int failures = 0;
#define CHECK(cond)                                                      \
    do {                                                                 \
        if (!(cond)) {                                                   \
            std::printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond);  \
            ++failures;                                                  \
        }                                                                \
    } while (0)

using namespace jarvis::speech;
namespace fs = std::filesystem;

namespace {

// UTF-8 kod noktası sayacı: Python len() karakter sayar, bayt değil.
int chars(const std::string& s) {
    int n = 0;
    for (unsigned char c : s) {
        if ((c & 0xC0) != 0x80) ++n;
    }
    return n;
}

bool ends_with(const std::string& s, const std::string& tail) {
    return s.size() >= tail.size() &&
           s.compare(s.size() - tail.size(), tail.size(), tail) == 0;
}

// VAD'yi yığın yığın besleyen test yardımcısı: 2048 baytlık (0.064 s) sabit yığın.
struct Feeder {
    Vad vad;
    Vad::Turn turn;
    std::string chunk = std::string(2048, '\0');
    double seconds = 0.064;

    explicit Feeder(double threshold) {
        turn.threshold = threshold;
        turn.chunk_seconds = seconds;
        turn.timeout = 5.0;  // sessizlik zaman aşımını testte kapalı tut
        vad.start_turn(turn);
    }

    VadEvent push(double rms, int count) {
        VadEvent last = VadEvent::Idle;
        for (int i = 0; i < count; ++i) last = vad.feed(rms, seconds, chunk);
        return last;
    }
};

// -EncodedCommand içeriğini çözmek için küçük base64 okuyucu.
std::string ascii_only(const std::wstring& w) {
    std::string s;
    s.reserve(w.size());
    for (wchar_t c : w) s.push_back(static_cast<char>(c));
    return s;
}

std::string from_base64(const std::string& text) {
    auto val = [](char c) -> int {
        if (c >= 'A' && c <= 'Z') return c - 'A';
        if (c >= 'a' && c <= 'z') return c - 'a' + 26;
        if (c >= '0' && c <= '9') return c - '0' + 52;
        if (c == '+') return 62;
        if (c == '/') return 63;
        return -1;
    };
    std::string out;
    int buffer = 0, bits = 0;
    for (char c : text) {
        int v = val(c);
        if (v < 0) break;
        buffer = (buffer << 6) | v;
        bits += 6;
        if (bits >= 8) {
            bits -= 8;
            out.push_back(static_cast<char>((buffer >> bits) & 0xFF));
        }
    }
    return out;
}

// --- PCM dönüştürücü yardımcıları: Qt tamponlarının küçük uç düzeni ---
void put_i16(std::string& out, int value) {
    out.push_back(static_cast<char>(value & 0xFF));
    out.push_back(static_cast<char>((value >> 8) & 0xFF));
}

void put_i32(std::string& out, long long value) {
    for (int shift = 0; shift < 32; shift += 8) {
        out.push_back(static_cast<char>((value >> shift) & 0xFF));
    }
}

void put_f32(std::string& out, float value) {
    const std::uint32_t bits = std::bit_cast<std::uint32_t>(value);
    for (int shift = 0; shift < 32; shift += 8) {
        out.push_back(static_cast<char>((bits >> shift) & 0xFF));
    }
}

int read_i16(const std::string& pcm, std::size_t index) {
    const unsigned char low = static_cast<unsigned char>(pcm[index * 2]);
    const signed char high = static_cast<signed char>(pcm[index * 2 + 1]);
    return (high << 8) | low;
}

// Tüm örnekler beklenen değere eşit mi? (izin verilen en büyük sapma ile)
bool all_equal(const std::string& pcm, int expected, int tolerance = 0) {
    const std::size_t count = pcm.size() / 2;
    for (std::size_t i = 0; i < count; ++i) {
        if (std::abs(read_i16(pcm, i) - expected) > tolerance) return false;
    }
    return count > 0;
}

constexpr double kPi = 3.14159265358979323846;

// Genlik korunması testi için 0,5 tepe değerli saf ses: 16-bit ölçeğinde
// etkin değer 0.5 * 32767 / sqrt(2) ≈ 11585 olmalı.
double tone_rms_target() { return 0.5 * 32767.0 / std::sqrt(2.0); }

// 48 kHz'de 100 örnekte bir yinelenen sinüs (480 Hz), 0,5 tepe.
float test_tone(int index) {
    return static_cast<float>(0.5 * std::sin(2.0 * kPi * double(index) / 100.0));
}

}  // namespace

int main() {
    // ---- fold_tr ----
    CHECK(fold_tr("Çğı İÖŞü") == "cgi iosu");
    CHECK(fold_tr("MerhabA") == "merhaba");

    // ---- clean_for_speech: markdown/kod/bağlantı ayıklama ----
    CHECK(clean_for_speech("Şunu dene: ```python\nprint(1)\n``` bitti.") == "Şunu dene: bitti.");
    CHECK(clean_for_speech("`tar -x` ile aç") == "tar -x ile aç");
    CHECK(clean_for_speech("Bak ![görsel](http://x/y.png) güzel.") == "Bak güzel.");
    CHECK(clean_for_speech("[GitHub](https://github.com) açıldı") == "GitHub açıldı");
    CHECK(clean_for_speech("https://example.com/a sayfası") == "sayfası");
    CHECK(clean_for_speech("**kalın** # başlık > alıntı ~") == "kalın başlık alıntı");
    CHECK(clean_for_speech("   boşluk\tve\nsatır   ") == "boşluk ve satır");
    CHECK(clean_for_speech("sessiz\x01 harf") == "sessiz harf");
    CHECK(clean_for_speech("") == "");

    std::string huge;
    for (int i = 0; i < 600; ++i) huge += "kelimeler ";
    std::string clipped = clean_for_speech(huge);
    CHECK(chars(clipped) <= kMaxSpeechChars + 1);
    CHECK(ends_with(clipped, "…"));

    // ---- stiller ve tempo ----
    CHECK(style_parts("bogus").voice == "en-US-AndrewMultilingualNeural");  // varsayılan jarvis
    CHECK(style_parts("  NATURAL ").voice == "tr-TR-EmelNeural");
    CHECK(style_label("warm").find("Ava") != std::string::npos);
    CHECK(style_rate_offset("male") == 22);
    CHECK(rate_text(-3, "jarvis") == "+21%");   // kullanıcı tempusu + karakter ofseti
    CHECK(rate_text(-3, "natural") == "+9%");
    CHECK(rate_text(20, "jarvis") == "+24%");   // üst sınır
    CHECK(rate_text(-30, "male") == "-8%");

    // ---- split_for_speech: cümleler max_chars'a kadar tek parçada toplanır ----
    std::string three = "Birinci cümle. İkinci cümle de burada! Üçüncü mü?";
    auto parts = split_for_speech(three);
    CHECK(parts.size() == 1);
    CHECK(parts.size() == 1 && parts.front() == three);
    auto tighter = split_for_speech(three, 30);
    CHECK(tighter.size() >= 2);
    for (const std::string& part : tighter) CHECK(chars(part) <= 30);

    std::string long_sentence = std::string(300, 'a');
    for (int i = 0; i < 300; i += 4) long_sentence[i] = ' ';
    auto wide = split_for_speech(long_sentence);
    CHECK(wide.size() >= 2);
    for (const std::string& part : wide) CHECK(chars(part) <= kMaxChunkChars);
    CHECK(split_for_speech("```\nkod yok\n```").empty());

    // ---- plan_chunks: ilk parça kısa, ses erken başlar ----
    std::string talk =
        "İlk cümle kısa kalsın ki ses hemen başlasın. İkinci cümle oldukça uzun ve "
        "birçok kelime içeriyor, böylece parçalanması gerekiyor. Üçüncü cümle.";
    auto chunks = plan_chunks(talk);
    CHECK(chunks.size() >= 2);
    if (!chunks.empty()) CHECK(chars(chunks.front()) <= kFirstChunkChars);
    for (const std::string& chunk : chunks) CHECK(!chunk.empty());

    std::string clause = std::string(60, 'x') + ", " + std::string(60, 'y') + ". Bitti.";
    auto clause_chunks = plan_chunks(clause);
    CHECK(!clause_chunks.empty());
    if (!clause_chunks.empty()) {
        CHECK(chars(clause_chunks.front()) <= kFirstChunkChars);
        CHECK(clause_chunks.size() >= 2);
    }

    // ---- concise_for_voice: sesli yanıt kısa kalır ----
    CHECK(concise_for_voice("Merhaba Jarvis.", 110) == "Merhaba Jarvis.");
    std::string chatter =
        "Bugün hava parçalı bulutlu ve sıcaklık yirmi üç derece. "
        "Rüzgar kuzeyden hafif esiyor. Akşam yağmur ihtimali var.";
    std::string brief = concise_for_voice(chatter, 110);
    CHECK(chars(brief) <= 110);
    CHECK(brief.find("parçalı bulutlu") != std::string::npos);
    std::string one_long = std::string(250, 'm');
    for (std::size_t i = 5; i < one_long.size(); i += 7) one_long[i] = ' ';
    CHECK(chars(concise_for_voice(one_long, 110)) <= 110);
    CHECK(concise_for_voice("", 110) == "");

    // ---- sesli istekte uzun anlatım sınırı ----
    CHECK(spoken_limit_for_prompt("hava nasıl?").value_or(0) == kVoiceReplyChars);
    CHECK(spoken_limit_for_prompt("tamam").value_or(0) == kVoiceReplyChars);
    CHECK(!spoken_limit_for_prompt("haberleri oku").has_value());
    CHECK(!spoken_limit_for_prompt("detaylı anlat").has_value());
    CHECK(!spoken_limit_for_prompt("listeyi açığa çıkar").has_value());
    CHECK(spoken_limit_for_prompt("kısaca ne oldu?", 40).value_or(0) == 40);

    // ---- seviye eğrisi ----
    CHECK(level_from_rms(30.0) == 0.0);
    CHECK(level_from_rms(kLevelFloor) == 0.0);
    CHECK(level_from_rms(kLevelCeil) > 0.99);
    double mid = level_from_rms(600.0);
    CHECK(mid > 0.4 && mid < 0.6);

    // ---- kelimeler ve söz kesme ----
    auto w = words_of("Not Defteri'ni aç!");
    CHECK(w.size() == 4);  // kesme işareti kelimeyi böler: not / defteri / ni / ac
    CHECK(should_accept_barge("sus"));
    CHECK(should_accept_barge("dur"));
    CHECK(!should_accept_barge("şşş"));     // tek kelime, durdurma sözü değil
    CHECK(!should_accept_barge(""));
    CHECK(should_accept_barge("hey Jarvis dur"));

    // ---- uyanma kelimesi onarımı ----
    CHECK(normalize_wake("carvis nasılsın") == "Jarvis nasılsın");
    CHECK(normalize_wake("hejiyan biz nasılsın") == "Jarvis nasılsın");
    CHECK(normalize_wake("hey jarvis gelir misin") == "Jarvis gelir misin");
    CHECK(normalize_wake("hava nasıl") == "hava nasıl");
    CHECK(normalize_wake("") == "");

    // ---- alternatif metin seçimi ----
    CHECK(pick_transcript(nlohmann::json(" merhaba ")) == "merhaba");
    // İç içe başlangıç kümesi dizide nesne üretmez; alternatifleri açıkça kur.
    nlohmann::json two = nlohmann::json::array();
    two.push_back(nlohmann::json{{"transcript", "karavaş gider"}});
    two.push_back(nlohmann::json{{"transcript", "Jarvis nerede"}});
    CHECK(pick_transcript(nlohmann::json{{"alternative", two}}) == "Jarvis nerede");
    nlohmann::json single = nlohmann::json::array();
    single.push_back(nlohmann::json{{"transcript", "carvis aç"}});
    CHECK(pick_transcript(nlohmann::json{{"alternative", single}}) == "carvis aç");
    nlohmann::json plain{{"transcript", "düz metin"}};
    CHECK(pick_transcript(plain) == "düz metin");
    CHECK(pick_transcript(nlohmann::json{{"alternative", nlohmann::json::array()}}) == "");
    CHECK(pick_transcript(nlohmann::json::object()) == "");
    CHECK(pick_transcript(nlohmann::json()) == "");

    // ---- aygıt sıralama/çözümleme ----
    CHECK(host_rank("Windows WASAPI") == 0);
    CHECK(host_rank("DirectSound") == 1);
    CHECK(host_rank("MME") == 2);
    CHECK(host_rank("Windows WDM-KS") == 3);
    CHECK(host_rank("Bilinmeyen") == 4);

    std::vector<std::pair<int, std::string>> devices = {
        {10, "Mikrofon (Realtek)"}, {2, "mikrofon (realtek)"}, {7, "USB Headset"}};
    CHECK(resolve_microphone_index(devices, "").value_or(-1) == -1);
    CHECK(resolve_microphone_index(devices, "usb headset") == 7);
    CHECK(resolve_microphone_index(devices, "Mikrofon") == 10);
    CHECK(!resolve_microphone_index(devices, "yok böylesi").has_value());

    auto ordered = order_microphones(devices, {"MME", "Windows WASAPI", "DirectSound"});
    CHECK(ordered.size() == 2);                       // aynı ad bir kez
    CHECK(!ordered.empty() && ordered.front().first == 2);  // wasapi öncelikli
    CHECK(ordered.size() > 1 && ordered[1].first == 7);

    // ---- base64 ----
    CHECK(to_base64("") == "");
    CHECK(to_base64("a") == "YQ==");
    CHECK(to_base64("ab") == "YWI=");
    CHECK(to_base64("abc") == "YWJj");
    CHECK(to_base64("test") == "dGVzdA==");

    // ---- önbellek anahtarı (CNG SHA-256, ilk 24 hex) ----
    CHECK(cache_key("", "", "", "") == "be5be69f55e91af25e54ecc2");
    CHECK(cache_key("v", "+1%", "-0Hz", "metin") == "ba31cabb5510e2ac9eaf022b");
    CHECK(cache_key("v", "+1%", "-0Hz", "metin") == cache_key("v", "+1%", "-0Hz", "metin"));
    CHECK(cache_key("v", "+1%", "-0Hz", "bambaşka") != cache_key("v", "+1%", "-0Hz", "metin"));
    CHECK(cache_key("v", "+2%", "-0Hz", "metin") != cache_key("v", "+1%", "-0Hz", "metin"));
    CHECK(cache_key("w", "+1%", "-0Hz", "metin") != cache_key("v", "+1%", "-0Hz", "metin"));
    CHECK(cache_key("v", "+1%", "+5Hz", "metin").size() == 24);
    // Anahtar UTF-8 baytlardan: cp1254'ye düşerse "ı" farklı özet üretirdi.
    CHECK(cache_key("", "", "", "ı") == "2369a2a4441667597c527f53");

    // ---- önbellek yolu ----
    CHECK(cache_dir().filename() == L"tts");
    CHECK(cache_dir().parent_path().filename() == L"cache");
    CHECK(cached_path("abc123").filename() == L"abc123.mp3");
    CHECK(cached_path("abc123").parent_path() == cache_dir());

    // ---- hazır önbellek dosyası ağa çıkmadan döner ----
    {
        std::string key = cache_key("tr-TR-EmelNeural", "+0%", "+0Hz", "önbellek denemesi");
        std::error_code ec;
        fs::create_directories(cache_dir(), ec);
        fs::path probe = cached_path(key);
        {
            std::ofstream out(probe, std::ios::binary);
            out.write(std::string(2048, 'M').data(), 2048);
        }
        CHECK(synthesize("önbellek denemesi", "tr-TR-EmelNeural", "+0%", "+0Hz") == probe);
        fs::remove(probe, ec);
        CHECK(!fs::exists(probe, ec));
    }

    // ---- edge-tts yorumlayıcısı ----
    // Python sürümü edge_tts'i kendi yorumlayıcısında import ediyordu; C++ ayrı
    // süreç açtığı için çözücünün projedeki .venv'i bulup global "python"a
    // düşmemesi gerekir. Import denemesi alt süreç açar ve disk/AV durumuna göre
    // saniyeler sürebildiği için tek test kırması yerine bilgi olarak yazdırılır.
    const std::string tts_py = tts_python();
    CHECK(!tts_py.empty());
    CHECK(tts_py.find(".venv") != std::string::npos);
    std::printf("  edge-tts yorumlayicisi: %s\n", tts_py.c_str());

    // ---- edge-tts komut satırı biçimi ----
    {
        const auto argv = edge_tts_command("Türkiye", "tr-TR-EmelNeural", "+21%", "-10Hz",
                                           fs::path("C:/tmp/a b.mp3"));
        CHECK(argv.size() == 8);
        if (argv.size() == 8) {
            CHECK(argv[1] == L"-m");
            CHECK(argv[2] == L"edge_tts");
            CHECK(argv[3] == L"--text=Türkiye");
            CHECK(argv[4] == L"--voice=tr-TR-EmelNeural");
            CHECK(argv[5] == L"--rate=+21%");
            // Değer bayrağa bitişik olmalı: ayrı "-10Hz" argparse'de bayrak sayılır.
            CHECK(argv[6] == L"--pitch=-10Hz");
            CHECK(argv[7].rfind(L"--write-media=", 0) == 0);
            CHECK(argv[7].find(L'a') != std::wstring::npos);
        }
    }

    // ---- SAPI yedeği ----
    std::string script = sapi_script("test", 75);
    CHECK(script.find("Add-Type -AssemblyName System.Speech") != std::string::npos);
    CHECK(script.find("FromBase64String('dGVzdA==')") != std::string::npos);
    CHECK(script.find("Volume = 75") != std::string::npos);
    CHECK(script.find("Culture.Name -like 'tr*'") != std::string::npos);
    CHECK(script.find("$synth.Speak($text)") != std::string::npos);
    CHECK(sapi_script("test", 150).find("Volume = 100") != std::string::npos);
    CHECK(sapi_script("test", -5).find("Volume = 0") != std::string::npos);
    // Türkçe metin bayt bayt değil base64 ile geçer.
    CHECK(sapi_script("ıŞğ", 60).find("FromBase64String('" + to_base64("ıŞğ") + "')") !=
          std::string::npos);

    // CreateProcessW'ya verilen satır: her parametre tırnaklı, aralarında tek boşluk.
    const std::wstring line = sapi_command_line("merhaba", 70);
    CHECK(line.find(L"\"powershell.exe\"") == 0);
    CHECK(line.find(L" \"-EncodedCommand\" ") != std::wstring::npos);
    CHECK(std::count(line.begin(), line.end(), L' ') == 4);

    auto argv = sapi_command("test", 80);
    CHECK(argv.size() == 5);
    CHECK(!argv.empty() && argv[0] == L"powershell.exe");
    CHECK(argv.size() == 5 && argv[1] == L"-NoProfile");
    CHECK(argv.size() == 5 && argv[2] == L"-NonInteractive");
    CHECK(argv.size() == 5 && argv[3] == L"-EncodedCommand");
    if (argv.size() == 5) {
        std::string decoded = from_base64(ascii_only(argv[4]));
        CHECK(decoded.size() >= 4 && decoded.size() % 2 == 0);
        // UTF-16LE "Sc" başlangıcı: betiğin ilk karakteri '$'.
        CHECK(decoded.size() >= 2 && decoded[0] == '$' && decoded[1] == '\0');
        CHECK(decoded.size() >= 4 && decoded[2] == 'E' && decoded[3] == '\0');
    }

    // ---- mikrofon sohbeti: tek kelime filtresi (voice.py düzeni) ----
    CHECK(!should_accept_barge("masa"));

    // ---- STT anahtarı ----
    // Ayar boşsa kütüphanenin gömülü anahtarı kullanılır; anahtar asla dışa
    // yazılmaz, yalnızca URL içinde taşınır.
    const std::string embedded = resolve_stt_key("");
    CHECK(!embedded.empty());
    CHECK(embedded.rfind("AIza", 0) == 0);
    CHECK(resolve_stt_key("  kendi-anahtarim  ") == "kendi-anahtarim");

    // ---- STT URL kuruluşu ----
    {
        const std::string url = stt_url("a b+c~d_e.f-g", "tr-TR");
        CHECK(url.rfind("http://www.google.com/speech-api/v2/recognize?", 0) == 0);
        CHECK(url.find("client=chromium") != std::string::npos);
        CHECK(url.find("&lang=tr-TR") != std::string::npos);
        CHECK(url.find("&pFilter=0") != std::string::npos);
        // urlencode/quote_plus: boşluk '+', '+' %2B, "_.-~" korunur.
        CHECK(url.find("&key=a+b%2Bc~d_e.f-g") != std::string::npos);
        CHECK(stt_url("ANAHTAR-GIZLI").find("ANAHTAR-GIZLI") != std::string::npos);
        // Boş anahtar varsayılana düşürülür: url() ham kullanır, build() çözer.
        CHECK(stt_url(resolve_stt_key("")).find("key=" + embedded) != std::string::npos);
        CHECK(build_stt_request("", std::string(), 16000).url.find("key=" + embedded) !=
              std::string::npos);
    }

    // ---- STT istek gövdesi / içerik türü ----
    {
        const std::string pcm(2048, '\0');
        const SttRequest l16 = build_stt_request("k", pcm, 16000);
        CHECK(l16.content_type == "audio/l16; rate=16000; channels=1");
        CHECK(l16.body.size() == pcm.size());
        const SttRequest flac = build_stt_request("k", pcm, 8000, SttFormat::Flac);
        CHECK(flac.content_type == "audio/x-flac; rate=8000");
        CHECK(flac.url.find("lang=tr-TR") != std::string::npos);
    }

    // ---- NDJSON ayrıştırma ----
    {
        const std::string ndjson =
            "{\"result\":[]}\n"
            "{\"result\":[{\"alternative\":[{\"transcript\":\"bir iki üç\","
            "\"confidence\":0.49},{\"transcript\":\"1 2\",\"confidence\":0.43}],"
            "\"final\":true}],\"result_index\":0}\n";
        const auto first = first_result(ndjson);
        CHECK(first.has_value());
        CHECK(first->at("alternative").size() == 2);
        // Boş satırlar atlanır, ilk dolu result[0] döner (en iyi olasılık değil).
        CHECK(parse_stt_response(ndjson).text == "bir iki üç");
        CHECK(parse_stt_response(ndjson).kind == SttOutcome::Kind::Text);

        // Uyanma kelimesini içeren alternatife öncelik verilir, sonra ada çevrilir.
        const std::string wake =
            "\n{\"result\":[{\"alternative\":[{\"transcript\":\"serwin al\"},"
            "{\"transcript\":\"carvis hava nasıl\"}]}]}";
        CHECK(parse_stt_response(wake).text == "Jarvis hava nasıl");

        // Boş/çöp yanıtlar "anlaşılamadı" sayılır, ağ hatası değil.
        CHECK(!first_result("{\"result\":[]}").has_value());
        CHECK(parse_stt_response("").kind == SttOutcome::Kind::NotUnderstood);
        CHECK(parse_stt_response("{\"result\":[]}").kind == SttOutcome::Kind::NotUnderstood);
        CHECK(!first_result("{\"result\":[{\"foo\":\"bar\"}]}").has_value());
        CHECK(!first_result("{\"result\":[{\"alternative\":[]}]}").has_value());
        CHECK(!first_result("cöp{").has_value());
        // Hata metni anahtar taşımaz.
        CHECK(std::string(kSttRequestFailed).find("AIza") == std::string::npos);
        CHECK(std::string(kSttNotUnderstood).find("AIza") == std::string::npos);
    }

    // ---- VAD: sessizlik → konuşma → sessizlik → cümle ----
    {
        Feeder quiet(140.0);
        quiet.turn.pause_threshold = 0.45;
        quiet.turn.phrase_limit = kPhraseLimit;
        quiet.vad.start_turn(quiet.turn);
        // 5 sessiz yığın: taban henüz 6 örneği görmedi, eşik 140'ta kalır.
        CHECK(quiet.push(20.0, 5) == VadEvent::Idle);
        // Üst üste 0.18 s geçen yığınlar konuşmayı başlatır (3 yığın = 0.192 s).
        CHECK(quiet.push(3000.0, 2) == VadEvent::Idle);
        CHECK(quiet.push(3000.0, 1) == VadEvent::SpeechStarted);
        CHECK(quiet.vad.started());
        CHECK(quiet.push(3000.0, 10) == VadEvent::Idle);
        // Sessizlik eşiği 112: altındaki yığınlar sessizlik sayar, 0.45 s = 8 yığın.
        CHECK(quiet.push(20.0, 7) == VadEvent::Idle);
        CHECK(quiet.push(20.0, 1) == VadEvent::PhraseDone);
        // 4 ön-al yığını + 10 konuşma + 8 sessizlik = 22 yığın.
        const double expected = 22 * 0.064;
        CHECK(std::fabs(quiet.vad.audio_seconds(16000.0) - expected) < 0.001);
        CHECK(quiet.vad.take_audio().size() == std::size_t(expected * 32000.0));
    }

    // ---- VAD: tek düzen yüksek enerji konuşma değil, AmbientOnly ----
    {
        Feeder flat(140.0);
        flat.vad.start_turn(flat.turn);
        CHECK(flat.push(2000.0, 3) == VadEvent::SpeechStarted);
        // 1.8 s boyunca hiç dalgalanma yok: cümle limiti beklenmez.
        CHECK(flat.push(2000.0, 29) == VadEvent::AmbientOnly);
        CHECK(!flat.vad.started());
        CHECK(flat.vad.audio_seconds(16000.0) == 0.0);  // gövde atıldı
        CHECK(flat.vad.noise_floor() >= 1900.0);        // taban yükseltildi
    }

    // ---- VAD: sessiz giriş Timeout verir (hata değildir) ----
    {
        Feeder idle(140.0);
        idle.turn.timeout = kListenTimeout;  // 0.65 s
        idle.vad.start_turn(idle.turn);
        int feeds = 0;
        VadEvent event = VadEvent::Idle;
        while (event == VadEvent::Idle && feeds < 40) {
            event = idle.vad.feed(8.0, idle.seconds, idle.chunk);
            ++feeds;
        }
        CHECK(event == VadEvent::Timeout);
        CHECK(feeds == 11);  // 11 * 0.064 = 0.704 >= 0.65
        CHECK(idle.vad.audio_seconds(16000.0) == 0.0);
    }

    // ---- VAD: uzun cümle phrase_limit ile kesilir ----
    {
        Feeder ramp(140.0);
        ramp.turn.phrase_limit = 1.0;
        ramp.turn.pause_threshold = 0.45;
        ramp.vad.start_turn(ramp.turn);
        CHECK(ramp.push(3000.0, 3) == VadEvent::SpeechStarted);
        CHECK(ramp.push(3000.0, 15) == VadEvent::Idle);
        CHECK(ramp.push(3000.0, 1) == VadEvent::PhraseDone);  // 16 * 0.064 = 1.024
    }

    // ---- VAD: gürültü tabanı turlar arasında korunur, konuşurken öğrenilmez ----
    {
        Feeder keep(140.0);
        CHECK(keep.push(100.0, 8) == VadEvent::Idle);  // eşik altı: yalnız taban öğrenir
        const double floor_before = keep.vad.noise_floor();
        CHECK(floor_before == 100.0);
        // Yeni tur tabanı sıfırlamaz; Python örnekleri işçi nesnesinde taşıyordu.
        keep.vad.start_turn(keep.turn);
        CHECK(keep.vad.noise_floor() == floor_before);
        CHECK(keep.push(100.0, 1) == VadEvent::Idle);
        CHECK(keep.vad.start_gate() >= floor_before * 1.55);
        // Jarvis konuşurken ölçüm duraklar: kendi sesini tabana katma.
        keep.turn.speaking = true;
        keep.vad.start_turn(keep.turn);
        keep.push(4000.0, 6);
        CHECK(keep.vad.noise_floor() == floor_before);
    }

    // ---- PCM dönüştürücü: yakalama biçimi → 16 kHz mono Int16 ----
    // Zaten hedef biçimdeyse baytlar olduğu gibi geçer.
    {
        std::string raw;
        for (int i = 0; i < 8; ++i) put_i16(raw, i * 100 - 300);
        PcmConverter pass(16000, 1, PcmSample::Int16);
        CHECK(pass.passthrough());
        std::string out;
        pass.append(raw.data(), raw.size(), out);
        CHECK(out == raw);
    }

    // 48 kHz float mono: hız 3'e bölünür, genlik 16-bit ölçeğinde kalır.
    std::string whole;
    {
        std::string float48;
        for (int i = 0; i < 48000; ++i) {
            put_f32(float48, test_tone(i));
        }
        PcmConverter conv(48000, 1, PcmSample::Float);
        CHECK(!conv.passthrough());
        conv.append(float48.data(), float48.size(), whole);
        // Bir saniye 48 kHz → 16000 örnek (32000 bayt); son örnek kuyrukta kalır.
        CHECK(whole.size() >= 31990 && whole.size() <= 32002);
        const double rms = rms16(whole.data(), whole.size());
        const double target = tone_rms_target();
        CHECK(std::abs(rms - target) < target * 0.05);
    }

    // Yığın sınırları ritmi bozamaz: bölünmüş besleme tek seferle aynıdır.
    {
        std::string float48;
        for (int i = 0; i < 48000; ++i) {
            put_f32(float48, test_tone(i));
        }
        PcmConverter conv(48000, 1, PcmSample::Float);
        std::string out;
        // 997 = 4'ün katı değil: her yığın bir kareyi ortadan böler.
        for (std::size_t at = 0; at < float48.size(); at += 997) {
            const std::size_t take = std::min<std::size_t>(997, float48.size() - at);
            conv.append(float48.data() + at, take, out);
        }
        CHECK(out == whole);
    }

    // İki kanal ortalamayla mono'ya iner (48 kHz stereo Int16).
    {
        std::string stereo;
        for (int i = 0; i < 4800; ++i) {
            put_i16(stereo, 1000);
            put_i16(stereo, 3000);
        }
        PcmConverter conv(48000, 2, PcmSample::Int16);
        std::string out;
        conv.append(stereo.data(), stereo.size(), out);
        CHECK(all_equal(out, 2000));
    }

    // 32-bit tamsayı 16-bit ölçeğine indirgenir.
    {
        std::string in;
        for (int i = 0; i < 16000; ++i) put_i32(in, 2000LL * 65536LL);
        PcmConverter conv(16000, 1, PcmSample::Int32);
        std::string out;
        conv.append(in.data(), in.size(), out);
        CHECK(all_equal(out, 2000, 1));
    }

    // 8-bit işaretsiz: önyargı 128, 256 katına ölçeklenir.
    {
        std::string in(8000, static_cast<char>(128 + 10));
        PcmConverter conv(16000, 1, PcmSample::UInt8);
        std::string out;
        conv.append(in.data(), in.size(), out);
        CHECK(all_equal(out, 2560));
    }

    // Ölçüm: audioop.rms karşılığı bilinen bir değeri doğrular.
    {
        std::string in;
        put_i16(in, 1000);
        put_i16(in, -1000);
        put_i16(in, 0);
        put_i16(in, 2000);
        const double rms = rms16(in.data(), in.size());
        CHECK(std::abs(rms - std::sqrt((1000000.0 + 1000000.0 + 0.0 + 4000000.0) / 4.0)) < 0.001);
        CHECK(rms16(nullptr, 0) == 0.0);
        CHECK(pcm_sample_bytes(PcmSample::Float) == 4 && pcm_sample_bytes(PcmSample::UInt8) == 1);
    }

    if (failures == 0) {
        std::printf("speech_test: TÜM TESTLER GEÇTİ\n");
        return 0;
    }
    std::printf("speech_test: %d HATA\n", failures);
    return 1;
}
