#include "jarvis/speech/synth.hpp"

#include "jarvis/speech/text.hpp"

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <stdexcept>
#include <thread>
#include <vector>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <bcrypt.h>
#endif

namespace fs = std::filesystem;

namespace jarvis::speech {
namespace {

std::wstring to_w(const std::string& s) {
#ifdef _WIN32
    if (s.empty()) return {};
    int n = MultiByteToWideChar(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), nullptr, 0);
    std::wstring w(n, 0);
    MultiByteToWideChar(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), w.data(), n);
    return w;
#else
    std::wstring w;
    w.reserve(s.size());
    for (unsigned char c : s) w.push_back(static_cast<wchar_t>(c));
    return w;
#endif
}

std::string to_u8(const std::wstring& w) {
#ifdef _WIN32
    if (w.empty()) return {};
    int n = WideCharToMultiByte(CP_UTF8, 0, w.data(), static_cast<int>(w.size()), nullptr, 0,
                                nullptr, nullptr);
    std::string s(n, 0);
    WideCharToMultiByte(CP_UTF8, 0, w.data(), static_cast<int>(w.size()), s.data(), n, nullptr,
                        nullptr);
    return s;
#else
    std::string s;
    s.reserve(w.size());
    for (wchar_t c : w) s.push_back(static_cast<char>(c));
    return s;
#endif
}

fs::path path_from_utf8(const std::string& utf8_text) {
#ifdef _WIN32
    return fs::path(to_w(utf8_text));
#else
    return fs::path(utf8_text);
#endif
}

fs::path user_home() {
#ifdef _WIN32
    if (const char* profile = std::getenv("USERPROFILE")) return path_from_utf8(profile);
    if (const char* home = std::getenv("HOME")) return path_from_utf8(home);
#else
    if (const char* home = std::getenv("HOME")) return path_from_utf8(home);
#endif
    return {};
}

std::string sha256_hex(const std::string& data) {
#ifdef _WIN32
    BCRYPT_ALG_HANDLE alg = nullptr;
    if (BCryptOpenAlgorithmProvider(&alg, BCRYPT_SHA256_ALGORITHM, nullptr, 0) < 0) return "";
    DWORD object_len = 0, result = 0;
    std::vector<BYTE> object;
    if (BCryptGetProperty(alg, BCRYPT_OBJECT_LENGTH, reinterpret_cast<PUCHAR>(&object_len),
                          sizeof(object_len), &result, 0) >= 0 && object_len > 0) {
        object.resize(object_len);
    }
    BCRYPT_HASH_HANDLE hash = nullptr;
    std::string hex;
    if (BCryptCreateHash(alg, &hash, object.empty() ? nullptr : object.data(), object_len,
                         nullptr, 0, 0) >= 0) {
        BYTE digest[32] = {};
        if (BCryptHashData(hash, reinterpret_cast<PUCHAR>(const_cast<char*>(data.data())),
                           static_cast<ULONG>(data.size()), 0) >= 0 &&
            BCryptFinishHash(hash, digest, sizeof(digest), 0) >= 0) {
            static const char* kHex = "0123456789abcdef";
            hex.reserve(64);
            for (BYTE byte : digest) {
                hex.push_back(kHex[byte >> 4]);
                hex.push_back(kHex[byte & 0x0F]);
            }
        }
        BCryptDestroyHash(hash);
    }
    BCryptCloseAlgorithmProvider(alg, 0);
    return hex;
#else
    (void)data;
    return "";
#endif
}

std::string base64_bytes(const std::string& bytes) {
    static const char kTable[] =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string out;
    out.reserve(((bytes.size() + 2) / 3) * 4);
    for (std::size_t i = 0; i < bytes.size(); i += 3) {
        unsigned long triple = static_cast<unsigned char>(bytes[i]) << 16;
        if (i + 1 < bytes.size()) triple |= static_cast<unsigned char>(bytes[i + 1]) << 8;
        if (i + 2 < bytes.size()) triple |= static_cast<unsigned char>(bytes[i + 2]);
        out.push_back(kTable[(triple >> 18) & 0x3F]);
        out.push_back(kTable[(triple >> 12) & 0x3F]);
        out.push_back(i + 1 < bytes.size() ? kTable[(triple >> 6) & 0x3F] : '=');
        out.push_back(i + 2 < bytes.size() ? kTable[triple & 0x3F] : '=');
    }
    return out;
}

