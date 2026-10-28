"""Build-time accounting for Kaikki translation retention and sense alignment.

Schema v1 does not store translation provenance, so these counters can only be
collected while importing. They never influence what the importer writes.
"""

from collections import Counter

SENSE_LOCAL_OUTCOMES = ("retained", "deduplicated", "truncated")
ENTRY_ACCEPTED = (
    "unique_multi_token",
    "reliable_single_token_head",
    "unscoped_single_sense",
)
ENTRY_REJECTED = (
    "ambiguous_or_tied",
    "zero_evidence",
    "generic_stopword_or_lemma_only",
    "single_sense_mismatch",
    "unsupported_morphology",
    "weak_single_token_evidence",
    "over_limit",
)
RECORD_COUNTERS = (
    "lines_read",
    "blank_lines",
    "skipped_language",
    "skipped_lemma",
    "entries_imported",
    "senses_imported",
    "senses_without_definition",
)


class ImportTelemetry:
    """Counters keyed by source edition and translation target language."""

    def __init__(self) -> None:
        self._sense_local: dict[tuple[str, str], Counter] = {}
        self._entry_level: dict[tuple[str, str], Counter] = {}
        self._records: dict[str, Counter] = {}

    def _pair(self, store: dict, edition: str, target: str) -> Counter:
        return store.setdefault((edition, target), Counter())

    def record(self, edition: str, counter: str, amount: int = 1) -> None:
        if counter not in RECORD_COUNTERS:
            raise ValueError(f"unknown record counter: {counter}")
        self._records.setdefault(edition, Counter())[counter] += amount

    def sense_local(self, edition: str, target: str, outcome: str) -> None:
        if outcome not in SENSE_LOCAL_OUTCOMES:
            raise ValueError(f"unknown sense-local outcome: {outcome}")
        counter = self._pair(self._sense_local, edition, target)
        counter["seen"] += 1
        counter[outcome] += 1

    def sense_local_discarded_with_invalid_sense(self, edition: str, target: str) -> None:
        """Translations nested under a sense that has no usable definition."""
        self._pair(self._sense_local, edition, target)["discarded_with_invalid_sense"] += 1

    def entry_level(self, edition: str, target: str, outcome: str) -> None:
        if outcome not in ENTRY_ACCEPTED and outcome not in ENTRY_REJECTED:
            raise ValueError(f"unknown entry-level outcome: {outcome}")
        counter = self._pair(self._entry_level, edition, target)
        counter["seen"] += 1
        counter[outcome] += 1

    def entry_level_duplicate(self, edition: str, target: str) -> None:
        """An aligned translation that already existed on its sense."""
        self._pair(self._entry_level, edition, target)["accepted_duplicate_of_existing"] += 1

    @staticmethod
    def _sense_local_record(counter: Counter) -> dict[str, int]:
        return {"seen": counter["seen"], **{key: counter[key] for key in SENSE_LOCAL_OUTCOMES}}

    @staticmethod
    def _entry_level_record(counter: Counter) -> dict:
        accepted = {key: counter[key] for key in ENTRY_ACCEPTED}
        accepted["total"] = sum(accepted.values())
        rejected = {key: counter[key] for key in ENTRY_REJECTED}
        rejected["total"] = sum(rejected.values())
        return {"seen": counter["seen"], "accepted": accepted, "rejected": rejected}

    def manifest_telemetry(self) -> dict:
        """Aggregate counters in the manifest v1 ``alignment_telemetry`` shape."""
        sense_total = sum(self._sense_local.values(), Counter())
        entry_total = sum(self._entry_level.values(), Counter())
        return {
            "instrumentation_status": "complete",
            "sense_local": self._sense_local_record(sense_total),
            "entry_level": self._entry_level_record(entry_total),
        }

    def report(self) -> dict:
        """Detailed edition/target breakdown for the build report."""
        pairs = sorted(set(self._sense_local) | set(self._entry_level))
        by_pair = []
        for edition, target in pairs:
            sense = self._sense_local.get((edition, target), Counter())
            entry = self._entry_level.get((edition, target), Counter())
            by_pair.append({
                "edition": edition,
                "target_language": target,
                "sense_local": {
                    **self._sense_local_record(sense),
                    "discarded_with_invalid_sense": sense["discarded_with_invalid_sense"],
                },
                "entry_level": {
                    **self._entry_level_record(entry),
                    "accepted_duplicate_of_existing": entry["accepted_duplicate_of_existing"],
                },
            })
        records = {
            edition: {key: counter[key] for key in RECORD_COUNTERS}
            for edition, counter in sorted(self._records.items())
        }
        return {
            "telemetry_version": 1,
            "records": records,
            "by_edition_and_target": by_pair,
            "totals": self.manifest_telemetry(),
        }
