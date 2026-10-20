from services.schemas.speaking import TargetUsage
from services.shadowing.alignment import normalize_tokens


def contains_phrase(transcript: str, phrase: str) -> bool:
    haystack, needle = normalize_tokens(transcript), normalize_tokens(phrase)
    if not needle or len(needle) > len(haystack):
        return False
    return any(haystack[index:index + len(needle)] == needle for index in range(len(haystack) - len(needle) + 1))


def record_target_usage(usages: list[TargetUsage], transcript: str, turn: int) -> list[TargetUsage]:
    evidence = " ".join(transcript.split())[:500]
    result = []
    for usage in usages:
        if not contains_phrase(transcript, usage.text):
            result.append(usage)
            continue
        snippets = list(usage.evidence)
        if evidence and evidence not in snippets and len(snippets) < 3:
            snippets.append(evidence)
        result.append(usage.model_copy(update={
            "use_count": usage.use_count + 1,
            "first_used_turn": usage.first_used_turn or turn,
            "last_used_turn": turn,
            "evidence": snippets,
        }))
    return result
