#pragma once

// Agent Skills uyumlu yerel yetenek kütüphanesi.
// Python app/core/skill_library.py dosyasının C++20 karşılığı.
//
// Her yetenek `<kök>/<ad>/SKILL.md` dosyasındadır. Dosyanın başındaki YAML
// benzeri alanlardan yalnızca `name` ve `description` okunur; gövde gerektiğinde
// `skill` aracıyla yüklenir. Yetenek talimatları güvenlik kurallarının, kullanıcı
// onayının veya araç sınırlarının yerine geçmez.

#include <cstddef>
#include <filesystem>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

namespace jarvis::core {

inline constexpr std::size_t kSkillMaxFileBytes = 250000;
inline constexpr std::size_t kSkillMaxBody = 20000;
inline constexpr std::size_t kSkillMaxDescription = 500;

// Yetenek dosyasından okunan tek bir SKILL.md kaydı.
struct Skill {
    std::string name;
    std::string description;
    std::string body;
    std::filesystem::path path;
};

class SkillError : public std::runtime_error {
public:
    explicit SkillError(const std::string& message) : std::runtime_error(message) {}
};

// Türkçe karakterleri aksansız küçük harfe indirger, [a-z0-9] dışını '-' yapar.
std::string slugify(const std::string& value);

// Bir SKILL.md metnini ayrıştırır; geçersiz/eksik alanlarda nullopt döner.
std::optional<Skill> parse_skill(const std::string& text, const std::filesystem::path& path);

class SkillLibrary {
public:
    // root boşsa %LOCALAPPDATA%/Jarvis/skills kullanılır.
    explicit SkillLibrary(std::filesystem::path root = {}, bool seed = true);

    void ensure_defaults();
    std::vector<Skill> list_skills();
    std::optional<Skill> get(const std::string& name);
    // Doğrulama hatasında SkillError fırlatır.
    Skill create(const std::string& name, const std::string& description, const std::string& body,
                 bool overwrite = false);
    std::string index_text(int limit = 12);

    // `skill` aracının giriş noktası; action: list/read/create.
    // Hatalı çağrıda SkillError fırlatır.
    std::string run(const nlohmann::json& args);

    const std::filesystem::path& root() const { return root_; }

private:
    std::filesystem::path root_;

    std::vector<std::filesystem::path> paths() const;
};

}  // namespace jarvis::core
