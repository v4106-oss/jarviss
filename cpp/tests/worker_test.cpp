// Worker orkestrasyon testi — sahte yürütücüyle, ağa çıkmadan.
// Hızlı yol, güvenlik/onay geçitleri, duraklat-devam-devral ve oturum kaydı.
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <filesystem>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "jarvis/ai/worker.hpp"
#include "jarvis/core/settings.hpp"
#include "jarvis/core/skill_library.hpp"
#include "jarvis/storage/storage.hpp"

static int failures = 0;
#define CHECK(cond)                                                      \
    do {                                                                 \
        if (!(cond)) {                                                   \
            std::printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                  \
        }                                                                \
    } while (0)

using namespace jarvis;
using namespace jarvis::ai;

namespace {

void sleep_ms(int ms) { std::this_thread::sleep_for(std::chrono::milliseconds(ms)); }

bool has(const std::vector<std::string>& values, const std::string& needle) {
    for (const std::string& value : values)
        if (value.find(needle) != std::string::npos) return true;
    return false;
}

// OS'e dokunmayan sahte yürütücü: kararı test verir, sonucu sabit olur.
class FakeExecutor : public IActionExecutor {
public:
    PlanAssessment assessment{"allow", Risk::Create, {"test karari"}};
    std::string execute_result = "OK-sonuc";
    std::vector<std::string> plan_results;
    std::function<void()> on_plan_enter;

    int assess_calls = 0;
    int plan_calls = 0;
    std::vector<std::string> executed;
    std::vector<std::string> plan_tools;
    std::vector<bool> tainted_seen;

    PlanAssessment assess(const ActionPlan& plan, bool tainted) override {
        (void)plan;
        std::lock_guard<std::mutex> lock(mx_);
        ++assess_calls;
        tainted_seen.push_back(tainted);
        return assessment;
    }
    std::string execute(const PlannedAction& action) override {
        std::lock_guard<std::mutex> lock(mx_);
        executed.push_back(action.action_type);
        return execute_result;
    }
    std::vector<std::string> execute_plan(
        const ActionPlan& plan,
        const std::function<void(const std::string&)>& on_progress) override {
        {
            std::lock_guard<std::mutex> lock(mx_);
            ++plan_calls;
            plan_tools.clear();
            for (const PlannedAction& action : plan.actions)
                plan_tools.push_back(action.action_type);
            if (plan_results.size() != plan.actions.size())
                plan_results.assign(plan.actions.size(), execute_result);
        }
        if (on_plan_enter) on_plan_enter();
        if (on_progress) on_progress("plan tamamlandi");
        std::lock_guard<std::mutex> lock(mx_);
        return plan_results;
    }
    std::string diff_self_workspace(const nlohmann::json&) override { return "DIFF-METNI"; }

    std::size_t executed_count() {
        std::lock_guard<std::mutex> lock(mx_);
        return executed.size();
    }
    bool last_tainted() {
        std::lock_guard<std::mutex> lock(mx_);
        return !tainted_seen.empty() && tainted_seen.back();
    }

private:
    std::mutex mx_;
};

// Qt sinyalleri yerine geçen geri çağırmaları toplayan kayıt defteri.
class Recorder {
public:
    void bind(AIWorker& worker) {
        WorkerCallbacks cb;
        cb.started = [this] { bump(started_); };
        cb.finished = [this] { bump(finished_); };
        cb.resumed = [this] { bump(resumed_); };
        cb.response = [this](const std::string& s) {
            std::lock_guard<std::mutex> lock(mx_);
            response_ = s;
        };
        cb.error = [this](const std::string& s) { push(errors_, s); };
        cb.progress = [this](const std::string& s) { push(progress_, s); };
        cb.approval_requested = [this](const std::string& s) {
            push(approvals_, s);
            asked_.store(true);
        };
        cb.paused = [this](const std::string& s) { push(paused_, s); };
        cb.takeover_requested = [this](const std::string& s) {
            push(takeovers_, s);
            bump(takeover_);
        };
        worker.set_callbacks(std::move(cb));
    }

