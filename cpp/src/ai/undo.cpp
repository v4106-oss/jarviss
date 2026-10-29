#include "jarvis/ai/undo.hpp"

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <ctime>
#include <random>
#include <stdexcept>
#include <system_error>

#include <nlohmann/json.hpp>

#include "jarvis/core/hash.hpp"
#include "jarvis/core/win_path.hpp"

namespace fs = std::filesystem;
using nlohmann::json;

namespace jarvis::ai {
namespace {

#ifdef _WIN32
fs::path from_u8(const std::string& utf8) { return fs::path(jarvis::util::utf8_to_wide(utf8)); }
std::string to_u8(const fs::path& path) { return jarvis::util::wide_to_utf8(path.wstring()); }
#else
fs::path from_u8(const std::string& utf8) { return fs::path(utf8); }
std::string to_u8(const fs::path& path) { return path.string(); }
#endif

std::string random_suffix() {
    static const char* kHex = "0123456789abcdef";
    std::random_device device;
    std::string out;
    out.reserve(6);
    for (int i = 0; i < 6; ++i) out.push_back(kHex[device() % 16]);
    return out;
}

std::string local_iso_seconds() {
    const std::time_t now = std::time(nullptr);
    std::tm stamp{};
#ifdef _WIN32
    localtime_s(&stamp, &now);
#else
    localtime_r(&now, &stamp);
#endif
    char buffer[32] = {};
    std::snprintf(buffer, sizeof(buffer), "%04d-%02d-%02dT%02d:%02d:%02d", stamp.tm_year + 1900,
                  stamp.tm_mon + 1, stamp.tm_mday, stamp.tm_hour, stamp.tm_min, stamp.tm_sec);
    return buffer;
}

bool path_exists(const fs::path& path) {
    std::error_code ec;
    return fs::exists(path, ec);
}

bool path_is_dir(const fs::path& path) {
    std::error_code ec;
    return fs::is_directory(path, ec);
}

bool is_regular(const fs::path& path) {
    std::error_code ec;
    return fs::is_regular_file(path, ec);
}

long long file_bytes(const fs::path& path) {
    std::error_code ec;
    const auto size = fs::file_size(path, ec);
    if (ec) return 0;
    return static_cast<long long>(size);
}

void make_directories(const fs::path& directory) {
    std::error_code ec;
    fs::create_directories(directory, ec);
}

void copy_over(const fs::path& from, const fs::path& to) {
    std::error_code ec;
    fs::copy_file(from, to, fs::copy_options::overwrite_existing, ec);
    if (ec) throw std::runtime_error("Dosya kopyalanamadı: " + to_u8(from));
    // Python shutil.copy2 zaman damgasını da taşır.
    std::error_code tec;
    const auto stamp = fs::last_write_time(from, tec);
    if (!tec) fs::last_write_time(to, stamp, tec);
}

// Ağaçtaki tüm dosyaları göreli yollarıyla ve sıralı döndürür (Python rglob + sorted).
std::vector<fs::path> tree_files(const fs::path& root) {
    std::vector<fs::path> out;
    std::error_code ec;
    if (!path_is_dir(root)) return out;
    for (fs::recursive_directory_iterator it(root, fs::directory_options::skip_permission_denied, ec),
                                         end;
         it != end; it.increment(ec)) {
        if (ec) break;
        if (!it->is_regular_file(ec)) continue;
        out.push_back(it->path());
    }
    std::sort(out.begin(), out.end());
    return out;
}

json entry_to_json(const UndoEntry& entry) {
    json item{{"kind", entry.kind}};
    if (!entry.path.empty()) item["path"] = to_u8(entry.path);
    if (!entry.backup.empty()) item["backup"] = to_u8(entry.backup);
    item["is_dir"] = entry.is_dir;
    if (!entry.sha256.empty()) item["sha256"] = entry.sha256;
    if (entry.truncated) item["truncated"] = true;
    if (!entry.files.empty()) item["files"] = entry.files;
    if (!entry.source.empty()) item["src"] = to_u8(entry.source);
    if (!entry.destination.empty()) item["dst"] = to_u8(entry.destination);
    return item;
}

UndoEntry entry_from_json(const json& item) {
    auto text = [&item](const char* key) -> std::string {
        if (!item.contains(key) || !item[key].is_string()) return {};
        return item[key].get<std::string>();
    };
    UndoEntry entry;
    entry.kind = text("kind");
    if (auto raw = text("path"); !raw.empty()) entry.path = from_u8(raw);
    if (auto raw = text("backup"); !raw.empty()) entry.backup = from_u8(raw);
    entry.is_dir = item.value("is_dir", false);
    entry.sha256 = text("sha256");
    entry.truncated = item.value("truncated", false);
    if (item.contains("files") && item["files"].is_object()) {
        for (auto it = item["files"].begin(); it != item["files"].end(); ++it) {
            if (it.value().is_string()) entry.files[it.key()] = it.value().get<std::string>();
        }
    }
    if (auto raw = text("src"); !raw.empty()) entry.source = from_u8(raw);
    if (auto raw = text("dst"); !raw.empty()) entry.destination = from_u8(raw);
    return entry;
}

std::optional<json> read_manifest(const fs::path& set_dir) {
    const fs::path manifest = set_dir / "manifest.json";
    std::FILE* stream = nullptr;
#ifdef _WIN32
    if (_wfopen_s(&stream, manifest.c_str(), L"rb") != 0 || stream == nullptr) return std::nullopt;
#else
    stream = std::fopen(manifest.string().c_str(), "rb");
    if (stream == nullptr) return std::nullopt;
#endif
    std::string data;
    char buffer[8192];
    std::size_t read = 0;
    while ((read = std::fread(buffer, 1, sizeof(buffer), stream)) > 0) data.append(buffer, read);
    std::fclose(stream);
    try {
        json parsed = json::parse(data);
        return parsed;
    } catch (const std::exception&) {
        return std::nullopt;
    }
}

}  // namespace

long long next_undo_order() {
    static std::mutex mutex;
    static long long last = 0;
    const auto now = std::chrono::duration_cast<std::chrono::nanoseconds>(
                         std::chrono::system_clock::now().time_since_epoch())
                         .count();
    std::lock_guard<std::mutex> lock(mutex);
    last = std::max<long long>(static_cast<long long>(now), last + 1);
    return last;
}

// ------------------------------------------------------------------ ChangeSet

ChangeSet::ChangeSet(UndoStore& store, std::string label)
    : store_(store), label_(std::move(label)) {
    char order[32] = {};
    std::snprintf(order, sizeof(order), "%020lld", next_undo_order());
    dir_ = store.root() / (std::string(order) + "-" + random_suffix());
}

void ChangeSet::backup(const fs::path& file) {
    if (path_is_dir(file)) {
        throw BackupError("Klasörler bu araçla yedeklenip değiştirilemez.");
    }
    const long long size = file_bytes(file);
    if (size > store_.max_backup_bytes()) {
        throw BackupError("Dosya yedeklenemeyecek kadar büyük (" + std::to_string(size / 1000000) +
                          " MB); değiştirilmedi: " + to_u8(file.filename()));
    }
    fs::path target = dir_ / "files" / std::to_string(entries_.size()) / file.filename();
    make_directories(target.parent_path());
    copy_over(file, target);
    UndoEntry entry;
    entry.kind = "modified";
    entry.path = file;
    std::error_code ec;
    entry.backup = fs::relative(target, dir_, ec);
    if (ec) entry.backup = target;
    entries_.push_back(std::move(entry));
}

void ChangeSet::created(const fs::path& path) {
    UndoEntry entry;
    entry.kind = "created";
    entry.path = path;
    entry.is_dir = path_is_dir(path);
    if (entry.is_dir) {
        std::size_t index = 0;
        for (const fs::path& child : tree_files(path)) {
            if (index >= kUndoMaxTreeFiles) {
                entry.truncated = true;
                break;
            }
            ++index;
            std::error_code ec;
            const fs::path rel = fs::relative(child, path, ec);
            const std::string key = ec ? to_u8(child.filename()) : to_u8(rel);
            const std::optional<std::string> digest = core::sha256_file(child);
            entry.files[key] = digest.value_or("");
        }
    } else if (is_regular(path)) {
        entry.sha256 = core::sha256_file(path).value_or("");
    }
    entries_.push_back(std::move(entry));
}

void ChangeSet::moved(const fs::path& source, const fs::path& destination) {
    UndoEntry entry;
    entry.kind = "moved";
    entry.source = source;
    entry.destination = destination;
    entries_.push_back(std::move(entry));
}

void ChangeSet::trashed(const fs::path& path) {
    UndoEntry entry;
    entry.kind = "trashed";
    entry.path = path;
    entries_.push_back(std::move(entry));
}

void ChangeSet::discard() {
    std::error_code ec;
    fs::remove_all(dir_, ec);
}

void ChangeSet::commit() {
    if (entries_.empty()) {
        discard();
        return;
    }
    make_directories(dir_);
    json items = json::array();
    for (const UndoEntry& entry : entries_) items.push_back(entry_to_json(entry));
    json manifest{{"label", label_}, {"created", local_iso_seconds()}, {"entries", items}};
    const std::string text = manifest.dump(1);
    const fs::path manifest_path = dir_ / "manifest.json";
    std::FILE* stream = nullptr;
#ifdef _WIN32
    if (_wfopen_s(&stream, manifest_path.c_str(), L"wb") != 0 || stream == nullptr) {
        throw std::runtime_error("Geri alma kaydı yazılamadı: " + to_u8(manifest_path));
    }
#else
    stream = std::fopen(manifest_path.string().c_str(), "wb");
    if (stream == nullptr) throw std::runtime_error("Geri alma kaydı yazılamadı");
#endif
    std::fwrite(text.data(), 1, text.size(), stream);
    std::fclose(stream);
    store_.prune();
}

// ------------------------------------------------------------------- UndoStore

UndoStore::UndoStore(fs::path root, int max_sets, long long max_total_bytes,
                     long long max_backup_bytes)
    : root_(std::move(root)),
      max_sets_(max_sets),
      max_total_bytes_(max_total_bytes),
      max_backup_bytes_(max_backup_bytes) {}

ChangeSet UndoStore::begin(const std::string& label) { return ChangeSet(*this, label); }

std::vector<fs::path> UndoStore::sets() const {
    std::vector<fs::path> out;
    if (!path_is_dir(root_)) return out;
    std::error_code ec;
    for (fs::directory_iterator it(root_, fs::directory_options::skip_permission_denied, ec), end;
         it != end; it.increment(ec)) {
        if (ec) break;
        if (!it->is_directory(ec)) continue;
        if (path_exists(it->path() / "manifest.json")) out.push_back(it->path());
    }
    std::sort(out.begin(), out.end());
    return out;
}

long long UndoStore::size_of(const fs::path& directory) {
    long long total = 0;
    for (const fs::path& file : tree_files(directory)) total += file_bytes(file);
    return total;
}

void UndoStore::prune() {
    std::lock_guard<std::mutex> lock(mutex_);
    std::vector<fs::path> sets = this->sets();
    while (sets.size() > static_cast<std::size_t>(std::max(0, max_sets_))) {
        std::error_code ec;
        fs::remove_all(sets.front(), ec);
        sets.erase(sets.begin());
    }
    long long total = 0;
    for (const fs::path& set : sets) total += size_of(set);
    while (sets.size() > 1 && total > max_total_bytes_) {
        const long long freed = size_of(sets.front());
        std::error_code ec;
        fs::remove_all(sets.front(), ec);
        sets.erase(sets.begin());
        total -= freed;
    }
}

std::optional<std::string> UndoStore::last_label() {
    std::vector<fs::path> sets = this->sets();
    if (sets.empty()) return std::nullopt;
    const std::optional<json> manifest = read_manifest(sets.back());
    if (!manifest || !manifest->is_object()) return std::nullopt;
    if (!manifest->contains("label") || !(*manifest)["label"].is_string()) return std::nullopt;
    return (*manifest)["label"].get<std::string>();
}

std::string UndoStore::undo_entry(const fs::path& set_dir, const UndoEntry& entry) {
    if (entry.kind == "modified") {
        const fs::path target = entry.path;
        make_directories(target.parent_path());
        copy_over(set_dir / entry.backup, target);
        return "↩️ Eski haline döndürüldü: " + to_u8(target);
    }
    if (entry.kind == "created") {
        const fs::path target = entry.path;
        if (!path_exists(target)) return "ℹ️ Zaten yok: " + to_u8(target);
        if (!entry.is_dir) {
            const std::string current = core::sha256_file(target).value_or("");
            if (current != entry.sha256) {
                return "⚠️ Oluşturulduktan sonra değiştirilmiş, silinmedi: " + to_u8(target);
            }
            std::error_code ec;
            fs::remove(target, ec);
            if (ec) throw std::runtime_error(ec.message());
            return "↩️ Oluşturulan dosya silindi: " + to_u8(target);
        }
        if (entry.truncated) {
            return "⚠️ Çok fazla dosya içeriyor, elle silmelisin: " + to_u8(target);
        }
        int left = 0;
        for (const auto& [rel, digest] : entry.files) {
            const fs::path child = target / from_u8(rel);
            if (is_regular(child) && core::sha256_file(child).value_or("") == digest) {
                std::error_code ec;
                fs::remove(child, ec);
            } else if (path_exists(child)) {
                ++left;
            }
        }
        // En derin klasörler önce: boşalanlar silinir, dolular kalır.
        std::vector<fs::path> folders;
        std::error_code ec;
        for (fs::recursive_directory_iterator it(target, fs::directory_options::skip_permission_denied, ec),
             end;
             it != end; it.increment(ec)) {
            if (ec) break;
            if (it->is_directory(ec)) folders.push_back(it->path());
        }
        std::sort(folders.begin(), folders.end());
        for (auto it = folders.rbegin(); it != folders.rend(); ++it) {
            std::error_code rm;
            fs::remove(*it, rm);
        }
        std::error_code rr;
        fs::remove(target, rr);
        if (path_exists(target)) ++left;
        if (left > 0) {
            return "⚠️ Sonradan eklenen/değişen dosyalar korundu, klasör silinmedi: " +
                   to_u8(target);
        }
        return "↩️ Oluşturulan klasör silindi: " + to_u8(target);
    }
    if (entry.kind == "moved") {
        const fs::path source = entry.source;
        const fs::path destination = entry.destination;
        if (!path_exists(destination)) {
            return "ℹ️ Taşınan öğe artık orada değil: " + to_u8(destination);
        }
        if (path_exists(source)) {
            return "⚠️ Eski konumda başka bir öğe var, geri taşınmadı: " + to_u8(source);
        }
        make_directories(source.parent_path());
        std::error_code ec;
        fs::rename(destination, source, ec);
        if (ec) throw std::runtime_error(ec.message());
        return "↩️ Geri taşındı: " + to_u8(source);
    }
    if (entry.kind == "trashed") {
        return "ℹ️ Geri Dönüşüm Kutusu'ndan elle geri yükle: " + to_u8(entry.path);
    }
    return "ℹ️ Bilinmeyen kayıt türü atlandı: " + entry.kind;
}

UndoOutcome UndoStore::undo_last() {
    std::lock_guard<std::mutex> lock(mutex_);
    UndoOutcome outcome;
    std::vector<fs::path> sets = this->sets();
    if (sets.empty()) {
        outcome.ok = false;
        outcome.messages.push_back("Geri alınacak bir işlem yok.");
        return outcome;
    }
    const fs::path latest = sets.back();
    const std::optional<json> manifest = read_manifest(latest);
    outcome.ok = true;
    if (manifest && manifest->is_object() && (*manifest).contains("label") &&
        (*manifest)["label"].is_string()) {
        outcome.label = (*manifest)["label"].get<std::string>();
        outcome.has_label = true;
    }
    json items = json::array();
    if (manifest && manifest->is_object() && manifest->contains("entries") &&
        (*manifest)["entries"].is_array()) {
        items = (*manifest)["entries"];
    }
    for (auto it = items.rbegin(); it != items.rend(); ++it) {
        const UndoEntry entry = entry_from_json(*it);
        const std::string hint = to_u8(!entry.path.empty()
                                           ? entry.path
                                           : (!entry.destination.empty() ? entry.destination
                                                                         : fs::path()));
        try {
            outcome.messages.push_back(undo_entry(latest, entry));
        } catch (const std::exception& error) {
            outcome.ok = false;
            outcome.messages.push_back("⚠️ Geri alınamadı (" + hint + "): " + error.what());
        }
    }
    std::error_code ec;
    fs::remove_all(latest, ec);
    return outcome;
}

}  // namespace jarvis::ai
