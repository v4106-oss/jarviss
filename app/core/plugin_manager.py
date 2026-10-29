import importlib.util
import inspect
from pathlib import Path
from typing import Any, Dict, List


class PluginBase:
    name = "base"

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("Plugin must implement execute")


class PluginManager:
    def __init__(self, plugins_dir: str = None):
        root = Path(__file__).resolve().parents[2]
        self.plugins_dir = Path(plugins_dir) if plugins_dir else root / "plugins"
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.plugins: Dict[str, PluginBase] = {}
        self.load_plugins()

    def load_plugins(self) -> None:
        for plugin_path in self.plugins_dir.glob("*.py"):
            self._load_plugin(plugin_path)

    def _load_plugin(self, plugin_path: Path) -> None:
        spec = importlib.util.spec_from_file_location(plugin_path.stem, plugin_path)
        if not spec or not spec.loader:
            return

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        for _, obj in inspect.getmembers(module, inspect.isclass):
            if obj is PluginBase or not issubclass(obj, PluginBase):
                continue

            plugin = obj()
            self.plugins[plugin.name] = plugin

    def get_plugin(self, name: str) -> PluginBase:
        return self.plugins[name]

    def available_plugins(self) -> List[str]:
        return list(self.plugins.keys())

    def reload_plugins(self) -> None:
        self.plugins.clear()
        self.load_plugins()
