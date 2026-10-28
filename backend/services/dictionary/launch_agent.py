"""LaunchAgent and local-backend control used by dictionary cutover.

``LaunchctlController`` is the only code that runs ``launchctl``. Tests and
cloud validation inject a mock with the same interface; real-Mac behaviour must
be verified separately.
"""

import json
import os
import plistlib
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Protocol

LABEL = "com.language-ai-platform"
DICTIONARY_ENVIRONMENT_KEYS = ("DICTIONARY_DB_PATH", "DICTIONARY_FORMS_INDEX_PATH")
_CREDENTIAL_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|COOKIE|CREDENTIAL|AUTH)", re.IGNORECASE)
_MAX_PLIST_BYTES = 256 * 1024
_ALLOWED_PLIST_KEYS = {
    "Label", "ProgramArguments", "WorkingDirectory", "RunAtLoad", "KeepAlive",
    "ThrottleInterval", "ProcessType", "EnvironmentVariables",
    "StandardOutPath", "StandardErrorPath",
}
_ALLOWED_ENVIRONMENT_KEYS = {
    "PATH", "PYTHONUNBUFFERED", *DICTIONARY_ENVIRONMENT_KEYS,
}
_EXPECTED_ARGUMENTS_TAIL = [
    "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", "8000",
]


class LaunchAgentError(RuntimeError):
    """The LaunchAgent configuration or service state is unsafe or unexpected."""


class ServiceController(Protocol):
    def is_loaded(self) -> bool: ...

    def stop(self) -> None: ...

    def start(self, plist_path: Path) -> None: ...

    def loaded_environment(self) -> dict[str, str]: ...


class BackendClient(Protocol):
    def healthy(self) -> bool: ...

    def get_json(self, path: str) -> dict: ...

    def post_json(self, path: str, payload: dict) -> dict: ...


def parse_launchctl_environment(output: str) -> dict[str, str]:
    """Parse the ``environment = { KEY => VALUE }`` block of ``launchctl print``."""
    environment: dict[str, str] = {}
    inside = False
    for line in output.splitlines():
        stripped = line.strip()
        if not inside:
            if stripped == "environment = {":
                inside = True
            continue
        if stripped == "}":
            return environment
        key, separator, value = stripped.partition(" => ")
        if separator:
            environment[key.strip()] = value.strip()
    if inside:
        raise LaunchAgentError("launchctl environment block is not terminated")
    return environment


class LaunchctlController:
    def __init__(
        self,
        *,
        label: str = LABEL,
        uid: int | None = None,
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        sleep: Callable[[float], None] = time.sleep,
        timeout_seconds: float = 30,
    ):
        self.label = label
        self.domain = f"gui/{os.getuid() if uid is None else uid}"
        self.runner = runner
        self.sleep = sleep
        self.timeout_seconds = timeout_seconds

    @property
    def target(self) -> str:
        return f"{self.domain}/{self.label}"

    def _run(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess:
        try:
            result = self.runner(["launchctl", *arguments], capture_output=True, text=True, check=False)
        except FileNotFoundError as exc:
            raise LaunchAgentError("launchctl is unavailable; this command requires macOS") from exc
        if check and result.returncode != 0:
            raise LaunchAgentError(f"launchctl {arguments[0]} failed with exit code {result.returncode}")
        return result

    def is_loaded(self) -> bool:
        return self._run("print", self.target, check=False).returncode == 0

    def stop(self) -> None:
        if self.is_loaded():
            self._run("bootout", self.target)
        waited = 0.0
        while self.is_loaded():
            if waited >= self.timeout_seconds:
                raise LaunchAgentError("LaunchAgent is still loaded after bootout")
            self.sleep(0.5)
            waited += 0.5

    def start(self, plist_path: Path) -> None:
        if not self.is_loaded():
            self._run("bootstrap", self.domain, str(plist_path))
        self._run("enable", self.target)
        self._run("kickstart", "-k", self.target)

    def loaded_environment(self) -> dict[str, str]:
        result = self._run("print", self.target)
        return parse_launchctl_environment(result.stdout)


class LocalBackendClient:
    """Loopback-only HTTP client; ignores proxy environment variables."""

    def __init__(self, base_url: str = "http://127.0.0.1:8000", timeout: float = 5):
        if not base_url.startswith(("http://127.0.0.1:", "http://localhost:")):
            raise LaunchAgentError("backend client is restricted to loopback")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _request(self, request: urllib.request.Request) -> dict:
        with self._opener.open(request, timeout=self.timeout) as response:
            body = response.read(2 * 1024 * 1024)
        value = json.loads(body)
        if not isinstance(value, dict):
            raise LaunchAgentError("backend returned a non-object JSON response")
        return value

    def healthy(self) -> bool:
        try:
            return self.get_json("/api/health").get("ok") is True
        except (OSError, urllib.error.URLError, ValueError, LaunchAgentError):
            return False

    def get_json(self, path: str) -> dict:
        return self._request(urllib.request.Request(self.base_url + path, method="GET"))

    def post_json(self, path: str, payload: dict) -> dict:
        return self._request(urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        ))


