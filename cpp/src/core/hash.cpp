#include "jarvis/core/hash.hpp"

#include <cstdint>
#include <cstdio>
#include <filesystem>
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

namespace jarvis::core {
namespace {

#ifdef _WIN32
// Windows CNG sağladığı özet nesneleri; her kullanımda open/close çift hâlinde çağrılır.
struct CngHash {
    BCRYPT_ALG_HANDLE alg = nullptr;
    BCRYPT_HASH_HANDLE hash = nullptr;
    std::vector<BYTE> object;
};

bool cng_open(CngHash& state) {
    if (BCryptOpenAlgorithmProvider(&state.alg, BCRYPT_SHA256_ALGORITHM, nullptr, 0) < 0) {
        state.alg = nullptr;
        return false;
    }
    DWORD object_len = 0;
    DWORD result = 0;
    if (BCryptGetProperty(state.alg, BCRYPT_OBJECT_LENGTH,
                          reinterpret_cast<PUCHAR>(&object_len), sizeof(object_len), &result, 0) >=
            0 &&
        object_len > 0) {
        state.object.resize(object_len);
    } else {
        object_len = 0;
    }
    const NTSTATUS status =
        BCryptCreateHash(state.alg, &state.hash,
                         state.object.empty() ? nullptr : state.object.data(), object_len, nullptr,
                         0, 0);
    if (status < 0) state.hash = nullptr;
    return state.hash != nullptr;
}

void cng_close(CngHash& state) {
    if (state.hash) BCryptDestroyHash(state.hash);
    if (state.alg) BCryptCloseAlgorithmProvider(state.alg, 0);
    state.hash = nullptr;
    state.alg = nullptr;
}

bool cng_add(CngHash& state, const void* data, ULONG length) {
    return BCryptHashData(state.hash, reinterpret_cast<PUCHAR>(const_cast<void*>(data)), length,
                          0) >= 0;
}

std::string cng_finish(CngHash& state) {
    BYTE digest[32] = {};
    if (BCryptFinishHash(state.hash, digest, sizeof(digest), 0) < 0) return {};
    static const char* kHex = "0123456789abcdef";
    std::string hex;
    hex.reserve(64);
    for (BYTE byte : digest) {
        hex.push_back(kHex[byte >> 4]);
        hex.push_back(kHex[byte & 0x0F]);
    }
    return hex;
}
#endif

}  // namespace

std::string sha256_hex(std::string_view data) {
#ifdef _WIN32
    CngHash state;
    if (!cng_open(state)) return {};
    std::string hex;
    if (cng_add(state, data.data(), static_cast<ULONG>(data.size()))) hex = cng_finish(state);
    cng_close(state);
    return hex;
#else
    (void)data;
    return {};
#endif
}

std::optional<std::string> sha256_file(const std::filesystem::path& path) {
#ifdef _WIN32
    std::FILE* stream = nullptr;
    if (_wfopen_s(&stream, path.c_str(), L"rb") != 0 || stream == nullptr) return std::nullopt;
    CngHash state;
    if (!cng_open(state)) {
        std::fclose(stream);
        return std::nullopt;
    }
    std::vector<char> block(1024 * 1024);
    std::size_t read = 0;
    bool ok = true;
    while (ok && (read = std::fread(block.data(), 1, block.size(), stream)) > 0) {
        ok = cng_add(state, block.data(), static_cast<ULONG>(read));
    }
    std::fclose(stream);
    std::string hex;
    if (ok) hex = cng_finish(state);
    cng_close(state);
    if (hex.empty()) return std::nullopt;
    return hex;
#else
    (void)path;
    return std::nullopt;
#endif
}

}  // namespace jarvis::core
