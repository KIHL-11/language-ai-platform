import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def prepared_upload_wav(raw: bytes, suffix: str):
    with tempfile.TemporaryDirectory(prefix="language-ai-shadowing-attempt-") as directory:
        root = Path(directory)
        input_path = root / f"attempt{suffix if suffix.startswith('.') else '.bin'}"
        output_path = root / "attempt.wav"
        input_path.write_bytes(raw)
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(input_path),
                "-af", "highpass=f=80,dynaudnorm=f=150:g=7",
                "-ar", "16000", "-ac", "1", "-f", "wav", str(output_path),
            ],
            check=True,
            capture_output=True,
        )
        yield output_path


@contextmanager
def prepared_reference_segment(source: Path, start: float, end: float):
    if start < 0 or end <= start:
        raise ValueError("Invalid shadowing sentence range")
    with tempfile.TemporaryDirectory(prefix="language-ai-shadowing-reference-") as directory:
        output_path = Path(directory) / "reference.wav"
        subprocess.run(
            [
                "ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(source),
                "-t", f"{end - start:.3f}", "-ar", "16000", "-ac", "1",
                "-f", "wav", str(output_path),
            ],
            check=True,
            capture_output=True,
        )
        yield output_path
