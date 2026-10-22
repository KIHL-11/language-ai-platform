import os
import threading
from dataclasses import dataclass

from services.schemas.shadowing import TimedWord


_models: dict[str, object] = {}
_model_init_lock = threading.Lock()


def model_name_for_language(source_language: str) -> str:
    if source_language == "en":
        return os.getenv("WHISPER_MODEL_EN", os.getenv("WHISPER_MODEL", "base.en"))
    if source_language == "de":
        return os.getenv("WHISPER_MODEL_MULTI", "base")
    raise ValueError(f"Unsupported source language: {source_language}")


def shadowing_model_name_for_language(source_language: str) -> str:
    if source_language == "en":
        return os.getenv("WHISPER_SHADOWING_MODEL_EN", "small.en")
    if source_language == "de":
        return os.getenv("WHISPER_SHADOWING_MODEL_MULTI", "small")
    raise ValueError(f"Unsupported source language: {source_language}")


def _get_model(model_name: str):
    model = _models.get(model_name)
    if model is None:
        with _model_init_lock:
            model = _models.get(model_name)
            if model is None:
                from faster_whisper import WhisperModel

                model = WhisperModel(
                    model_name,
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=6,
                )
                _models[model_name] = model
    return model


def get_local_whisper_model(source_language: str):
    return _get_model(model_name_for_language(source_language))


def get_shadowing_whisper_model(source_language: str):
    return _get_model(shadowing_model_name_for_language(source_language))


@dataclass(frozen=True)
class LocalTranscription:
    text: str
    words: list[TimedWord]
    model_config: str | None = None


def transcribe_local_words(path: str, source_language: str) -> LocalTranscription:
    model = get_local_whisper_model(source_language)
    segments, _ = model.transcribe(
        path,
        language=source_language,
        beam_size=1,
        condition_on_previous_text=False,
        word_timestamps=True,
    )
    texts: list[str] = []
    words: list[TimedWord] = []
    for segment in segments:
        text = str(segment.text).strip()
        if text:
            texts.append(text)
        for word in segment.words or []:
            token = str(word.word).strip()
            if token:
                words.append(TimedWord(
                    word=token,
                    start=max(0, float(word.start)),
                    end=max(float(word.start), float(word.end)),
                ))
    return LocalTranscription(text=" ".join(texts).strip(), words=words)


def transcribe_shadowing_words(path: str, source_language: str) -> LocalTranscription:
    """Accuracy-oriented local ASR for short scored attempts and references."""
    model_name = shadowing_model_name_for_language(source_language)
    model = get_shadowing_whisper_model(source_language)
    segments, _ = model.transcribe(
        path,
        language=source_language,
        beam_size=5,
        temperature=0,
        condition_on_previous_text=False,
        word_timestamps=True,
    )
    texts: list[str] = []
    words: list[TimedWord] = []
    for segment in segments:
        text = str(segment.text).strip()
        if text:
            texts.append(text)
        for word in segment.words or []:
            token = str(word.word).strip()
            if token:
                words.append(TimedWord(
                    word=token,
                    start=max(0, float(word.start)),
                    end=max(float(word.start), float(word.end)),
                ))
    display_name = model_name.replace("\\", "/").rsplit("/", 1)[-1]
    return LocalTranscription(
        text=" ".join(texts).strip(),
        words=words,
        model_config=f"{display_name} · beam 5 · local",
    )