// CreateProcess komut satırı alıntı kuralları (MSVC CRT): tüm parametreler tırnak içinde,
// tırnaktan önceki ardışık ters bölüler iki katına çıkarılır.
std::wstring quote_arg(const std::wstring& arg) {
    if (arg.empty()) return L"\"\"";
    std::wstring out = L"\"";
    std::size_t slashes = 0;
    for (wchar_t c : arg) {
        if (c == L'\\') {
            ++slashes;
            continue;
        }
        if (c == L'"') {
            out.append(slashes * 2 + 1, L'\\');
            out.push_back(c);
            slashes = 0;
            continue;
        }
        out.append(slashes, L'\\');
        slashes = 0;
        out.push_back(c);
    }
    out.append(slashes * 2, L'\\');
    out.push_back(L'"');
    return out;
}

struct RunResult {
    bool started = false;
    bool finished = false;
    int code = -1;
};

RunResult run_hidden(const std::vector<std::wstring>& argv, long timeout_ms) {
#ifdef _WIN32
    RunResult result;
    std::wstring line;
    for (std::size_t i = 0; i < argv.size(); ++i) {
        if (i) line += L' ';
        line += quote_arg(argv[i]);
    }
    HANDLE nul = CreateFileW(L"NUL", GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr,
                             OPEN_EXISTING, 0, nullptr);
    STARTUPINFOW si{};
    si.cb = sizeof(si);
    PROCESS_INFORMATION pi{};
    if (nul != INVALID_HANDLE_VALUE) {
        si.dwFlags = STARTF_USESTDHANDLES;
        si.hStdInput = nul;
        si.hStdOutput = nul;
        si.hStdError = nul;
    }
    BOOL ok = CreateProcessW(nullptr, line.data(), nullptr, nullptr, FALSE, CREATE_NO_WINDOW,
                             nullptr, nullptr, &si, &pi);
    if (ok) {
        DWORD wait = WaitForSingleObject(pi.hProcess, static_cast<DWORD>(timeout_ms));
        if (wait == WAIT_OBJECT_0) {
            DWORD code = 1;
            GetExitCodeProcess(pi.hProcess, &code);
            result.finished = true;
            result.code = static_cast<int>(code);
        } else {
            TerminateProcess(pi.hProcess, 1);
            WaitForSingleObject(pi.hProcess, 2000);
            result.finished = false;
            result.code = -1;
        }
        CloseHandle(pi.hThread);
        CloseHandle(pi.hProcess);
        result.started = true;
    }
    if (nul != INVALID_HANDLE_VALUE) CloseHandle(nul);
    return result;
#else
    (void)argv;
    (void)timeout_ms;
    return {};
#endif
}

std::string env_or(const char* name, const std::string& fallback) {
    if (const char* value = std::getenv(name)) {
        if (*value) return value;
    }
    return fallback;
}

