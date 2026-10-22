import re


_NOISE_LABEL = re.compile(
    r"\[\s*(?:music|musik|singing|singen|applause|laughter|cheering|inaudible)\s*\]",
    re.IGNORECASE,
)


def clean_transcript_text(text: str) -> str:
    """Remove known non-lexical subtitle labels without changing stored text."""
    return " ".join(_NOISE_LABEL.sub(" ", text).split())
