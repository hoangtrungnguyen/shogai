import os
import tempfile

# Keep the module-level app (shogai.main.app) away from the real data folder and the public port.
os.environ["SHOGAI_DATA_DIR"] = tempfile.mkdtemp(prefix="shogai-test-")
os.environ["SHOGAI_PUBLIC_DIR"] = ""
os.environ["SHOGAI_PUBLIC_PORT"] = "0"

import pytest  # noqa: E402

from shogai.config import load_settings  # noqa: E402
from shogai.store import Store  # noqa: E402


@pytest.fixture
def settings(tmp_path):
    return load_settings().with_overrides(
        data_dir=tmp_path / "data",
        gemini_api_key="test-key",
        frontend_dist=tmp_path / "no-dist",
        public_dir=None,  # -> data_dir/public
        public_port=0,
        relay_poll=0,  # tests sync by hand
    )


@pytest.fixture
def store(settings):
    return Store(settings.data_dir)
