#include "jarvis/net/http.hpp"

#include <utility>

namespace jarvis::net {

namespace {

cpr::Header to_header(const std::map<std::string, std::string>& headers) {
    // cpr::Header bir std::map'tir (büyük/küçük harf duyarsız karşılaştırıcı ile).
    return cpr::Header(headers.begin(), headers.end());
}

cpr::Parameters to_parameters(const std::map<std::string, std::string>& query) {
    cpr::Parameters out;
    for (const auto& [key, value] : query) {
        out.Add({key, value});
    }
    return out;
}

std::string trim_slashes(std::string value) {
    while (!value.empty() && value.back() == '/') value.pop_back();
    return value;
}

}  // namespace

Result<nlohmann::json, HttpError> HttpResponse::as_json() const {
    try {
        return nlohmann::json::parse(text);
    } catch (const nlohmann::json::exception& e) {
        return HttpError::json(e.what());
    }
}

HttpClient::HttpClient(std::string base_url) : base_url_(trim_slashes(std::move(base_url))) {}

void HttpClient::set_timeout_ms(long ms) { timeout_ms_ = ms; }
void HttpClient::set_base_url(std::string base_url) {
    base_url_ = trim_slashes(std::move(base_url));
}
void HttpClient::set_user_agent(std::string user_agent) {
    user_agent_ = std::move(user_agent);
}
void HttpClient::set_allow_error_status(bool allow) { allow_error_status_ = allow; }

HttpResult HttpClient::get(std::string path,
                           const std::map<std::string, std::string>& query,
                           const std::map<std::string, std::string>& headers) {
    cpr::Session session;
    session.SetUrl(cpr::Url{base_url_ + path});
    session.SetHeader(to_header(headers));
    session.SetParameters(to_parameters(query));
    session.SetTimeout(cpr::Timeout{timeout_ms_});
    session.SetUserAgent(cpr::UserAgent{user_agent_});
    return finish(session.Get());
}

HttpResult HttpClient::post_json(std::string path, const nlohmann::json& body,
                                 const std::map<std::string, std::string>& headers) {
    cpr::Session session;
    session.SetUrl(cpr::Url{base_url_ + path});
    auto merged = headers;
    merged["Content-Type"] = "application/json";
    session.SetHeader(to_header(merged));
    // error_handler_t::replace: bozuk UTF-8 içeren bir alan isteği çökertmesin.
    session.SetBody(cpr::Body{
        body.dump(-1, ' ', false, nlohmann::json::error_handler_t::replace)});
    session.SetTimeout(cpr::Timeout{timeout_ms_});
    session.SetUserAgent(cpr::UserAgent{user_agent_});
    return finish(session.Post());
}

HttpResult HttpClient::post_bytes(const std::string& url, std::string body,
                                  const std::string& content_type) {
    cpr::Session session;
    session.SetUrl(cpr::Url{url});
    session.SetHeader(cpr::Header{{"Content-Type", content_type}});
    session.SetBody(cpr::Body(std::move(body)));
    session.SetTimeout(cpr::Timeout{timeout_ms_});
    session.SetUserAgent(cpr::UserAgent{user_agent_});
    return finish(session.Post());
}

HttpResult HttpClient::finish(cpr::Response response) const {
    if (response.error) {
        if (response.error.code == cpr::ErrorCode::OPERATION_TIMEDOUT) {
            return HttpError::timeout(response.error.message);
        }
        return HttpError::network(response.error.message);
    }

    HttpResponse result{static_cast<long>(response.status_code),
                        std::move(response.text)};
    if (!allow_error_status_ && result.status_code >= 400) {
        return HttpError::status(result.status_code);
    }
    return result;
}

}  // namespace jarvis::net
