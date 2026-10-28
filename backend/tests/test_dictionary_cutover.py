"""Cutover, rollback, LaunchAgent protection, and post-switch fault injection.

The LaunchAgent and backend are mocks (see migration_support); real launchd
behaviour requires separate real-Mac validation.
"""

import json
import os
import plistlib
import stat
from pathlib import Path

import pytest

from migration_support import (
    NOW,
    MigrationEnvironment,
    current_target,
    lifecycle,
    make_writable,
    release_dir,
    tree_state,
    write_launch_agent,
)
from services.dictionary.cutover import (
    MIGRATIONS_DIRNAME,
    CutoverError,
    learning_snapshot,
    run_cutover,
    run_legacy_conversion,
    run_rollback,
)
from services.dictionary.launch_agent import (
    LaunchAgentError,
    LaunchctlController,
    migrated_plist,
    parse_launchctl_environment,
)
from services.dictionary.release_store import current_build_id, switch_current


@pytest.fixture()
def env(tmp_path):
    return MigrationEnvironment(tmp_path)


@pytest.fixture()
def prepared(env):
    legacy = env.legacy_release()
    candidate = env.prepare(legacy)["build_id"]
    return env, legacy, candidate


def _cutover(env, build_id, controller, backend, **overrides):
    arguments = {
        "layout": env.layout,
        "source_root": env.sources,
        "build_id": build_id,
        "confirmation": f"CUTOVER {build_id}",
        "controller": controller,
        "client": backend,
        "plist_path": env.plist,
        "legacy_database": env.legacy,
        "now": NOW,
        "minimum_free_bytes": 0,
        "health_attempts": 2,
        "sleep": lambda seconds: None,
    }
    arguments.update(overrides)
    return run_cutover(**arguments)


def _rollback(env, build_id, controller, backend, **overrides):
    arguments = {
        "layout": env.layout,
        "build_id": build_id,
        "confirmation": f"ROLLBACK {build_id}",
        "controller": controller,
        "client": backend,
        "plist_path": env.plist,
        "now": NOW,
        "health_attempts": 2,
        "sleep": lambda seconds: None,
    }
    arguments.update(overrides)
    return run_rollback(**arguments)


def _result(env):
    records = sorted((env.storage / MIGRATIONS_DIRNAME).iterdir())
    return json.loads((records[-1] / "result.json").read_text()), records[-1]


# ------------------------------------------------------------- happy paths

def test_cutover_dry_run_changes_nothing(prepared):
    env, legacy, candidate = prepared
    controller, backend = lifecycle(env)
    before = tree_state(env.root)
    plan = _cutover(env, candidate, controller, backend, dry_run=True, confirmation=None)
    assert plan["action"] == "would_cutover"
    assert plan["legacy_mode"] is True and plan["plist_change_required"] is True
    assert controller.calls == []
    assert tree_state(env.root) == before


def test_cutover_requires_exact_owner_confirmation(prepared):
    env, _, candidate = prepared
    controller, backend = lifecycle(env)
    for confirmation in (None, "yes", f"CUTOVER {candidate} "):
        with pytest.raises(CutoverError, match="confirmation"):
            _cutover(env, candidate, controller, backend, confirmation=confirmation)
    assert controller.calls == []


def test_first_cutover_switches_release_and_launch_agent(prepared):
    env, legacy, candidate = prepared
    controller, backend = lifecycle(env)
    legacy_bytes = env.legacy.read_bytes()
    learning = learning_snapshot(env.storage)
    assert backend.get_json("/api/dictionary/status")["release_id"] == "unversioned"

    result = _cutover(env, candidate, controller, backend)

    assert result["action"] == "cutover_complete"
    assert current_target(env) == f"dictionary_releases/{candidate}"
    environment = plistlib.loads(env.plist.read_bytes())["EnvironmentVariables"]
    assert environment["DICTIONARY_DB_PATH"] == str(env.storage / "dictionary_current" / "dictionary.sqlite3")
    assert environment["DICTIONARY_FORMS_INDEX_PATH"] == str(env.storage / "dictionary_current" / "dictionary.forms.sqlite3")
    assert environment["PATH"] == "/usr/bin:/bin"
    assert backend.get_json("/api/dictionary/status") == {
        "configured": True, "release_id": candidate, "forms_index_status": "available",
    }
    assert controller.calls == ["stop", "start"]
    assert env.legacy.read_bytes() == legacy_bytes
    assert learning_snapshot(env.storage) == learning
    record, path = _result(env)
    assert record["result"] == "complete"
    backup = path / "launch-agent.before.plist"
    assert backup.read_bytes() == env.original_plist
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.stat().st_mode) == 0o700
    for evidence in path.iterdir():
        text = evidence.read_text(errors="ignore")
        assert "旧释义" not in text and "card-1" not in text


