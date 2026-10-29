#pragma once

// AppLauncher — Python app/core/agent.py ApplicationLauncher karşılığı.
//
// Uygulama keşif zinciri Python sırasıyla çalışır:
//   ① SYSTEM_APPS bilinen adlar  ② Başlat menüsü .lnk kısayolları (kullanıcı +
//   sistem)  ③ App Paths kayıt defteri (HKCU/HKLM, WOW6432Node dahil)
//   ④ shell:AppsFolder (UWP, Get-StartApps)  ⑤ winget paket klasörleri.
// Bulunamazsa Türkçe "eşleşen kısayol veya kayıt bulunamadı" hatası + en yakın
// üç aday adı döner.
//
// Başlatma ShellExecuteW / CreateProcessW ile yapılır (Qt yok). Açılış yeni bir
// pencere/süreç doğurduysa EnumWindows + süreç adıyla geri okunarak doğrulanır;
// doğrulanamazsa "başlatıldı ama penceresi açılmadı" denir — sahte başarı yok.
//
// Python'dan bilinçli eksik: oyun istemcilerindeki "Oyna/Play" düğmesine tıklama
// (_start_from_launcher, UIA + OCR ister) P1-3 turuna bırakıldı; o noktada
// "istemci açıldı, Oyna'ya basılmalı" diye dürüst yanıt döner.

#include <filesystem>
#include <functional>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace jarvis::tools {

class AppLauncher {
public:
    using StopPredicate = std::function<bool()>;

    AppLauncher();
    AppLauncher(std::vector<std::filesystem::path> start_menu_dirs,
                std::filesystem::path winget_root);

    // Python launch(): doğrula → çalışanı öne getir → keşif zinciri → doğrula.
    std::string launch(const std::string& application, const StopPredicate& should_stop = {});
    // Python open_url(): seçilen tarayıcıyı URL ile başlat.
    std::string open_url(const std::string& application, const std::string& url);

    // --- salt metin mantığı: ekran/OS gerektirmeden test edilir ---
    static std::string normalize(const std::string& value);
    static double similarity(const std::string& left, const std::string& right);
    static double score(const std::string& query, const std::string& stem);
    static std::string validate(const std::string& application);
    static std::vector<std::string> tokens(const std::string& text);
    static std::vector<std::string> app_tokens(const std::string& application);
    static bool window_matches(const std::string& application, const std::string& title);
    static bool is_play_label(const std::string& text);
    // SYSTEM_APPS tablosu; yoksa boş metin.
    static std::string system_command(const std::string& application);
    // IGNORED_SHORTCUT_WORDS: kaldırma/yardım kısayolları eşleşmeye girmez.
    static bool is_ignored_shortcut(const std::string& normalized_name);

    // --- keşif uçları (ambiguity durumunda ActionForbidden fırlatır) ---
    std::optional<std::filesystem::path> find_shortcut(const std::string& application) const;
    std::optional<std::pair<std::string, std::string>> find_registered_app(
        const std::string& application) const;
    std::optional<std::pair<std::string, std::string>> find_store_app(
        const std::string& application) const;
    std::optional<std::pair<std::string, std::string>> find_winget_app(
        const std::string& application) const;
    // Hata mesajına eklenen en yakın adaylar (kısayol + kayıtlı + winget adları).
    std::vector<std::string> nearest_candidates(const std::string& application,
                                                std::size_t count = 3) const;

    // .lnk hedef yolu (WScript.Shell CreateShortcut karşılığı); okunamaz boş.
    static std::string shortcut_target(const std::filesystem::path& shortcut);

    const std::vector<std::filesystem::path>& start_menu_dirs() const { return start_menu_dirs_; }

private:
    std::vector<std::filesystem::path> start_menu_dirs_;
    std::filesystem::path winget_root_;
};

}  // namespace jarvis::tools
