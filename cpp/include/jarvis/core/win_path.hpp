#pragma once

// Windows'ta std::filesystem::path::string() yerel kod sayfasını kullanır; Türkçe
// yollar bayt bayt bozulmasın diye UTF-8 ↔ geniş karakter dönüşümleri burada.
#include <string>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

namespace jarvis::util {

std::wstring utf8_to_wide(const std::string& utf8);
std::string wide_to_utf8(const std::wstring& wide);

}  // namespace jarvis::util
#endif
