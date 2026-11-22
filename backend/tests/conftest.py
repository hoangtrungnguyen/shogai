import os
import tempfile

# Keep the module-level app (shogai.main.app) away from the real data folder.
os.environ["SHOGAI_DATA_DIR"] = tempfile.mkdtemp(prefix="shogai-test-")

import pytest  # noqa: E402

from shogai.config import load_settings  # noqa: E402
from shogai.store import Store  # noqa: E402


@pytest.fixture
def settings(tmp_path):
    return load_settings().with_overrides(
        data_dir=tmp_path / "data",
        gemini_api_key="test-key",
        frontend_dist=tmp_path / "no-dist",
    )


@pytest.fixture
def store(settings):
    return Store(settings.data_dir)
