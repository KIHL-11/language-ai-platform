import plistlib
import re
import subprocess
import sys
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[2]
SCRIPTS = REPOSITORY / "scripts"
SHELL_SCRIPTS = [
    SCRIPTS / "build_personal_app.sh",
    SCRIPTS / "install_personal_app_macos.sh",
    SCRIPTS / "update_personal_app_macos.sh",
    SCRIPTS / "status_personal_app_macos.sh",
    SCRIPTS / "uninstall_personal_app_macos.sh",
    SCRIPTS / "cutover_dictionary_macos.sh",
]


def test_personal_app_shell_scripts_have_valid_bash_syntax():
    for script in SHELL_SCRIPTS:
        result = subprocess.run(
            ["bash", "-n", str(script)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"{script.name}: {result.stderr}"


def test_scripts_do_not_hard_code_owner_home_or_secret_material():
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [
            *SHELL_SCRIPTS,
            SCRIPTS / "generate_launch_agent.py",
            REPOSITORY / "backend" / "scripts" / "dictionary_cutover.py",
            REPOSITORY / "backend" / "scripts" / "prepare_dictionary_release.py",
        ]
    )
    assert "/Users/lin" not in sources
    assert "OPENAI_API_KEY" not in sources
    assert "AI_API_KEY" not in sources
    assert "AZURE_SPEECH_KEY" not in sources


def test_generated_launch_agent_is_local_only_and_contains_no_secrets(tmp_path):
    plist_path = tmp_path / "com.language-ai-platform.plist"
    subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_launch_agent.py"),
            str(plist_path),
            "/example/backend/.venv/bin/python",
            "/example/backend",
            "/example/logs",
        ],
        check=True,
    )

    with plist_path.open("rb") as plist_file:
        launch_agent = plistlib.load(plist_file)
    arguments = launch_agent["ProgramArguments"]
    assert arguments == [
        "/example/backend/.venv/bin/python",
        "-m",
        "uvicorn",
        "app:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
    ]
    serialized = plist_path.read_bytes()
    assert b"0.0.0.0" not in serialized
    assert b"API_KEY" not in serialized
    assert b".env" not in serialized
    assert launch_agent["WorkingDirectory"] == "/example/backend"
    assert launch_agent["RunAtLoad"] is True
    assert launch_agent["KeepAlive"] == {"SuccessfulExit": False}
    assert launch_agent["EnvironmentVariables"]["PATH"].startswith(
        "/opt/homebrew/bin:/usr/local/bin:"
    )
    assert launch_agent["StandardOutPath"] == "/example/logs/backend.stdout.log"
    assert launch_agent["StandardErrorPath"] == "/example/logs/backend.stderr.log"


def test_uninstall_only_targets_generated_service_and_application():
    source = (SCRIPTS / "uninstall_personal_app_macos.sh").read_text(
        encoding="utf-8",
    )
    assert "backend/storage" not in source
    assert "chunk_bank" not in source
    assert "backend/.env" not in source
    assert "backend/media" not in source
    assert "backend/.venv" not in source


def _generate(tmp_path, *extra):
    plist_path = tmp_path / "com.language-ai-platform.plist"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_launch_agent.py"),
            str(plist_path),
            "/example/backend/.venv/bin/python",
            "/example/backend",
            "/example/logs",
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return result, plist_path


def test_generated_launch_agent_without_release_keeps_backend_dictionary_defaults(tmp_path):
    result, plist_path = _generate(tmp_path)
    assert result.returncode == 0
    environment = plistlib.loads(plist_path.read_bytes())["EnvironmentVariables"]
    assert "DICTIONARY_DB_PATH" not in environment
    assert "DICTIONARY_FORMS_INDEX_PATH" not in environment


