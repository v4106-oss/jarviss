#pragma once

#include <string>
#include <string_view>
#include <vector>

namespace jarvis::util {

// Baş/son boşlukları atar.
std::string trim(std::string_view s);

// Ardışık boşlukları tek boşluğa indirger.
std::string collapse_whitespace(std::string_view s);

// Yalnızca ASCII harfleri küçültür (Türkçe noktalı harfler aynen korunur).
std::string to_lower_ascii(std::string_view s);

// "HH:MM" biçimini doğrular; geçersizse fallback döner.
std::string normalize_clock_text(std::string_view value, std::string_view fallback);

// Değeri [lo, hi] aralığına sıkıştırır.
int clamp_int(long long value, int lo, int hi);

// UTF-8 kod noktası sayısı (Python len() karşılığı; geçersiz baytlar 1 sayılır).
std::size_t utf8_length(const std::string& value);

// UTF-8 metni en fazla `limit` kod noktasına indirger; kırpılırsa '…' eklenmez.
std::string utf8_take(const std::string& value, std::size_t limit);

// Geçersiz/eksik UTF-8 baytlarını U+FFFD ile değiştirir (Python errors="replace"
// karşılığı). Bozuk bayt veritabanına ya da istek gövdesine böylece sızmaz.
std::string utf8_sanitize(const std::string& value);

// Windows'ta char* argv ANSI kod sayfasıyla (cp1254) dolduğu için Türkçe girdi
// bozuk bayta dönüşür. Komut satırını geniş hâlden okuyup UTF-8'e çevirir.
std::vector<std::string> utf8_arguments(int argc, char* argv[]);

// Şu anki saati ISO-8601 UTC biçiminde döner (milisaniye hassasiyeti).
std::string now_utc_iso();

// Tema adını kanonik 5 temadan birine indirger; bilinmeyen -> "black".
std::string normalize_theme(std::string_view name);

}  // namespace jarvis::util
