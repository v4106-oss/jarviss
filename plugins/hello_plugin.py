from app.core.plugin_manager import PluginBase


class HelloPlugin(PluginBase):
    name = "hello"

    def execute(self, *args, **kwargs):
        return "Merhaba, ben eklenti modülüyüm!"
