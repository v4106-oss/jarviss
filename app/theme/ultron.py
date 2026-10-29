# Seçili Jarvis teması. Süreç başında settings.json'dan okunur.

from app.theme.themes import THEMES, saved_theme_name

THEME_NAME = saved_theme_name()
globals().update(THEMES[THEME_NAME])