def wait_until(predicate: Callable[[], bool], *, attempts: int, sleep: Callable[[float], None]) -> bool:
    for attempt in range(attempts):
        if predicate():
            return True
        if attempt + 1 < attempts:
            sleep(1)
    return False


def read_plist(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise LaunchAgentError("LaunchAgent plist must be a regular, non-symlink file")
    if path.stat().st_size > _MAX_PLIST_BYTES:
        raise LaunchAgentError("LaunchAgent plist is unexpectedly large")
    return path.read_bytes()


def expected_dictionary_environment(current_link: Path) -> dict[str, str]:
    from services.dictionary.release_store import DICTIONARY_NAME, SIDECAR_NAME

    return {
        "DICTIONARY_DB_PATH": str(current_link / DICTIONARY_NAME),
        "DICTIONARY_FORMS_INDEX_PATH": str(current_link / SIDECAR_NAME),
    }


def credential_like_variables(environment: dict) -> list[str]:
    return sorted(name for name in environment if _CREDENTIAL_NAME.search(str(name)))


def migrated_plist(original: bytes, dictionary_environment: dict[str, str]) -> bytes:
    """Return a plist identical to ``original`` except for the dictionary variables.

    Refuses plists that are not the local-only Language AI agent or that carry
    credential-like environment variables (secrets belong in ``backend/.env``).
    """
    try:
        value = plistlib.loads(original)
    except (plistlib.InvalidFileException, ValueError) as exc:
        raise LaunchAgentError("LaunchAgent plist cannot be parsed") from exc
    if not isinstance(value, dict) or value.get("Label") != LABEL:
        raise LaunchAgentError("LaunchAgent plist is not the Language AI agent")
    if set(value) - _ALLOWED_PLIST_KEYS:
        raise LaunchAgentError("LaunchAgent plist contains unsupported fields")
    arguments = value.get("ProgramArguments")
    if (
        not isinstance(arguments, list)
        or len(arguments) != 8
        or not all(isinstance(argument, str) for argument in arguments)
        or arguments[1:] != _EXPECTED_ARGUMENTS_TAIL
        or not Path(arguments[0]).is_absolute()
    ):
        raise LaunchAgentError("LaunchAgent ProgramArguments are outside the safe contract")
    environment = value.get("EnvironmentVariables", {})
    if not isinstance(environment, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in environment.items()
    ):
        raise LaunchAgentError("LaunchAgent EnvironmentVariables are malformed")
    unsupported = sorted(set(environment) - _ALLOWED_ENVIRONMENT_KEYS)
    credentials = credential_like_variables(environment)
    if unsupported or credentials:
        raise LaunchAgentError(
            "LaunchAgent defines unsupported environment variable(s); reinstall the generated "
            "local-only agent before migrating"
        )
    updated = dict(value)
    updated["EnvironmentVariables"] = {**environment, **dictionary_environment}
    serialized = plistlib.dumps(updated, sort_keys=False)
    if plistlib.loads(serialized) != updated:
        raise LaunchAgentError("generated LaunchAgent plist does not round-trip")
    return serialized


def plist_dictionary_environment(serialized: bytes) -> dict[str, str]:
    environment = plistlib.loads(serialized).get("EnvironmentVariables", {})
    return {key: environment[key] for key in DICTIONARY_ENVIRONMENT_KEYS if key in environment}


def write_plist_atomic(path: Path, payload: bytes) -> None:
    """Replace a LaunchAgent plist via a fsynced temporary file in the same directory."""
    if path.is_symlink():
        raise LaunchAgentError("refusing to write through a symlinked LaunchAgent plist")
    temporary = path.with_name(f".{path.name}.dictionary-migration")
    if temporary.is_symlink():
        raise LaunchAgentError("LaunchAgent temporary path is an unexpected symlink")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o644)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    if path.read_bytes() != payload:
        raise LaunchAgentError("LaunchAgent plist did not persist as written")
