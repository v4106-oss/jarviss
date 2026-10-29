#pragma once

// Geri alınabilir dosya değişiklikleri — Python app/core/safety.py
// UndoStore/ChangeSet karşılığı.
//
// Her eylem kendi "değişim seti" klasörüne yazılır:
//   <kök>/<sıra>-<rastgele>/manifest.json  (etiket, zaman, kayıtlar)
//   <kök>/<sıra>-<rastgele>/files/<n>/<ad>  (değişmeden önceki yedekler)
// Manifest alan adları Python sürümüyle birebir aynıdır; iki uygulama aynı
// klasörü paylaşabilir.

#include <cstddef>
#include <filesystem>
#include <map>
#include <mutex>
#include <optional>
#include <string>
#include <vector>

#include "jarvis/ai/safety.hpp"

namespace jarvis::ai {

inline constexpr int kUndoMaxSets = 50;
inline constexpr long long kUndoMaxTotalBytes = 500000000LL;
inline constexpr long long kUndoMaxBackupBytes = 100000000LL;
inline constexpr std::size_t kUndoMaxTreeFiles = 2000;

struct UndoEntry {
    std::string kind;  // modified | created | moved | trashed
    std::filesystem::path path;
    std::filesystem::path backup;  // set klasörüne göreli (modified)
    bool is_dir = false;
    std::string sha256;
    bool truncated = false;
    std::map<std::string, std::string> files;  // created klasör ağacı: göreli yol -> özet
    std::filesystem::path source;              // moved
    std::filesystem::path destination;         // moved
};

struct UndoOutcome {
    bool ok = false;
    std::vector<std::string> messages;
    std::string label;
    bool has_label = false;
};

class UndoStore;

// Tek bir eylemin geri alınabilir kaydı; commit() edilmezse iz bırakmaz.
class ChangeSet {
public:
    ChangeSet(UndoStore& store, std::string label);

    void backup(const std::filesystem::path& file);  // BackupError fırlatabilir
    void created(const std::filesystem::path& path);
    void moved(const std::filesystem::path& source, const std::filesystem::path& destination);
    void trashed(const std::filesystem::path& path);
    void discard();
    void commit();

    const std::filesystem::path& directory() const { return dir_; }
    bool empty() const { return entries_.empty(); }

private:
    UndoStore& store_;
    std::string label_;
    std::filesystem::path dir_;
    std::vector<UndoEntry> entries_;
};

class UndoStore {
public:
    explicit UndoStore(std::filesystem::path root, int max_sets = kUndoMaxSets,
                       long long max_total_bytes = kUndoMaxTotalBytes,
                       long long max_backup_bytes = kUndoMaxBackupBytes);

    ChangeSet begin(const std::string& label);

    // Sıralı (eski→yeni) değişim seti klasörleri; manifesti olanlar.
    std::vector<std::filesystem::path> sets() const;
    std::optional<std::string> last_label();
    UndoOutcome undo_last();

    const std::filesystem::path& root() const { return root_; }
    long long max_backup_bytes() const { return max_backup_bytes_; }
    int max_sets() const { return max_sets_; }
    long long max_total_bytes() const { return max_total_bytes_; }

    void prune();
    static long long size_of(const std::filesystem::path& directory);
    static std::string undo_entry(const std::filesystem::path& set_dir, const UndoEntry& entry);

private:
    std::filesystem::path root_;
    int max_sets_;
    long long max_total_bytes_;
    long long max_backup_bytes_;
    std::mutex mutex_;
};

// Her çağrıda öncekinden büyük bir sayı verir: "son işlem" asla karışmaz.
long long next_undo_order();

}  // namespace jarvis::ai
