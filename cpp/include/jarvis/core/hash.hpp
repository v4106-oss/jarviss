#pragma once

// Windows CNG (bcrypt) üzerinden SHA-256; OpenSSL/cpr bağımlılığı üretmez.

#include <filesystem>
#include <optional>
#include <string>
#include <string_view>

namespace jarvis::core {

// UTF-8/bayt yığınının küçük harfli onaltılık özeti; sağlayıcı açılamazsa boş döner.
std::string sha256_hex(std::string_view data);

// Dosya içeriğinin özeti; okunamayan veya boş olmayan ama açılamayan yolda nullopt.
std::optional<std::string> sha256_file(const std::filesystem::path& path);

}  // namespace jarvis::core
