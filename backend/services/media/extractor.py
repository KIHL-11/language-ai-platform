import glob
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from yt_dlp import YoutubeDL
from yt_dlp.utils import download_range_func


BACKEND_DIR = Path(__file__).resolve().parents[2]
MEDIA = BACKEND_DIR / "media"
MEDIA.mkdir(exist_ok=True)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_whisper_models: dict[str, object] = {}


def get_subtitle_languages(source_language: str) -> list[str]:
    if source_language == "en":
        return ["en", "en-US", "en-GB", "en-orig"]
    if source_language == "de":
        return ["de", "de-DE", "de-orig"]
    raise ValueError(f"Unsupported source language: {source_language}")


def get_whisper_model(source_language: str) -> str:
    if source_language == "en":
        return os.getenv("WHISPER_MODEL_EN", os.getenv("WHISPER_MODEL", "base.en"))
    if source_language == "de":
        return os.getenv("WHISPER_MODEL_MULTI", "base")
    raise ValueError(f"Unsupported source language: {source_language}")


def normalize_sentences(sentences: list[dict]) -> list[dict]:
    normalized = []
    for index, sentence in enumerate(sentences, start=1):
        normalized.append(
            {
                "id": index,
                "text": str(sentence.get("text", "")).strip(),
                "start": float(sentence.get("start", 0.0)),
                "end": float(sentence.get("end", 0.0)),
            }
        )
    return normalized


def _vtt_to_sec(value: str) -> float:
    value = value.replace(",", ".")
    hours, minutes, seconds = value.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def parse_vtt(path: str) -> list[tuple[float, float, str]]:
    inline_ts = re.compile(r"<\d+:\d+:\d+[.,]\d+>")
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    timestamp = re.compile(
        r"(\d+:\d+:\d+[.,]\d+)\s*-->\s*(\d+:\d+:\d+[.,]\d+)"
    )
    cues = []
    for block in re.split(r"\n\n+", text):
        match = None
        tagged, plain = [], []
        for line in block.splitlines():
            hit = timestamp.search(line)
            if hit:
                match = hit
            elif (
                line.strip()
                and "WEBVTT" not in line
                and not line.strip().isdigit()
                and not line.startswith(("Kind:", "Language:", "NOTE"))
            ):
                has_tag = bool(inline_ts.search(line))
                clean = re.sub(r"<[^>]+>", "", line).strip()
                if clean:
                    (tagged if has_tag else plain).append(clean)
        lines = tagged if tagged else plain
        if match and lines:
            cues.append(
                (
                    _vtt_to_sec(match.group(1)),
                    _vtt_to_sec(match.group(2)),
                    " ".join(lines),
                )
            )
    return cues


def cues_to_sentences(
    cues: list[tuple[float, float, str]], offset: float = 0.0
) -> list[dict]:
    deduplicated = []
    previous = None
    for start, end, text in cues:
        text = text.strip()
        if not text or text == previous:
            continue
        previous = text
        deduplicated.append((start, end, text))

    sentences, current = [], None
    for start, end, text in deduplicated:
        if current is None:
            current = [start, end, text]
        else:
            current[1] = end
            current[2] = (current[2] + " " + text).strip()
        if re.search(r'[.!?]["\')]?\s*$', current[2]) or len(current[2]) > 160:
            sentences.append(
                {
                    "text": current[2],
                    "start": round(max(0, current[0] - offset), 2),
                    "end": round(max(0, current[1] - offset), 2),
                }
            )
            current = None
    if current:
        sentences.append(
            {
                "text": current[2],
                "start": round(max(0, current[0] - offset), 2),
                "end": round(max(0, current[1] - offset), 2),
            }
        )
    return sentences


def whisper_transcribe(path: str, source_language: str) -> list[dict]:
    model_name = get_whisper_model(source_language)
    model = _whisper_models.get(model_name)
    if model is None:
        from faster_whisper import WhisperModel

        model = WhisperModel(
            model_name, device="cpu", compute_type="int8", cpu_threads=6
        )
        _whisper_models[model_name] = model

    segments, _ = model.transcribe(
        path,
        language=source_language,
        beam_size=1,
        condition_on_previous_text=False,
        word_timestamps=True,
    )
    sentences, words, current_start, last_end = [], [], None, 0.0
    for segment in segments:
        for word in segment.words or []:
            if current_start is None:
                current_start = word.start
            words.append(word.word)
            last_end = word.end
            text = "".join(words).strip()
            if re.search(r'[.!?]["\')]?$', word.word.strip()) or len(text) > 160:
                if text:
                    sentences.append(
                        {
                            "text": text,
                            "start": round(current_start, 2),
                            "end": round(word.end, 2),
                        }
                    )
                words, current_start = [], None
    if words:
        text = "".join(words).strip()
        if text:
            sentences.append(
                {
                    "text": text,
                    "start": round(current_start or 0.0, 2),
                    "end": round(last_end, 2),
                }
            )
    if sentences:
        return sentences

    segments, _ = model.transcribe(
        path,
        language=source_language,
        beam_size=1,
        condition_on_previous_text=False,
    )
    return [
        {"text": item.text.strip(), "start": round(item.start, 2), "end": round(item.end, 2)}
        for item in segments
        if item.text.strip()
    ]