def test_second_cutover_and_explicit_rollback(prepared):
    env, legacy, first = prepared
    controller, backend = lifecycle(env)
    _cutover(env, first, controller, backend)
    second = env.prepare(first, now=NOW.replace(hour=13))["build_id"]
    plist_after_first = env.plist.read_bytes()

    result = _cutover(env, second, controller, backend)
    assert result["legacy_mode"] is False and result["plist_change_required"] is False
    assert env.plist.read_bytes() == plist_after_first
    assert backend.get_json("/api/dictionary/status")["release_id"] == second

    before = tree_state(env.root)
    assert _rollback(env, first, controller, backend, dry_run=True, confirmation=None)["action"] == "would_rollback"
    assert tree_state(env.root) == before

    _rollback(env, first, controller, backend)
    assert current_build_id(env.layout) == first
    assert backend.get_json("/api/dictionary/status")["release_id"] == first
    _rollback(env, legacy, controller, backend)
    assert backend.get_json("/api/dictionary/status")["release_id"] == legacy


def test_legacy_conversion_lifecycle_restarts_unchanged_configuration(env):
    controller, backend = lifecycle(env)
    plan = run_legacy_conversion(layout=env.layout, legacy_database=env.legacy, controller=controller,
                                 client=backend, plist_path=env.plist, dry_run=True)
    assert plan["action"] == "would_create" and controller.calls == []
    result = run_legacy_conversion(layout=env.layout, legacy_database=env.legacy, controller=controller,
                                   client=backend, plist_path=env.plist, now=NOW,
                                   health_attempts=2, sleep=lambda s: None)
    assert result["action"] == "created" and controller.calls == ["stop", "start"]
    assert env.plist.read_bytes() == env.original_plist
    assert backend.get_json("/api/dictionary/status")["release_id"] == "unversioned"


# ------------------------------------------------- post-switch fault injection

@pytest.mark.parametrize(
    "failure",
    ["restart", "health_never", "loaded_environment", "smoke_lookup", "after_switch", "before_smoke", "before_start"],
)
def test_post_stop_failures_roll_back_to_untouched_legacy_configuration(prepared, failure):
    env, legacy, candidate = prepared
    controller, backend = lifecycle(env)
    legacy_bytes = env.legacy.read_bytes()
    learning = learning_snapshot(env.storage)
    fault = None
    if failure == "restart":
        controller.fail_start_times = 1
    elif failure == "health_never":
        original_start = controller.start

        def start_then_crash(plist):
            original_start(plist)
            if plistlib.loads(Path(plist).read_bytes())["EnvironmentVariables"].get("DICTIONARY_DB_PATH"):
                backend.shutdown()
        controller.start = start_then_crash
    elif failure == "loaded_environment":
        controller.ignore_plist_environment = True
    elif failure == "smoke_lookup":
        original_boot = backend.boot

        def failing_boot(environment):
            original_boot(environment)
            backend.fail_lookup = bool(environment.get("DICTIONARY_DB_PATH"))
        backend.boot = failing_boot
    else:
        def fault(stage):
            if stage == failure:
                raise RuntimeError(f"injected {stage}")

    with pytest.raises(CutoverError, match="rolled back") as raised:
        _cutover(env, candidate, controller, backend, _fault=fault)

    assert raised.value.recovered is True
    assert env.plist.read_bytes() == env.original_plist
    assert backend.get_json("/api/dictionary/status")["release_id"] == "unversioned"
    assert env.legacy.read_bytes() == legacy_bytes
    assert learning_snapshot(env.storage) == learning
    # First-cutover recovery removes the link it created: back to pure legacy.
    assert not (env.storage / "dictionary_current").exists()
    assert not (env.storage / "dictionary_current").is_symlink()
    assert (release_dir(env, candidate) / "READY").exists()  # candidate preserved for diagnosis
    record, _ = _result(env)
    assert record["result"] == "rolled_back"


