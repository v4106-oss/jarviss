#include <cstdio>
#include <string>

#include <jarvis/core/result.hpp>
#include <jarvis/net/http.hpp>

static int failures = 0;

#define CHECK(cond)                                                          \
    do {                                                                     \
        if (!(cond)) {                                                       \
            std::printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond);      \
            ++failures;                                                      \
        }                                                                    \
    } while (0)

int main() {
    using jarvis::Result;
    using jarvis::net::HttpClient;
    using jarvis::net::HttpError;
    using jarvis::net::HttpResponse;

    // --- Result<T, E> (std::expected benzeri) ---
    {
        auto ok = Result<int, std::string>::ok(42);
        CHECK(ok.has_value());
        CHECK(static_cast<bool>(ok));
        CHECK(ok.value() == 42);
        CHECK(ok.value_or(0) == 42);

        auto err = Result<int, std::string>::err(std::string("hata"));
        CHECK(!err.has_value());
        CHECK(err.error() == "hata");
        CHECK(err.value_or(7) == 7);
    }

    // --- HttpResponse::as_json ---
    {
        HttpResponse good{200, R"({"adi":"Jarvis","surum":1})"};
        auto j = good.as_json();
        CHECK(j.has_value());
        CHECK(j.value()["adi"] == "Jarvis");
        CHECK(j.value()["surum"] == 1);

        HttpResponse bad{200, "bu json değil"};
        auto e = bad.as_json();
        CHECK(!e.has_value());
        CHECK(e.error().kind == HttpError::Kind::Json);
    }

    // --- HttpError fabrikaları ---
    {
        CHECK(HttpError::status(404).kind == HttpError::Kind::HttpStatus);
        CHECK(HttpError::timeout("zaman").kind == HttpError::Kind::Timeout);
        CHECK(HttpError::network("bağlantı").kind == HttpError::Kind::Network);
        CHECK(HttpError::status(404).message == "HTTP 404");
    }

    // --- HttpClient yapılandırması (ağ isteği yok) ---
    {
        HttpClient client("https://ornek.com/");
        client.set_timeout_ms(5000);
        client.set_user_agent("JarvisTest/1.0");

        // Adres değişimi nesneyi yerinde tutar (istemciyi her çağrıda yeniden
        // kurmak elde tutulan referansı sarkık bırakıyordu).
        HttpClient& same = client;
        client.set_base_url("https://ornek.com//");
        client.set_base_url("http://127.0.0.1:9");
        CHECK(&same == &client);

        // Regresyon: bozuk taban adres birleştirmede dev ayırma isteyip
        // std::bad_alloc fırlatıyordu; kapalı porta istek hata döndürmeli.
        bool threw = false;
        try {
            (void)client.get("/probe");
        } catch (...) {
            threw = true;
        }
        CHECK(!threw);
    }

    if (failures == 0) {
        std::printf("net_test: TÜM TESTLER GEÇTİ\n");
        return 0;
    }
    std::printf("net_test: %d HATA\n", failures);
    return 1;
}