fs::path executable_dir() {
#ifdef _WIN32
    std::wstring buffer(32768, 0);
    const DWORD length =
        GetModuleFileNameW(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
    if (length == 0 || length >= buffer.size()) return {};
    buffer.resize(length);
    return fs::path(buffer).parent_path();
#else
    std::error_code ec;
    return fs::current_path(ec).parent_path();
#endif
}

bool has_edge_tts(const fs::path& python) {
#ifdef _WIN32
    // edge_tts gerçekten import edilmez: aiohttp + SSL zinciriyle bu makinede
    // ~6 sn sürüyor ve yorumlayıcı seçimi alt süreç zaman aşımına giriyordu.
    // find_spec yalnızca sys.path tarar, aynı soruyu ~1 sn'de cevaplar.
    const RunResult run = run_hidden(
        {python.wstring(), L"-c",
         L"import importlib.util,sys;"
         L"sys.exit(0 if importlib.util.find_spec('edge_tts') else 1)"},
        8000);
    return run.finished && run.code == 0;
#else
    (void)python;
    return false;
#endif
}

struct TtsInterpreter {
    std::wstring path;
};

// Adaylar sırayla denenir: açık ayar, çalıştırılabilirin üst basamaklarındaki
// .venv, mevcut çalışma dizininin üst basamaklarındaki .venv, son çare "python".
const TtsInterpreter& resolve_tts_python() {
    static const TtsInterpreter resolved = [] {
        TtsInterpreter result;
        const std::string forced = env_or("JARVIS_TTS_PYTHON", "");
        if (!forced.empty()) {
            const fs::path candidate = path_from_utf8(forced);
            result.path = candidate.empty() ? to_w(forced) : candidate.wstring();
            return result;
        }
        std::error_code ec;
        const fs::path seeds[] = {executable_dir(), fs::current_path(ec)};
        for (const fs::path& seed : seeds) {
            fs::path dir = seed;
            for (int depth = 0; depth <= 4 && !dir.empty(); ++depth) {
                const fs::path candidate =
                    dir / fs::path(L".venv") / fs::path(L"Scripts") / fs::path(L"python.exe");
                if (fs::is_regular_file(candidate, ec) && has_edge_tts(candidate)) {
                    result.path = candidate.wstring();
                    return result;
                }
                dir = dir.parent_path();
            }
        }
        result.path = L"python";
        return result;
    }();
    return resolved;
}

}  // namespace

std::vector<std::wstring> edge_tts_command(const std::string& text, const std::string& voice,
                                           const std::string& rate, const std::string& pitch,
                                           const fs::path& out) {
    // edge-tts argparse boşluklu biçimde "--pitch -10Hz" değerini bayrak sanıp
    // reddediyor; hepsi --bayrak=değer biçiminde verilir.
    std::vector<std::wstring> argv;
    argv.push_back(resolve_tts_python().path);
    argv.push_back(L"-m");
    argv.push_back(L"edge_tts");
    argv.push_back(L"--text=" + to_w(text));
    argv.push_back(L"--voice=" + to_w(voice));
    argv.push_back(L"--rate=" + to_w(rate));
    argv.push_back(L"--pitch=" + to_w(pitch));
#ifdef _WIN32
    argv.push_back(L"--write-media=" + out.wstring());
#else
    argv.push_back(L"--write-media=" + to_w(out.string()));
#endif
    return argv;
}

std::string to_base64(const std::string& bytes) { return base64_bytes(bytes); }

std::string tts_python() { return to_u8(resolve_tts_python().path); }

fs::path cache_dir() {
#ifdef _WIN32
    fs::path base;
    if (const char* local = std::getenv("LOCALAPPDATA")) base = path_from_utf8(local);
    if (base.empty()) base = user_home();
    return base / L"Jarvis" / L"cache" / L"tts";
#else
    fs::path base = user_home();
    if (base.empty()) base = fs::temp_directory_path();
    return base / "Jarvis" / "cache" / "tts";
#endif
}

std::string cache_key(const std::string& voice, const std::string& rate, const std::string& pitch,
                      const std::string& text) {
    // Anahtar UTF-8 baytlar üzerinden hesaplanır; Windows yerel kodu (cp1254)
    // Türkçe metinde baytları kaydırdığı için önbellek tutarsız olurdu.
    std::string hex = sha256_hex(voice + "|" + rate + "|" + pitch + "|" + text);
    if (hex.empty()) return {};
    return hex.substr(0, 24);
}

fs::path cached_path(const std::string& key) { return cache_dir() / (key + ".mp3"); }