def test_crash_before_symlink_replace_leaves_legacy_current(prepared):
    env, _, candidate = prepared
    controller, backend = lifecycle(env)

    def fault(stage):
        if stage == "during_switch":
            raise RuntimeError("power loss before rename")

    with pytest.raises(CutoverError, match="rolled back"):
        _cutover(env, candidate, controller, backend, _fault=fault)
    assert current_build_id(env.layout) is None
    assert (env.storage / "dictionary_current.next").is_symlink()
    # A later switch replaces the stale .next link instead of failing.
    switch_current(env.layout, candidate)
    assert current_build_id(env.layout) == candidate


def test_rollback_failure_in_release_mode_restores_previous_release(prepared):
    env, legacy, candidate = prepared
    controller, backend = lifecycle(env)
    _cutover(env, candidate, controller, backend)
    controller.calls.clear()

    def fault(stage):
        if stage == "before_smoke":
            raise RuntimeError("smoke crashed")

    with pytest.raises(CutoverError, match="rolled back") as raised:
        _rollback(env, legacy, controller, backend, _fault=fault)
    assert raised.value.recovered is True
    assert current_build_id(env.layout) == candidate
    assert backend.get_json("/api/dictionary/status")["release_id"] == candidate


def test_learning_store_drift_blocks_acceptance_and_requires_investigation(prepared):
    env, legacy, candidate = prepared
    controller, backend = lifecycle(env)

    def fault(stage):
        if stage == "after_smoke":
            (env.storage / "study_bank.json").write_text('{"items":[]}', encoding="utf-8")

    with pytest.raises(CutoverError, match="investigate") as raised:
        _cutover(env, candidate, controller, backend, _fault=fault)
    assert raised.value.recovered is False
    assert backend.get_json("/api/dictionary/status")["release_id"] == "unversioned"
    record, path = _result(env)
    assert record["result"] == "rolled_back_learning_drift"
    assert (path / "learning-before.json").is_file() and (path / "learning-after.json").is_file()


def test_failed_recovery_is_reported_for_manual_intervention(prepared):
    env, _, candidate = prepared
    controller, backend = lifecycle(env)
    controller.fail_start_times = 5
    with pytest.raises(CutoverError, match="automatic recovery failed") as raised:
        _cutover(env, candidate, controller, backend)
    assert raised.value.recovered is False
    record, _ = _result(env)
    assert record["result"] == "recovery_failed"
    assert record["final_safe_stop_verified"] is True
    assert not controller.loaded and not backend.healthy()


def test_failed_recovery_after_old_service_restart_ends_safely_stopped(prepared):
    env, _, candidate = prepared
    controller, backend = lifecycle(env)

    def fault(stage):
        if stage == "before_smoke":
            backend.fail_lookup = True

    with pytest.raises(CutoverError, match="automatic recovery failed") as raised:
        _cutover(env, candidate, controller, backend, _fault=fault)
    assert raised.value.recovered is False
    assert not controller.loaded and not backend.healthy()
    record, _ = _result(env)
    assert record["final_safe_stop_verified"] is True


def test_backend_that_refuses_to_stop_is_never_switched(prepared):
    env, _, candidate = prepared
    controller, backend = lifecycle(env)
    controller.refuse_stop = True
    with pytest.raises(CutoverError):
        _cutover(env, candidate, controller, backend)
    assert current_build_id(env.layout) is None
    assert env.plist.read_bytes() == env.original_plist


# ------------------------------------------------ read-only refusal cases

