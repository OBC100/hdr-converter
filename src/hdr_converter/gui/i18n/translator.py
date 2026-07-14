"""JSON 国际化。"""

from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import QObject, QLocale, QSettings, pyqtSignal

_LOCALES_DIR = Path(__file__).resolve().parent / "locales"
_FALLBACK = "en"
SUPPORTED_LOCALES = (
    "en",
    "zh_CN",
    "zh_TW",
    "ko",
    "es",
    "ar",
    "id",
    "pt",
    "fr",
    "ja",
    "ru",
    "de",
)

_LOCALE_LABEL_KEYS: dict[str, str] = {
    "en": "lang.en",
    "zh_CN": "lang.zh",
    "zh_TW": "lang.zh_tw",
    "ko": "lang.ko",
    "es": "lang.es",
    "ar": "lang.ar",
    "id": "lang.id",
    "pt": "lang.pt",
    "fr": "lang.fr",
    "ja": "lang.ja",
    "ru": "lang.ru",
    "de": "lang.de",
}

_SYSTEM_LANG_MAP: dict[str, str] = {
    "zh": "zh_CN",
    "es": "es",
    "ar": "ar",
    "id": "id",
    "pt": "pt",
    "fr": "fr",
    "ja": "ja",
    "ru": "ru",
    "de": "de",
    "ko": "ko",
    "en": "en",
}

_translator: "Translator | None" = None


class Translator(QObject):
    language_changed = pyqtSignal(str)

    def __init__(self, locale: str | None = None) -> None:
        super().__init__()
        self._strings: dict[str, str] = {}
        self._locale = _FALLBACK
        self._catalogs: dict[str, dict[str, str]] = {}
        self._load_catalogs()
        saved = QSettings().value("ui/locale")
        initial = locale or (str(saved) if saved else self._system_locale())
        self.set_locale(initial, persist=False)

    def _load_catalogs(self) -> None:
        for code in SUPPORTED_LOCALES:
            path = _LOCALES_DIR / f"{code}.json"
            with path.open(encoding="utf-8-sig") as f:
                self._catalogs[code] = json.load(f)

    @staticmethod
    def _system_locale() -> str:
        name = QLocale.system().name()
        if name in ("zh_TW", "zh_HK", "zh_MO"):
            return "zh_TW"
        if name.startswith("zh"):
            return "zh_CN"
        lang = name.split("_")[0].lower()
        return _SYSTEM_LANG_MAP.get(lang, _FALLBACK)

    @property
    def locale(self) -> str:
        return self._locale

    @property
    def supported_locales(self) -> tuple[str, ...]:
        return SUPPORTED_LOCALES

    def set_locale(self, locale: str, *, persist: bool = True) -> None:
        code = locale if locale in SUPPORTED_LOCALES else _FALLBACK
        if code == self._locale and self._strings:
            return
        self._locale = code
        self._strings = dict(self._catalogs.get(code, self._catalogs[_FALLBACK]))
        if persist:
            QSettings().setValue("ui/locale", code)
        self.language_changed.emit(code)

    def tr(self, key: str, **kwargs: object) -> str:
        text = self._strings.get(key, self._catalogs[_FALLBACK].get(key, key))
        if kwargs:
            try:
                return text.format(**kwargs)
            except (KeyError, ValueError):
                return text
        return text


def locale_label_key(code: str) -> str:
    return _LOCALE_LABEL_KEYS.get(code, "lang.en")


def get_translator() -> Translator:
    global _translator
    if _translator is None:
        _translator = Translator()
    return _translator


def tr(key: str, **kwargs: object) -> str:
    return get_translator().tr(key, **kwargs)
