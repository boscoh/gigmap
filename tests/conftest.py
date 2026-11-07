from pathlib import Path

import pytest

from gigmap import stores


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every test at a temporary data dir, never at ./data."""
    monkeypatch.setenv(stores.DATA_DIR_ENV, str(tmp_path / "data"))
