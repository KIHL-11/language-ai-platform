"""AI Chunk 2.0 pure-algorithm validation slice (CH-01/02 design revision).

Isolated on purpose: no production module imports this package, it performs
no I/O, calls no AI provider and writes nothing. It reuses two pure
production functions so its results stay comparable with the real system:
`prepare_chunk_text` (Study Bank chunk identity) and
`AiChunkOrganizerService._ground_phrase` (source grounding).

See docs/ai-chunk-2/FULL-TRANSCRIPT-RANKING-SPEC.md.
"""