def separate_vocals(audio_path: str) -> str:
    outdir = MEDIA / "_demucs"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "demucs",
            "--two-stems=vocals",
            "--mp3",
            "-o",
            str(outdir),
            audio_path,
        ],
        check=True,
        capture_output=True,
    )
    base = os.path.splitext(os.path.basename(audio_path))[0]
    found = glob.glob(str(outdir / "*" / base / "vocals.mp3"))
    if not found:
        raise RuntimeError("Demucs did not produce a vocals file")
    destination = MEDIA / f"{base}.vocals.mp3"
    if destination.exists():
        destination.unlink()
    shutil.move(found[0], destination)
    shutil.rmtree(outdir, ignore_errors=True)
    return str(destination)


def _select_vtt(paths: list[str], source_language: str) -> str:
    priorities = get_subtitle_languages(source_language)
    for language in priorities:
        suffix = f".{language}.vtt".lower()
        for path in paths:
            if path.lower().endswith(suffix):
                return path
    return sorted(paths)[0]


def extract_media(
    url: str,
    start: float | None,
    end: float | None,
    vocals: bool = False,
    source_language: str = "en",
) -> dict:
    subtitle_languages = get_subtitle_languages(source_language)
    video_id = uuid.uuid4().hex[:10]
    is_bilibili = "bilibili.com" in url or "b23.tv" in url
    accept_language = (
        "de-DE,de;q=0.9,en;q=0.8" if source_language == "de" else "en-US,en;q=0.9,zh-CN;q=0.8"
    )
    options = {
        "format": "18/bestaudio/best",
        "outtmpl": str(MEDIA / f"{video_id}.%(ext)s"),
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": subtitle_languages,
        "subtitlesformat": "vtt",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "128",
            }
        ],
        "quiet": False,
        "no_warnings": False,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 5,
        "http_headers": {
            "User-Agent": UA,
            "Accept-Language": accept_language,
            "Referer": "https://www.bilibili.com/" if is_bilibili else url,
        },
        "extractor_args": {"youtube": {"player_client": ["android"]}},
    }

    cookie_file = os.getenv("YT_COOKIES_FILE")
    if not cookie_file:
        candidate = BACKEND_DIR / "cookies.txt"
        if candidate.exists():
            cookie_file = str(candidate)
    if cookie_file and os.path.exists(cookie_file):
        options["cookiefile"] = cookie_file

    cookie_browser = os.getenv("YT_COOKIES_BROWSER")
    if cookie_browser:
        options["cookiesfrombrowser"] = (cookie_browser,)

    if start is not None and end is not None and end > start:
        options["download_ranges"] = download_range_func(None, [(start, end)])
        options["force_keyframes_at_cuts"] = True

    with YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=True)

    mp3_paths = glob.glob(str(MEDIA / f"{video_id}*.mp3"))
    audio_file = os.path.basename(mp3_paths[0]) if mp3_paths else None
    offset = start if start is not None and end is not None else 0.0
    vocals_ok = None

    if vocals and audio_file:
        try:
            vocals_path = separate_vocals(str(MEDIA / audio_file))
            audio_file = os.path.basename(vocals_path)
            vocals_ok = True
        except Exception:
            vocals_ok = False

    sentences = []
    source = None
    vtt_paths = glob.glob(str(MEDIA / f"{video_id}*.vtt"))
    if vtt_paths:
        cues = parse_vtt(_select_vtt(vtt_paths, source_language))
        if start is not None and end is not None:
            cues = [cue for cue in cues if cue[1] > start and cue[0] < end]
        sentences = cues_to_sentences(cues, offset)
        if sentences:
            source = "subtitles"

    if not sentences and audio_file:
        sentences = whisper_transcribe(str(MEDIA / audio_file), source_language)
        source = "whisper"

    return {
        "title": info.get("title", ""),
        "audio_url": f"/media/{audio_file}" if audio_file else None,
        "sentences": normalize_sentences(sentences),
        "source": source,
        "vocals": vocals_ok,
    }


run_extract = extract_media