std::filesystem::path synthesize(const std::string& text, const std::string& voice,
                                 const std::string& rate, const std::string& pitch) {
    std::error_code ec;
    fs::create_directories(cache_dir(), ec);
    std::string key = cache_key(voice, rate, pitch, text);
    fs::path path = key.empty() ? fs::path() : cached_path(key);
    if (!path.empty() && fs::is_regular_file(path, ec) && fs::file_size(path, ec) > 1024) {
        return path;
    }

    std::string reason = "bilinmeyen hata";
    for (int attempt = 0; attempt < kSynthAttempts; ++attempt) {
        RunResult run = run_hidden(edge_tts_command(text, voice, rate, pitch, path),
                                   kSynthTimeoutMs);
        if (run.finished && run.code == 0 && !path.empty() && fs::is_regular_file(path, ec) &&
            fs::file_size(path, ec) > 1024) {
            trim_cache();
            return path;
        }
        if (!run.started) {
            reason = "edge-tts çalıştırılamadı (python yok mu?)";
        } else if (!run.finished) {
            reason = "edge-tts zaman aşımına uğradı";
        } else {
            reason = "edge-tts çıkış kodu " + std::to_string(run.code);
        }
        if (!path.empty()) fs::remove(path, ec);
        if (attempt + 1 < kSynthAttempts) {
            std::this_thread::sleep_for(std::chrono::milliseconds(600));
        }
    }
    throw std::runtime_error("Nöral ses üretilemedi: " + reason);
}

void trim_cache(int keep) {
    std::error_code ec;
    fs::directory_iterator it(cache_dir(), fs::directory_options::skip_permission_denied, ec);
    if (ec) return;
    std::vector<std::pair<fs::file_time_type, fs::path>> files;
    for (const fs::directory_entry& entry : it) {
        if (!entry.is_regular_file(ec) || ec) continue;
        const fs::path& p = entry.path();
        if (p.extension() != L".mp3") continue;
        files.emplace_back(fs::last_write_time(p, ec), p);
    }
    std::sort(files.begin(), files.end(),
              [](const auto& a, const auto& b) { return a.first > b.first; });
    for (std::size_t i = keep < 0 ? 0 : static_cast<std::size_t>(keep); i < files.size(); ++i) {
        std::error_code rm;
        fs::remove(files[i].second, rm);
    }
}

std::string sapi_script(const std::string& text, int volume) {
    const int clamped = std::max(0, std::min(100, volume));
    const std::string encoded = base64_bytes(text);
    std::string script;
    script += "$ErrorActionPreference = 'Stop'\n";
    script += "Add-Type -AssemblyName System.Speech\n";
    script += "$text = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + encoded +
              "'))\n";
    script += "$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer\n";
    script += "try { $synth.Volume = " + std::to_string(clamped) + " } catch {}\n";
    script += "try {\n";
    script += "  $voice = $synth.GetInstalledVoices() | Where-Object { $_.Enabled -and "
              "$_.VoiceInfo.Culture.Name -like 'tr*' } | Select-Object -First 1\n";
    script += "  if ($voice) { $synth.SelectVoice($voice.VoiceInfo.Name) }\n";
    script += "} catch {}\n";
    script += "$synth.Speak($text)\n";
    script += "$synth.Dispose()\n";
    return script;
}

std::vector<std::wstring> sapi_command(const std::string& text, int volume) {
    const std::string script = sapi_script(text, volume);
    std::string utf16;
#ifdef _WIN32
    int n = MultiByteToWideChar(CP_UTF8, 0, script.data(), static_cast<int>(script.size()),
                                 nullptr, 0);
    std::wstring wide(n, 0);
    MultiByteToWideChar(CP_UTF8, 0, script.data(), static_cast<int>(script.size()), wide.data(), n);
    utf16.assign(reinterpret_cast<const char*>(wide.data()),
                 static_cast<std::size_t>(n) * sizeof(wchar_t));
#else
    for (unsigned char c : script) {
        utf16.push_back(static_cast<char>(c));
        utf16.push_back('\0');
    }
#endif
    return {L"powershell.exe", L"-NoProfile", L"-NonInteractive", L"-EncodedCommand",
            to_w(base64_bytes(utf16))};
}

std::wstring sapi_command_line(const std::string& text, int volume) {
    std::wstring line;
    for (const std::wstring& arg : sapi_command(text, volume)) {
        if (!line.empty()) line += L' ';
        line += quote_arg(arg);
    }
    return line;
}

}  // namespace jarvis::speech