def test_generated_launch_agent_preserves_versioned_dictionary_paths(tmp_path):
    storage = tmp_path / "storage"
    (storage / "dictionary_releases" / "release-a").mkdir(parents=True)
    current = storage / "dictionary_current"
    current.symlink_to("dictionary_releases/release-a", target_is_directory=True)
    result, plist_path = _generate(tmp_path, "--dictionary-current", str(current))
    assert result.returncode == 0, result.stderr
    environment = plistlib.loads(plist_path.read_bytes())["EnvironmentVariables"]
    resolved_storage = storage.resolve()
    # Paths go through the stable symlink, never the release it currently names.
    assert environment["DICTIONARY_DB_PATH"] == str(resolved_storage / "dictionary_current" / "dictionary.sqlite3")
    assert environment["DICTIONARY_FORMS_INDEX_PATH"] == str(
        resolved_storage / "dictionary_current" / "dictionary.forms.sqlite3"
    )
    assert "release-a" not in plist_path.read_text()


def test_generator_refuses_a_non_symlink_dictionary_current(tmp_path):
    real = tmp_path / "dictionary_current"
    real.mkdir()
    result, plist_path = _generate(tmp_path, "--dictionary-current", str(real))
    assert result.returncode != 0
    assert not plist_path.exists()


def _installer_dictionary_arguments(tmp_path, *, link: bool, plist_text: str | None):
    """Execute the installer's real dictionary-argument block in isolation."""
    source = (SCRIPTS / "install_personal_app_macos.sh").read_text(encoding="utf-8")
    start = source.index("DICTIONARY_ARGUMENTS=()")
    end = source.index("fi\n", start) + 3
    backend = tmp_path / "backend"
    (backend / "storage" / "dictionary_releases" / "a").mkdir(parents=True)
    if link:
        (backend / "storage" / "dictionary_current").symlink_to("dictionary_releases/a")
    plist = tmp_path / "agent.plist"
    if plist_text is not None:
        plist.write_text(plist_text, encoding="utf-8")
    script = (
        "set -euo pipefail\n"
        f'BACKEND="{backend}"\nPLIST_PATH="{plist}"\n'
        + source[start:end]
        + 'echo "${#DICTIONARY_ARGUMENTS[@]} ${DICTIONARY_ARGUMENTS[*]+${DICTIONARY_ARGUMENTS[*]}}"\n'
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    return result.stdout


def test_installer_keeps_versioned_paths_only_when_agent_already_uses_them(tmp_path):
    versioned = "<string>/x/backend/storage/dictionary_current/dictionary.sqlite3</string>"
    assert _installer_dictionary_arguments(
        tmp_path / "a", link=True, plist_text=versioned).startswith("2 --dictionary-current ")
    # A stray link (e.g. from an interrupted run) must not redirect the service.
    assert _installer_dictionary_arguments(tmp_path / "b", link=True, plist_text="<plist/>").strip() == "0"
    assert _installer_dictionary_arguments(tmp_path / "c", link=True, plist_text=None).strip() == "0"
    assert _installer_dictionary_arguments(tmp_path / "d", link=False, plist_text=versioned).strip() == "0"


def test_cutover_wrapper_refuses_non_macos_and_never_deletes():
    wrapper = SCRIPTS / "cutover_dictionary_macos.sh"
    source = wrapper.read_text(encoding="utf-8")
    assert not re.search(r"(^|[\s;&|(])(rm|rmdir|unlink)\s", source)
    assert "curl" not in source and "wget" not in source
    if sys.platform != "darwin":
        result = subprocess.run(["bash", str(wrapper), "status"], capture_output=True, text=True, check=False)
        assert result.returncode == 1
        assert "only supported on macOS" in result.stderr


def test_dictionary_migration_clis_parse_without_side_effects():
    for name in ("dictionary_cutover.py", "prepare_dictionary_release.py"):
        result = subprocess.run(
            [sys.executable, str(REPOSITORY / "backend" / "scripts" / name), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "--dry-run" in result.stdout or "dry-run" in result.stdout


def test_dictionary_cutover_status_on_non_macos_fails_cleanly(tmp_path):
    storage = tmp_path / "storage"
    storage.mkdir()
    result = subprocess.run(
        [
            sys.executable,
            str(REPOSITORY / "backend" / "scripts" / "dictionary_cutover.py"),
            "--storage-root", str(storage),
            "--plist", str(tmp_path / "missing.plist"),
            "status",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if sys.platform != "darwin":
        assert result.returncode == 1
        assert "requires macOS" in result.stderr
    assert list(storage.iterdir()) == []
