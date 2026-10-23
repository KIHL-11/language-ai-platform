from typing import Protocol

from services.dictionary.provider import ProviderEntry
from services.schemas.dictionary import DictionaryPronunciation


class PronunciationProvider(Protocol):
    def options(self, entry: ProviderEntry) -> list[DictionaryPronunciation]: ...


class DictionaryAndTTSPronunciationProvider:
    """Expose source recordings first and labeled Edge TTS fallbacks second."""

    def options(self, entry: ProviderEntry) -> list[DictionaryPronunciation]:
        values = [
            DictionaryPronunciation(
                ipa=value.ipa,
                accent=value.accent,
                source=entry.source_name,
                source_url=entry.source_url,
                audio_url=value.audio_url,
                audio_available=bool(value.audio_url),
            )
            for value in entry.pronunciations[:6]
        ]
        accents = ["en-US", "en-GB"] if entry.language == "en" else ["de-DE"]
        for accent in accents:
            aliases = {
                accent.casefold(),
                "us" if accent == "en-US" else "uk" if accent == "en-GB" else "standard german",
            }
            if any(
                value.accent.casefold() in aliases and value.audio_available
                for value in values
            ):
                continue
            values.append(DictionaryPronunciation(
                accent=accent,
                source="Microsoft Edge Text-to-Speech",
                audio_available=True,
                tts_fallback=True,
            ))
        return values
