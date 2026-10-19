import re
import unicodedata
from dataclasses import dataclass

from services.schemas.shadowing import AlignedWord, TimedWord


TOKEN = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)


def normalize_tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return [token.replace("’", "'") for token in TOKEN.findall(normalized)]


@dataclass(frozen=True)
class AlignmentStep:
    status: str
    target: str | None
    recognized: str | None
    target_index: int | None
    recognized_index: int | None


def align_tokens(target: list[str], recognized: list[str]) -> list[AlignmentStep]:
    rows, columns = len(target) + 1, len(recognized) + 1
    costs = [[0] * columns for _ in range(rows)]
    operations = [[""] * columns for _ in range(rows)]
    for i in range(1, rows):
        costs[i][0], operations[i][0] = i, "missed"
    for j in range(1, columns):
        costs[0][j], operations[0][j] = j, "extra"
    for i in range(1, rows):
        for j in range(1, columns):
            if target[i - 1] == recognized[j - 1]:
                costs[i][j], operations[i][j] = costs[i - 1][j - 1], "matched"
                continue
            choices = [
                (costs[i - 1][j - 1] + 1, 0, "substituted"),
                (costs[i - 1][j] + 1, 1, "missed"),
                (costs[i][j - 1] + 1, 2, "extra"),
            ]
            cost, _, operation = min(choices)
            costs[i][j], operations[i][j] = cost, operation
    result: list[AlignmentStep] = []
    i, j = len(target), len(recognized)
    while i or j:
        operation = operations[i][j]
        if operation in {"matched", "substituted"}:
            result.append(AlignmentStep(operation, target[i - 1], recognized[j - 1], i - 1, j - 1))
            i, j = i - 1, j - 1
        elif operation == "missed":
            result.append(AlignmentStep(operation, target[i - 1], None, i - 1, None))
            i -= 1
        else:
            result.append(AlignmentStep("extra", None, recognized[j - 1], None, j - 1))
            j -= 1
    return list(reversed(result))


def public_alignment(steps: list[AlignmentStep]) -> list[AlignedWord]:
    return [AlignedWord(status=step.status, target=step.target, recognized=step.recognized) for step in steps]


def normalize_timed_words(words: list[TimedWord]) -> list[TimedWord]:
    result: list[TimedWord] = []
    for timed in words:
        for token in normalize_tokens(timed.word):
            result.append(TimedWord(word=token, start=timed.start, end=max(timed.start, timed.end)))
    return result
