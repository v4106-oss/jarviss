#include <jarvis/core/history.hpp>

#include <utility>

namespace jarvis::core {

nlohmann::json ChatMessage::as_dict() const {
    return {
        {"role", role},
        {"content", content},
        {"source", source},
        {"metadata", metadata.has_value() ? *metadata : nlohmann::json(nullptr)},
        {"created_at", created_at},
    };
}

ChatMessage ChatMessage::from_row(const nlohmann::json& row) {
    ChatMessage message;
    message.role = row.value("role", std::string{});
    message.content = row.value("content", std::string{});
    message.source = row.value("source", std::string{"chat"});
    if (row.contains("metadata") && !row["metadata"].is_null()) {
        message.metadata = row["metadata"];
    }
    message.created_at = row.value("created_at", std::string{});
    return message;
}

}  // namespace jarvis::core
