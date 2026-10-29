#include <cstdio>

#include <jarvis/net/http.hpp>

int main() {
    using jarvis::net::HttpClient;

    HttpClient client("https://api.github.com");

    auto text = client.get("/zen");
    if (!text.has_value()) {
        std::printf("FAIL GET /zen: %s\n", text.error().message.c_str());
        return 1;
    }
    std::printf("zen[%ld]: %.40s...\n", text.value().status_code,
                text.value().text.c_str());

    auto json = client.get("/repos/nlohmann/json");
    if (!json.has_value()) {
        std::printf("FAIL GET /repos: %s\n", json.error().message.c_str());
        return 1;
    }
    auto parsed = json.value().as_json();
    if (!parsed.has_value()) {
        std::printf("FAIL JSON parse: %s\n", parsed.error().message.c_str());
        return 1;
    }
    std::printf("repo full_name: %s\n",
                parsed.value().at("full_name").get<std::string>().c_str());

    std::printf("net_smoke: BASARILI\n");
    return 0;
}