    std::vector<std::string> progress() const {
        std::lock_guard<std::mutex> lock(mx_);
        return progress_;
    }
    std::vector<std::string> approvals() const {
        std::lock_guard<std::mutex> lock(mx_);
        return approvals_;
    }
    std::vector<std::string> paused() const {
        std::lock_guard<std::mutex> lock(mx_);
        return paused_;
    }
    std::vector<std::string> takeovers() const {
        std::lock_guard<std::mutex> lock(mx_);
        return takeovers_;
    }
    std::string response() const {
        std::lock_guard<std::mutex> lock(mx_);
        return response_;
    }
    int started() const { std::lock_guard<std::mutex> lock(mx_); return started_; }
    int finished() const { std::lock_guard<std::mutex> lock(mx_); return finished_; }
    int resumed_count() const { std::lock_guard<std::mutex> lock(mx_); return resumed_; }
    int takeover_count() const { std::lock_guard<std::mutex> lock(mx_); return takeover_; }
    bool asked() const { return asked_.load(); }
    void reset_asked() { asked_.store(false); }

private:
    void bump(int& counter) {
        std::lock_guard<std::mutex> lock(mx_);
        ++counter;
    }
    void push(std::vector<std::string>& store, const std::string& value) {
        std::lock_guard<std::mutex> lock(mx_);
        store.push_back(value);
    }

    mutable std::mutex mx_;
    std::vector<std::string> progress_, errors_, approvals_, paused_, takeovers_;
    std::string response_;
    int started_ = 0, finished_ = 0, resumed_ = 0, takeover_ = 0;
    std::atomic<bool> asked_{false};
};

// Onay sorusunu yanıtlayan yardımcı iş parçacığı.
std::thread answer_approval(Recorder& rec, AIWorker& worker, bool approve) {
    return std::thread([&rec, &worker, approve] {
        for (int i = 0; i < 400 && !rec.asked(); ++i) sleep_ms(5);
        rec.reset_asked();
        worker.set_approval(approve);
    });
}

ActionPlan single_plan(const std::string& type, const nlohmann::json& arguments) {
    ActionPlan plan;
    plan.actions.push_back(PlannedAction{type, arguments, type});
    return plan;
}

std::int64_t latest_session_id(storage::Storage& store) {
    auto session = store.get_latest_task_session();
    if (!session || !session->contains("id")) return 0;
    return session->at("id").get<std::int64_t>();
}

}  // namespace