@pytest.mark.parametrize(
    "damage",
    ["failure_marker", "ready_missing", "manifest_tampered", "rollback_sidecar_stale",
     "legacy_changed", "credential_plist", "public_host", "symlink_plist", "not_launch_agent",
     "already_current", "unknown_release"],
)
def test_unsafe_states_are_refused_before_stopping_the_service(prepared, damage, tmp_path):
    env, legacy, candidate = prepared
    controller, backend = lifecycle(env)
    build_id = candidate
    release = release_dir(env, candidate)
    if damage == "failure_marker":
        make_writable(release)
        (release / "build-failure.json").write_text("{}")
    elif damage == "ready_missing":
        make_writable(release)
        (release / "READY").unlink()
    elif damage == "manifest_tampered":
        make_writable(release)
        manifest = json.loads((release / "manifest.json").read_text())
        manifest["created_at"] = "2026-10-28T00:00:00Z"
        (release / "manifest.json").write_text(json.dumps(manifest))
    elif damage == "rollback_sidecar_stale":
        rollback = release_dir(env, legacy)
        make_writable(rollback)
        database = rollback / "dictionary.sqlite3"
        os.utime(database, ns=(database.stat().st_atime_ns, database.stat().st_mtime_ns + 5_000_000))
    elif damage == "legacy_changed":
        connection = __import__("sqlite3").connect(env.legacy)
        connection.execute("INSERT INTO metadata VALUES ('changed', 'yes')")
        connection.commit()
        connection.close()
    elif damage == "credential_plist":
        write_launch_agent(env.plist, environment={"OPENAI_API_KEY": "placeholder-not-a-secret"})
    elif damage == "public_host":
        write_launch_agent(env.plist, host="0.0.0.0")
    elif damage == "symlink_plist":
        real = tmp_path / "real.plist"
        real.write_bytes(env.plist.read_bytes())
        env.plist.unlink()
        env.plist.symlink_to(real)
    elif damage == "not_launch_agent":
        payload = plistlib.loads(env.plist.read_bytes())
        payload["Label"] = "com.example.other"
        env.plist.write_bytes(plistlib.dumps(payload))
    elif damage == "already_current":
        switch_current(env.layout, candidate)
    elif damage == "unknown_release":
        build_id = "20990101T000000Z-000000000000"
    before = tree_state(env.root)
    with pytest.raises(CutoverError) as raised:
        _cutover(env, build_id, controller, backend)
    assert raised.value.recovered is None
    assert controller.calls == []
    after = tree_state(env.root)
    after.pop("backend/storage/.dictionary-migration.lock", None)  # lock taken before preflight
    assert after == before
    serialized = str(raised.value)
    assert "placeholder-not-a-secret" not in serialized


def test_rollback_requires_versioned_launch_agent(prepared):
    env, legacy, candidate = prepared
    switch_current(env.layout, candidate)
    controller, backend = lifecycle(env)
    with pytest.raises(CutoverError, match="does not use dictionary_current"):
        _rollback(env, legacy, controller, backend)
    assert controller.calls == []


def test_link_without_versioned_launch_agent_is_inconsistent_and_refused(prepared):
    env, legacy, candidate = prepared
    switch_current(env.layout, legacy)
    controller, backend = lifecycle(env)
    with pytest.raises(CutoverError, match="does not use it"):
        _cutover(env, candidate, controller, backend)
    assert controller.calls == []


def test_rollback_without_current_link_is_refused(prepared):
    env, legacy, _ = prepared
    controller, backend = lifecycle(env)
    with pytest.raises(CutoverError, match="legacy mode"):
        _rollback(env, legacy, controller, backend)


def test_cutover_refuses_when_rollback_is_not_current(prepared):
    env, legacy, first = prepared
    controller, backend = lifecycle(env)
    _cutover(env, first, controller, backend)
    second = env.prepare(first, now=NOW.replace(hour=13))["build_id"]
    _rollback(env, legacy, controller, backend)
    with pytest.raises(CutoverError, match="not the current release"):
        _cutover(env, second, controller, backend)


# ---------------------------------------------------------- LaunchAgent unit

def test_launchctl_environment_parser_reads_only_the_environment_block():
    output = """gui/501/com.language-ai-platform = {
	active count = 1
	inherited environment = {
		SSH_AUTH_SOCK => /private/tmp/socket
	}
	environment = {
		PATH => /usr/bin:/bin
		DICTIONARY_DB_PATH => /Users/example/storage/dictionary_current/dictionary.sqlite3
	}
	state = running
}"""
    assert parse_launchctl_environment(output) == {
        "PATH": "/usr/bin:/bin",
        "DICTIONARY_DB_PATH": "/Users/example/storage/dictionary_current/dictionary.sqlite3",
    }
    with pytest.raises(LaunchAgentError):
        parse_launchctl_environment("environment = {\n KEY => value\n")


def test_launchctl_controller_issues_expected_commands_and_confirms_stop():
    calls = []
    state = {"loaded": True}

    class Result:
        def __init__(self, code, stdout=""):
            self.returncode = code
            self.stdout = stdout

    def runner(command, **kwargs):
        calls.append(command[1:])
        action = command[1]
        if action == "print":
            return Result(0 if state["loaded"] else 113, "environment = {\n\tA => b\n}\n")
        if action == "bootout":
            state["loaded"] = False
        if action == "bootstrap":
            state["loaded"] = True
        return Result(0)

    controller = LaunchctlController(uid=501, runner=runner, sleep=lambda s: None)
    controller.stop()
    controller.start(Path("/example/agent.plist"))
    assert controller.loaded_environment() == {"A": "b"}
    assert calls == [
        ["print", "gui/501/com.language-ai-platform"],
        ["bootout", "gui/501/com.language-ai-platform"],
        ["print", "gui/501/com.language-ai-platform"],
        ["print", "gui/501/com.language-ai-platform"],
        ["bootstrap", "gui/501", "/example/agent.plist"],
        ["enable", "gui/501/com.language-ai-platform"],
        ["kickstart", "-k", "gui/501/com.language-ai-platform"],
        ["print", "gui/501/com.language-ai-platform"],
    ]


