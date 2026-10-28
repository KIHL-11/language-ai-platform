"""Phase 7B: evaluation builds that never publish READY, and the data-quality gate."""

import json
from pathlib import Path

import pytest

from migration_support import NOW, MigrationEnvironment, lifecycle, release_dir
from services.dictionary.cutover import CutoverError, run_cutover
from services.dictionary.release_prepare import PrepareError

MIGRATION = Path(__file__).parent / "fixtures" / "migration"
CASES = {
    "data_quality_version": 1, "scope": "synthetic_fixture", "approved": True,
    "cases": [
        {"id": "get-understand", "language": "en", "lemma": "get",
         "sense": {"definition_contains_any": ["understand"]},
         "chinese_allowed": ["明白"], "chinese_forbidden": ["得到"]},
        {"id": "bank-river", "language": "en", "lemma": "bank",
         "sense": {"definition_contains_any": ["river"]}, "chinese_forbidden": ["银行"]},
    ],
}


@pytest.fixture()
def env(tmp_path):
    return MigrationEnvironment(tmp_path)


def _cases(tmp_path, payload=CASES):
    path = tmp_path / "dq.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _strict_gold(tmp_path, env):
    gold = json.loads(env.gold.read_text())
    gold["cases"][0]["expect"]["chinese_any"] = ["得到"]  # force a context failure
    path = tmp_path / "gold-fail.json"
    path.write_text(json.dumps(gold, ensure_ascii=False), encoding="utf-8")
    return path


def _cutover_refused(env, build_id):
    controller, backend = lifecycle(env)
    with pytest.raises(CutoverError, match="READY|published"):
        run_cutover(layout=env.layout, source_root=env.sources, build_id=build_id,
                    confirmation=f"CUTOVER {build_id}", controller=controller, client=backend,
                    plist_path=env.plist, legacy_database=env.legacy, now=NOW, minimum_free_bytes=0,
                    health_attempts=1, sleep=lambda s: None)
    assert controller.calls == []


def test_withheld_build_with_failing_gate_reports_everything_and_never_writes_ready(env, tmp_path):
    legacy = env.legacy_release()
    result = env.prepare(legacy, gold_set=_strict_gold(tmp_path, env), withhold_ready=True,
                         data_quality_cases=_cases(tmp_path))
    release = release_dir(env, result["build_id"])
    assert result["action"] == "evaluated_ready_withheld"
    assert result["gate_failures"] == ["1 gold case(s) failed"]
    assert result["ready_eligible"] is False and "non-validated" in result["ready_eligibility_reason"]
    assert not (release / "READY").exists() and not (release / "build-failure.json").exists()
    manifest = json.loads((release / "manifest.json").read_text())
    assert manifest["status"] == "failed"
    readiness = json.loads((release / "readiness.json").read_text())
    assert readiness["ready_written"] is False and readiness["ready_eligible"] is False
    quality = json.loads((release / "quality-report.json").read_text())
    assert quality["dictionary_data_quality"]["data_quality_failures"] == 0
    assert quality["broadcast_profile"]["en"]["entries_with_chinese"] > 0
    assert quality["importer_recovery_enabled"] is True
    _cutover_refused(env, result["build_id"])


def test_withheld_build_that_passes_every_gate_still_has_no_ready(env, tmp_path):
    legacy = env.legacy_release()
    result = env.prepare(legacy, withhold_ready=True, data_quality_cases=_cases(tmp_path))
    release = release_dir(env, result["build_id"])
    assert result["gate_failures"] == [] and result["ready_eligible"] is True
    assert json.loads((release / "manifest.json").read_text())["status"] == "candidate_validated"
    assert not (release / "READY").exists()
    _cutover_refused(env, result["build_id"])


def test_data_quality_failure_blocks_a_normal_build(env, tmp_path):
    legacy = env.legacy_release()
    payload = json.loads(json.dumps(CASES))
    payload["cases"][0]["chinese_forbidden"] = ["明白"]
    with pytest.raises(PrepareError, match="data-quality"):
        env.prepare(legacy, data_quality_cases=_cases(tmp_path, payload))
    release = next(path for path in env.layout.releases.iterdir() if path.name != legacy)
    assert not (release / "READY").exists() and (release / "build-failure.json").exists()


def test_strict_mode_build_is_available_for_same_snapshot_comparison(env, tmp_path):
    legacy = env.legacy_release()
    result = env.prepare(legacy, withhold_ready=True, recovery=False)
    quality = json.loads((release_dir(env, result["build_id"]) / "quality-report.json").read_text())
    assert quality["importer_recovery_enabled"] is False


def test_strict_importer_builds_cannot_be_normal_releases(env):
    legacy = env.legacy_release()
    with pytest.raises(PrepareError, match="evaluation-only"):
        env.prepare(legacy, recovery=False)
    assert sorted(path.name for path in env.layout.releases.iterdir()) == [legacy]
    strict = env.prepare(legacy, withhold_ready=True, recovery=False, dry_run=True)
    repaired = env.prepare(legacy, withhold_ready=True, dry_run=True)
    assert strict["build_id"] != repaired["build_id"]
