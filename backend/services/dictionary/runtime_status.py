"""Path-free description of which dictionary release this process serves."""

from pathlib import Path

from services.dictionary.forms_sidecar import lookup_forms
from services.dictionary.release_store import RELEASES_DIRNAME

UNVERSIONED_RELEASE_ID = "unversioned"


def runtime_status(database_path: Path | None, forms_index_path: Path | None) -> dict:
    """Return configuration, release ID, and Sidecar status without exposing paths."""
    if database_path is None or not database_path.is_file():
        return {
            "configured": False,
            "release_id": None,
            "forms_index_status": "index_unavailable",
        }
    resolved = database_path.resolve()
    if resolved.parent.parent.name == RELEASES_DIRNAME:
        release_id = resolved.parent.name
    else:
        release_id = UNVERSIONED_RELEASE_ID
    forms_status = (
        lookup_forms(database_path, forms_index_path, 0).status
        if forms_index_path is not None
        else "index_unavailable"
    )
    return {
        "configured": True,
        "release_id": release_id,
        "forms_index_status": forms_status,
    }