def test_launchctl_controller_fails_when_agent_stays_loaded():
    class Result:
        returncode = 0
        stdout = ""

    controller = LaunchctlController(uid=501, runner=lambda command, **kwargs: Result(),
                                     sleep=lambda s: None, timeout_seconds=1)
    with pytest.raises(LaunchAgentError, match="still loaded"):
        controller.stop()


def test_launchctl_missing_is_reported_cleanly():
    def runner(command, **kwargs):
        raise FileNotFoundError("launchctl")

    with pytest.raises(LaunchAgentError, match="requires macOS"):
        LaunchctlController(uid=501, runner=runner).is_loaded()


def test_migrated_plist_changes_only_dictionary_variables(tmp_path):
    original = write_launch_agent(tmp_path / "agent.plist")
    updated = plistlib.loads(migrated_plist(original, {"DICTIONARY_DB_PATH": "/a", "DICTIONARY_FORMS_INDEX_PATH": "/b"}))
    before = plistlib.loads(original)
    assert {key: value for key, value in updated.items() if key != "EnvironmentVariables"} == {
        key: value for key, value in before.items() if key != "EnvironmentVariables"
    }
    assert updated["EnvironmentVariables"] == {
        **before["EnvironmentVariables"], "DICTIONARY_DB_PATH": "/a", "DICTIONARY_FORMS_INDEX_PATH": "/b",
    }


@pytest.mark.parametrize("unsafe_key", ["CUSTOM_FLAG", "AUTHORIZATION"])
def test_migrated_plist_refuses_unapproved_environment_fields(tmp_path, unsafe_key):
    original = write_launch_agent(
        tmp_path / "agent.plist", environment={unsafe_key: "not-a-real-secret"}
    )
    with pytest.raises(LaunchAgentError, match="unsupported environment"):
        migrated_plist(original, {"DICTIONARY_DB_PATH": "/a"})



# ---------------------------------------------------- independent-review fixes

def test_second_attempt_after_failed_first_cutover_recovers_cleanly(prepared):
    env, legacy, candidate = prepared
    controller, backend = lifecycle(env)
    for _ in range(2):
        controller.fail_start_times = 1
        with pytest.raises(CutoverError, match="rolled back") as raised:
            _cutover(env, candidate, controller, backend)
        assert raised.value.recovered is True
        assert not (env.storage / "dictionary_current").is_symlink()
        assert env.plist.read_bytes() == env.original_plist
    _cutover(env, candidate, controller, backend)
    assert current_build_id(env.layout) == candidate


def test_keyboard_interrupt_in_stopped_window_recovers(prepared):
    env, _, candidate = prepared
    controller, backend = lifecycle(env)

    def fault(stage):
        if stage == "before_smoke":
            raise KeyboardInterrupt

    with pytest.raises(CutoverError, match="rolled back") as raised:
        _cutover(env, candidate, controller, backend, _fault=fault)
    assert raised.value.recovered is True
    assert controller.loaded and backend.healthy()
    assert backend.get_json("/api/dictionary/status")["release_id"] == "unversioned"
    assert not (env.storage / "dictionary_current").is_symlink()


def test_real_sigint_during_stopped_window_is_deferred_until_complete(prepared):
    import signal

    env, _, candidate = prepared
    controller, backend = lifecycle(env)

    def fault(stage):
        if stage == "after_switch":
            os.kill(os.getpid(), signal.SIGINT)

    result = _cutover(env, candidate, controller, backend, _fault=fault)
    assert result["action"] == "cutover_complete"
    assert result["deferred_signals"] == [signal.SIGINT]
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler


