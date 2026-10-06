from services.ai.lesson_analyzer import analyze_sentences


def translate_sentences(
    sentences: list[dict] | list[str],
    target_language: str = "zh-CN",
    source_language: str = "en",
    llm_client=None,
) -> list[dict]:
    """Compatibility entry point backed by the batched lesson analyzer."""
    normalized = []
    for index, sentence in enumerate(sentences, start=1):
        if isinstance(sentence, str):
            normalized.append(
                {"id": index, "text": sentence, "start": 0.0, "end": 0.0}
            )
        else:
            normalized.append(sentence)

    return analyze_sentences(
        normalized,
        source_language=source_language,
        target_language=target_language,
        llm_client=llm_client,
    )
