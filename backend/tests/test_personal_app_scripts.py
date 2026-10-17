import plistlib
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
        for path in [*SHELL_SCRIPTS, SCRIPTS / "generate_launch_agent.py"]
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
