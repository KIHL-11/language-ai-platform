import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import app as app_module
import pytest


def test_transcribe_text_initializes_local_whisper_model(monkeypatch, tmp_path):
    created = {}

    class FakeWhisperModel:
        def __init__(self, model_name, **kwargs):
            created["model_name"] = model_name
            created["options"] = kwargs

        def transcribe(self, path, **kwargs):
            created["path"] = path
            created["transcribe_options"] = kwargs
            return (
                [SimpleNamespace(text=" Hello "), SimpleNamespace(text="world ")],
                None,
            )

    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=FakeWhisperModel),
    )
    original_whisper = getattr(app_module, "_whisper", None)

    try:
        result = app_module._transcribe_text(str(tmp_path / "speech.wav"))
    finally:
        if hasattr(app_module, "_whisper"):
            app_module._whisper = original_whisper

    assert result == "Hello world"
    assert created["model_name"] == app_module.WHISPER_MODEL
    assert created["options"] == {
        "device": "cpu",
        "compute_type": "int8",
        "cpu_threads": 6,
    }
    assert created["transcribe_options"] == {
        "language": "en",
        "beam_size": 1,
        "condition_on_previous_text": False,
    }


def test_failed_transcode_removes_all_temporary_files(monkeypatch, tmp_path):
    create_temporary_file = tempfile.NamedTemporaryFile

    def create_in_test_directory(*args, **kwargs):
        kwargs["dir"] = tmp_path
        return create_temporary_file(*args, **kwargs)

    def fail_transcode(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0], stderr=b"invalid audio")

    monkeypatch.setattr(
        app_module.tempfile, "NamedTemporaryFile", create_in_test_directory
    )
    monkeypatch.setattr(app_module.subprocess, "run", fail_transcode)

    with pytest.raises(subprocess.CalledProcessError):
        app_module._to_wav16k(b"not audio", ".webm")

    assert list(tmp_path.iterdir()) == []


def test_successful_transcode_keeps_only_output_file(monkeypatch, tmp_path):
    create_temporary_file = tempfile.NamedTemporaryFile

    def create_in_test_directory(*args, **kwargs):
        kwargs["dir"] = tmp_path
        return create_temporary_file(*args, **kwargs)

    def write_transcoded_audio(command, **kwargs):
        Path(command[-1]).write_bytes(b"wav audio")

    monkeypatch.setattr(
        app_module.tempfile, "NamedTemporaryFile", create_in_test_directory
    )
    monkeypatch.setattr(app_module.subprocess, "run", write_transcoded_audio)

    output_path = Path(app_module._to_wav16k(b"source audio", ".webm"))

    assert output_path.read_bytes() == b"wav audio"
    assert list(tmp_path.iterdir()) == [output_path]


def test_output_tempfile_creation_failure_removes_input_file(monkeypatch, tmp_path):
    create_temporary_file = tempfile.NamedTemporaryFile
    created = 0

    def fail_on_output_file(*args, **kwargs):
        nonlocal created
        created += 1
        if created == 2:
            raise OSError("cannot create output file")
        kwargs["dir"] = tmp_path
        return create_temporary_file(*args, **kwargs)

    monkeypatch.setattr(app_module.tempfile, "NamedTemporaryFile", fail_on_output_file)

    with pytest.raises(OSError, match="cannot create output file"):
        app_module._to_wav16k(b"audio", ".webm")

    assert list(tmp_path.iterdir()) == []
