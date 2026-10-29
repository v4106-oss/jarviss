#include <string>

#include <jarvis/core/log.hpp>

#ifdef JARVIS_ENABLE_UI
#include <QApplication>
#include <QFont>

#include <jarvis/tools/screen_capture.hpp>
#include <jarvis/ui/main_window.hpp>
#include <jarvis/ui/theme.hpp>
#else
#include <iostream>

#include <jarvis/core/settings.hpp>
#endif

int main(int argc, char* argv[]) {
    try {
        jarvis::log::write_crash_header();

#ifdef JARVIS_ENABLE_UI
        // Ekran yakalama (BitBlt/GetSystemMetrics) ve SendInput fiziksel pikselle
        // çalışır. Süreç DPI farkındalığını Qt'den önce kendi kurar; Qt zaten
        // ayarlı bir bağlamı değiştirmez, bu yüzden sıra önemli.
        jarvis::tools::make_process_dpi_aware();

        QApplication::setAttribute(Qt::AA_ShareOpenGLContexts);
        QApplication app(argc, argv);
        QApplication::setApplicationName(QStringLiteral("Jarvis"));
        QApplication::setOrganizationName(QStringLiteral("Jarvis"));

        const QFont font(QString::fromStdString(jarvis::ui::current_theme().font), 11);
        app.setFont(font);

        jarvis::ui::MainWindow window;
        window.show();
        return app.exec();
#else
        // Konsol/servis doğrulama modu: komut satırı argümanları kullanılmaz.
        (void)argc;
        (void)argv;
        jarvis::core::Settings settings;
        std::cout << "provider=" << settings.provider()
                  << " model=" << settings.active_model()
                  << " theme=" << settings.theme()
                  << " voice_replies=" << (settings.voice_replies() ? "on" : "off")
                  << " screen_context=" << (settings.screen_context() ? "on" : "off")
                  << '\n';
        return 0;
#endif
    } catch (const std::exception& error) {
        jarvis::log::report_startup_error(std::string("std::exception: ") + error.what());
        return 1;
    }
}