def test_switch_failing_after_replace_still_restores_link(prepared, monkeypatch):
    from services.dictionary import release_store

    env, _, candidate = prepared
    controller, backend = lifecycle(env)
    real = release_store.fsync_directory
    calls = {"count": 0}

    def flaky(path):
        if Path(path) == env.layout.storage:
            calls["count"] += 1
            if calls["count"] == 2:  # the fsync right after os.replace
                raise OSError("EIO")
        return real(path)

    monkeypatch.setattr(release_store, "fsync_directory", flaky)
    with pytest.raises(CutoverError, match="rolled back") as raised:
        _cutover(env, candidate, controller, backend)
    assert raised.value.recovered is True
    assert not (env.storage / "dictionary_current").is_symlink()
    assert backend.get_json("/api/dictionary/status")["release_id"] == "unversioned"


def test_rollback_refuses_failed_or_never_served_releases(prepared):
    env, legacy, first = prepared
    controller, backend = lifecycle(env)
    _cutover(env, first, controller, backend)
    never_served = env.prepare(first, now=NOW.replace(hour=13))["build_id"]
    with pytest.raises(CutoverError, match="never been served"):
        _rollback(env, never_served, controller, backend)
    failed = release_dir(env, never_served)
    make_writable(failed)
    (failed / "build-failure.json").write_text("{}")
    with pytest.raises(CutoverError, match="build failure"):
        _rollback(env, never_served, controller, backend)
    assert current_build_id(env.layout) == first


def test_startup_created_learning_stores_are_not_drift(prepared):
    env, _, candidate = prepared
    (env.storage / "speaking_sessions.json").unlink()
    controller, backend = lifecycle(env)
    original_boot = backend.boot

    def boot_creates_default(environment):
        original_boot(environment)
        path = env.storage / "speaking_sessions.json"
        if not path.exists():
            path.write_text('{"version":1,"sessions":[]}', encoding="utf-8")
    backend.boot = boot_creates_default
    assert _cutover(env, candidate, controller, backend)["action"] == "cutover_complete"


def test_startup_created_nonempty_learning_store_is_drift(prepared):
    env, _, candidate = prepared
    (env.storage / "speaking_sessions.json").unlink()
    controller, backend = lifecycle(env)
    original_boot = backend.boot

    def boot_creates_content(environment):
        original_boot(environment)
        path = env.storage / "speaking_sessions.json"
        if not path.exists():
            path.write_text('{"version":1,"sessions":[{"id":"unexpected"}]}')

    backend.boot = boot_creates_content
    with pytest.raises(CutoverError, match="learning-store hashes differ"):
        _cutover(env, candidate, controller, backend)


def test_learning_snapshot_rejects_symlinked_media(tmp_path):
    storage = tmp_path / "storage"
    storage.mkdir()
    outside = tmp_path / "outside-media"
    outside.mkdir()
    (storage / "library_media").symlink_to(outside, target_is_directory=True)
    with pytest.raises(RuntimeError, match="symlink"):
        learning_snapshot(storage)


def test_concurrent_migration_is_refused_by_lock(prepared):
    from services.dictionary.cutover import _migration_lock

    env, _, candidate = prepared
    controller, backend = lifecycle(env)
    with _migration_lock(env.storage):
        with pytest.raises(CutoverError, match="already running"):
            _cutover(env, candidate, controller, backend)
    assert controller.calls == []


@pytest.mark.parametrize("failure", ["conversion", "stop_raises", "restart_unhealthy"])
def test_legacy_conversion_failures_always_restart_and_report(env, monkeypatch, failure):
    from services.dictionary import cutover as cutover_module

    controller, backend = lifecycle(env)
    if failure == "conversion":
        def broken(*args, **kwargs):
            if kwargs.get("dry_run"):
                return real(*args, **kwargs)
            raise RuntimeError("disk vanished")
        real = cutover_module.create_legacy_rollback_release
        monkeypatch.setattr(cutover_module, "create_legacy_rollback_release", broken)
    elif failure == "stop_raises":
        def stop():
            controller.calls.append("stop")
            raise LaunchAgentError("bootout failed")
        controller.stop = stop
    else:
        controller.fail_start_times = 1
    with pytest.raises(CutoverError) as raised:
        run_legacy_conversion(layout=env.layout, legacy_database=env.legacy, controller=controller,
                              client=backend, plist_path=env.plist, now=NOW,
                              health_attempts=2, sleep=lambda s: None)
    record, _ = _result(env)
    if failure == "restart_unhealthy":
        assert raised.value.recovered is False and record["result"] == "restart_failed"
    else:
        assert raised.value.recovered is True and record["result"] == "failed"
        assert controller.loaded and backend.healthy()
    assert env.plist.read_bytes() == env.original_plist
