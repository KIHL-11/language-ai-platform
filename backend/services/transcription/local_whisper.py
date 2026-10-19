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


def get_local_whisper_model(source_language: str):
    model_name = model_name_for_language(source_language)
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


@dataclass(frozen=True)
class LocalTranscription:
    text: str
    words: list[TimedWord]


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