int main() {
    namespace fs = std::filesystem;
    auto stamp = std::chrono::steady_clock::now().time_since_epoch().count();
    fs::path dir = fs::temp_directory_path() / ("jarvis_worker_test_" + std::to_string(stamp));
    fs::create_directories(dir);

    {
        core::Settings settings((dir / "settings.json").string());
        storage::Storage store((dir / "jarvis.db").string());
        core::SkillLibrary skills(dir / "skills", false);
        AI ai(settings, store, skills);
        SafetyServices services = build_services(settings);
        FakeExecutor exec;
        AIWorker worker(ai, store, skills, services, exec);
        Recorder rec;
        rec.bind(worker);

        // ---- 1) oturum yokken task_session aracı ----
        std::string status = worker.execute_tool("task_session", nlohmann::json{{"action", "status"}});
        CHECK(status.find("Etkin g") != std::string::npos);  // "Etkin görev oturumu yok."
        std::string bad = worker.execute_tool("task_session", nlohmann::json{{"action", "yok-boyle"}});
        CHECK(bad.rfind("\xE2\x9D\x8C", 0) == 0);  // ❌ ile başlar
        CHECK(exec.assess_calls == 0);             // task_session güvenlik geçidine girmez

        // ---- 2) allow kararı: yürütür + ilerleme mesajları + audit allow ----
        exec.assessment = PlanAssessment{"allow", Risk::Create, {"serbest"}};
        exec.execute_result = "OK-sonuc";
        std::string created =
            worker.execute_tool("create_folder", nlohmann::json{{"path", "C:/tmp/x"}});
        CHECK(created == "OK-sonuc");
        CHECK(exec.executed_count() == 1);
        CHECK(has(rec.progress(), "create_folder"));

        // ---- 3) deny kararı: yürütmez, gerekçeyi ❌ ile döner ----
        exec.assessment = PlanAssessment{"deny", Risk::Forbidden, {"yasak", "gerekce"}};
        std::string denied = worker.execute_tool("delete_path", nlohmann::json{{"path", "C:/Windows"}});
        CHECK(denied.rfind("\xE2\x9D\x8C", 0) == 0);
        CHECK(denied.find("yasak") != std::string::npos && denied.find("gerekce") != std::string::npos);
        CHECK(exec.executed_count() == 1);  // execute hiç çağrılmadı

        // ---- 4) ask + reddedilen onay: yürütmez, kullanıcı red mesajı ----
        exec.assessment = PlanAssessment{"ask", Risk::Modify, {"onay gerekli"}};
        {
            std::thread reply = answer_approval(rec, worker, false);
            std::string rejected =
                worker.execute_tool("write_text_file", nlohmann::json{{"path", "C:/tmp/a.txt"},
                                                                     {"content", "gizli icerik"}});
            reply.join();
            CHECK(rejected.find("onaylamad") != std::string::npos);
            CHECK(exec.executed_count() == 1);
            CHECK(rec.approvals().size() == 1);
            // içerik onay metninde gösterilmez, yalnızca karakter sayısı yazılır
            CHECK(!has(rec.approvals(), "gizli icerik"));
            CHECK(has(rec.approvals(), "karakter"));
        }

        // ---- 5) ask + onay: yürütür ----
        {
            std::thread reply = answer_approval(rec, worker, true);
            std::string approved_run =
                worker.execute_tool("write_text_file", nlohmann::json{{"path", "C:/tmp/b.txt"}});
            reply.join();
            CHECK(approved_run == "OK-sonuc");
            CHECK(exec.executed_count() == 2);
        }

        // ---- 6) apply_self_changes her zaman onay ister (allow olsa bile) ----
        exec.assessment = PlanAssessment{"allow", Risk::Dangerous, {"serbest"}};
        {
            std::thread reply = answer_approval(rec, worker, true);
            std::string self = worker.execute_tool(
                "apply_self_changes", nlohmann::json{{"patch", "diff"}});
            reply.join();
            CHECK(self == "OK-sonuc");
            CHECK(has(rec.approvals(), "apply_self_changes"));
            CHECK(has(rec.approvals(), "DIFF-METNI"));  // diff metni onay ekranına eklenir
        }

        // ---- 7) web okuma sonucu "tainted" bayrağını kaldırır ----
        exec.assessment = PlanAssessment{"allow", Risk::Read, {"okuma"}};
        exec.execute_result = "OK-iceerik";
        worker.execute_tool("read_page", nlohmann::json{{"url", "https://example.com"}});
        bool first_clean = !exec.last_tainted();
        worker.execute_tool("create_folder", nlohmann::json{{"path", "C:/tmp/y"}});
        CHECK(first_clean);
        CHECK(exec.last_tainted());  // güvenilmeyen içerikten sonraki adım tainted

        // ---- 8) kill anahtarı: hiçbir şey yürütülmez ----
        services.kill->request();
        std::string killed = worker.execute_tool("create_folder", nlohmann::json{{"path", "C:/tmp/z"}});
        CHECK(killed.find("durduruldu") != std::string::npos);
        std::string killed_session =
            worker.execute_tool("task_session", nlohmann::json{{"action", "status"}});
        CHECK(killed_session.find("durduruldu") != std::string::npos);
        services.kill->clear();

        // ---- 9) audit günlüğü kararları içerir, dosya içeriğini ifşa etmez ----
        auto audit = services.audit.tail(50);
        CHECK(audit.size() >= 5);
        bool saw_deny = false, saw_ask = false, saw_allow = false;
        for (const auto& entry : audit) {
            const std::string decision = entry.value("decision", std::string());
            if (decision == "deny") saw_deny = true;
            if (decision == "ask") saw_ask = true;
            if (decision == "allow") saw_allow = true;
        }
        CHECK(saw_deny && saw_ask && saw_allow);

        // ---- 10) duraklatma çalışmıyorken sessiz kalır ----
        CHECK(!worker.is_paused());
        worker.pause("istem disi");
        CHECK(!worker.is_paused());  // request_running false => duraklatma yok

        // ---- 11) hızlı yol: plan override, oturum adımı kaydı, yanıt ----
        exec.plan_results = {"STEP-A", "STEP-B"};
        worker.set_prompt("bir klasör ve bir dosya olustur");
        ActionPlan plan;
        plan.actions.push_back(PlannedAction{"create_folder", nlohmann::json{{"path", "C:/tmp/p1"}},
                                             "klasor"});
        plan.actions.push_back(
            PlannedAction{"write_text_file", nlohmann::json{{"path", "C:/tmp/p1/a.txt"}}, "dosya"});
        worker.set_plan(plan);

        std::mutex bx;
        std::condition_variable bcv;
        bool entered = false;
        bool release = false;
        exec.on_plan_enter = [&] {
            std::unique_lock<std::mutex> lock(bx);
            entered = true;
            bcv.notify_all();
            bcv.wait(lock, [&] { return release; });
        };

        std::thread running([&] { worker.run(); });
        {
            std::unique_lock<std::mutex> lock(bx);
            bcv.wait_for(lock, std::chrono::seconds(5), [&] { return entered; });
        }
        CHECK(entered);
        CHECK(worker.request_running());

        // duraklat -> araç çağrısı bekler, yürütmez
        worker.pause("test molası");
        CHECK(worker.is_paused());
        std::size_t before = exec.executed_count();
        std::string blocked_result;
        std::thread blocked([&] {
            blocked_result = worker.execute_tool("open_path", nlohmann::json{{"path", "C:/tmp/p1"}});
        });
        sleep_ms(150);
        CHECK(exec.executed_count() == before);  // duraklatmada yürütme yok

        worker.resume();
        CHECK(!worker.is_paused());
        blocked.join();
        CHECK(blocked_result == "OK-iceerik");
        CHECK(exec.executed_count() == before + 1);
        CHECK(rec.resumed_count() == 1);

        // devralma: hem takeover hem paused sinyali
        worker.takeover("kullanıcı devraldı");
        CHECK(worker.is_paused());
        CHECK(rec.takeover_count() == 1);
        CHECK(has(rec.takeovers(), "kullan"));
        CHECK(has(rec.paused(), "kullan"));
        worker.resume();
        CHECK(!worker.is_paused());

        // iptal: duraklatılmış araç çağrısını kill ile serbest bırakır
        worker.pause("iptal testi");
        std::string cancelled_result;
        std::thread blocked2([&] {
            cancelled_result = worker.execute_tool("open_path", nlohmann::json{{"path", "C:/tmp/p2"}});
        });
        sleep_ms(120);
        worker.cancel();
        blocked2.join();
        CHECK(cancelled_result.find("durduruldu") != std::string::npos);
        // Anahtar temizlenmezse yürütmenin son durumu "cancelled" olurdu.
        services.kill->clear();

        release = true;
        bcv.notify_all();
        running.join();

        CHECK(rec.started() == 1);
        CHECK(rec.finished() == 1);
        CHECK(!worker.request_running());
        CHECK(rec.response() == "STEP-A\nSTEP-B");
        CHECK(has(rec.progress(), "Yerel hızlı yol"));

        // oturum adımı + koşu kaydı depoda doğrulanır
        std::int64_t session_id = latest_session_id(store);
        CHECK(session_id > 0);
        auto steps = store.get_task_steps(session_id, 50);
        CHECK(steps.size() >= 3);  // iki plan adımı + bekleyen open_path çağrısı
        bool session_step = false;
        for (const auto& step : steps)
            if (step.value("tool", std::string()) == "open_path") session_step = true;
        CHECK(session_step);
        auto runs = store.get_agent_runs(5);
        CHECK(!runs.empty());
        CHECK(runs.front().value("status", std::string()) == "completed");

        // ---- 12) hızlı yol başarısızsa ajana düşer (anahtar yok => uyarı metni) ----
        Recorder rec2;
        rec2.bind(worker);
        exec.plan_results = {"\xE2\x9D\x8C bulunamadı"};
        worker.set_prompt("photoshop u aç");
        worker.set_plan(single_plan("open_application", nlohmann::json{{"application", "photoshop"}}));
        worker.run();
        CHECK(has(rec2.progress(), "Hızlı yol başarısız"));
        CHECK(rec2.response().find("Gemini API anahtarı yok") != std::string::npos);
        CHECK(rec2.finished() == 1);

        // ---- 13) yerel hızlı yol seçimi: ayrıştırıcı + bağ/kelime/ek filtreleri ----
        Recorder rec3;
        rec3.bind(worker);
        exec.on_plan_enter = nullptr;
        exec.plan_results.clear();
        exec.execute_result = "HIZLI-YOL";
        worker.set_plan(std::nullopt);

        // Tek adım, kısa ve bağlaçsız: hızlı yol çalışır, model çağrılmaz.
        worker.set_prompt("Projeler adında bir klasör oluştur");
        worker.run();
        CHECK(exec.plan_calls >= 1);
        CHECK(rec3.response() == "HIZLI-YOL");
        CHECK(has(rec3.progress(), "Yerel hızlı yol"));

        // "ve" bağlacı iki adımı işaret eder: hızlı yol terk edilir, ajana düşülür.
        Recorder rec4;
        rec4.bind(worker);
        int plans_before = exec.plan_calls;
        worker.set_prompt("Projeler adında bir klasör oluştur ve içine Not yaz");
        worker.run();
        CHECK(exec.plan_calls == plans_before);
        CHECK(rec4.response().find("Gemini API anahtarı yok") != std::string::npos);

        // Uzun istek (>12 kelime) her zaman ajana gider.
        Recorder rec5;
        rec5.bind(worker);
        plans_before = exec.plan_calls;
        worker.set_prompt(
            "şimdi bana on iki kelimeyi aşan epeyce uzun bir cümle kur ve klasör oluştur "
            "diye bitir");
        worker.run();
        CHECK(exec.plan_calls == plans_before);
        CHECK(rec5.response().find("Gemini API anahtarı yok") != std::string::npos);

        // Ek/camera bağlamı varsa yerel ayrıştırıcı hiç devreye girmez.
        Recorder rec6;
        rec6.bind(worker);
        plans_before = exec.plan_calls;
        worker.set_attachments({(dir / "ek.txt").string()});
        worker.set_prompt("Projeler adında bir klasör oluştur");
        worker.run();
        worker.set_attachments({});
        CHECK(exec.plan_calls == plans_before);
        CHECK(rec6.response().find("Gemini API anahtarı yok") != std::string::npos);
    }

    fs::remove_all(dir);

    if (failures == 0) {
        std::printf("worker_test: TÜM TESTLER GEÇTİ\n");
        return 0;
    }
    std::printf("worker_test: %d HATA\n", failures);
    return 1;
}
