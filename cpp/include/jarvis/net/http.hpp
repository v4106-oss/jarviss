#pragma once

#include <cpr/cpr.h>
#include <nlohmann/json.hpp>

#include <map>
#include <string>

#include "jarvis/core/result.hpp"

namespace jarvis::net {

// Ağ katmanı hatası: kategori + insan tarafından okunabilir açıklama.
struct HttpError {
    enum class Kind { Network, Timeout, HttpStatus, Json };

    Kind kind;
    std::string message;

    static HttpError network(std::string msg) { return {Kind::Network, std::move(msg)}; }
    static HttpError timeout(std::string msg) { return {Kind::Timeout, std::move(msg)}; }
    static HttpError status(long code) {
        return {Kind::HttpStatus, "HTTP " + std::to_string(code)};
    }
    static HttpError json(std::string msg) { return {Kind::Json, std::move(msg)}; }
};

struct HttpResponse {
    long status_code{0};
    std::string text;

    // Gövdeyi JSON olarak ayrıştırır; ayrışma hatasında HttpError::json döner.
    Result<nlohmann::json, HttpError> as_json() const;
};

using HttpResult = Result<HttpResponse, HttpError>;

// cpr/libcurl üzerine ince bir HTTP istemcisi. Zaman aşımı, kullanıcı aracısı
// ve özel başlık desteği taşır; başarısızlıklar istisna yerine Result ile döner.
class HttpClient {
public:
    explicit HttpClient(std::string base_url);

    HttpResult get(std::string path,
                   const std::map<std::string, std::string>& query = {},
                   const std::map<std::string, std::string>& headers = {});

    HttpResult post_json(std::string path, const nlohmann::json& body,
                         const std::map<std::string, std::string>& headers = {});

    // Tam URL'ye ham (ikili) gövdeli POST — ses yüklemeleri için. base_url
    // kullanılmaz; sorgu dizesi URL'nin içinde gelir.
    HttpResult post_bytes(const std::string& url, std::string body,
                          const std::string& content_type);

    void set_timeout_ms(long ms);
    void set_user_agent(std::string user_agent);

    // Temel adresi yerinde değiştirir (sondaki '/' atılır). Nesne kimliği
    // korunduğu için elde tutulan referanslar geçersiz olmaz.
    void set_base_url(std::string base_url);

    // true iken 4xx/5xx yanıtlar hata sayılmaz; gövde ve durum kodu caller'a
    // taşınır (kota/hata mesajını ayrıştırmak gerekir). Yalnızca ağ/zaman
    // aşımı hatalı döner. Varsayılan false: geriye dönük uyumlu.
    void set_allow_error_status(bool allow);

private:
    std::string base_url_;
    long timeout_ms_{15000};
    std::string user_agent_{"JarvisCPP/0.1"};
    bool allow_error_status_{false};

    HttpResult finish(cpr::Response response) const;
};

}  // namespace jarvis::net
