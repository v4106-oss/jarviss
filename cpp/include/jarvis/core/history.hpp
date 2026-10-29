#pragma once

#include <optional>
#include <string>

#include <nlohmann/json.hpp>

#include <jarvis/core/util.hpp>

namespace jarvis::core {

// Sohbet iletisi; Python'daki app/core/history.py ChatMessage dataclass karşılığı.
struct ChatMessage {
    std::string role;
    std::string content;
    std::string source{"chat"};
    std::optional<nlohmann::json> metadata;
    std::string created_at;

    ChatMessage() = default;
    ChatMessage(std::string role,
                std::string content,
                std::string source = "chat",
                std::optional<nlohmann::json> metadata = std::nullopt,
                std::string created_at = util::now_utc_iso())
        : role(std::move(role)),
          content(std::move(content)),
          source(std::move(source)),
          metadata(std::move(metadata)),
          created_at(std::move(created_at)) {}

    nlohmann::json as_dict() const;
    static ChatMessage from_row(const nlohmann::json& row);
};

}  // namespace jarvis::core
