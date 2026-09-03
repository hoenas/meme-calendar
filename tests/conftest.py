import os
import tempfile
from pathlib import Path

# Muss vor dem ersten memecal-Import stehen, damit die App nicht die echte
# Datenbank im Projektverzeichnis anfasst.
_TMP = tempfile.mkdtemp(prefix="memecal-tests-")
os.environ.setdefault("MEMECAL_DATA_DIR", _TMP)
os.environ.setdefault("MEMECAL_SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("MEMECAL_ADMIN_PASSWORD", "")
# Ohne das würde der Startup die kuratierte Default-Liste anlegen wollen und
# dabei echte YouTube-Requests absetzen.
os.environ.setdefault("MEMECAL_DEFAULT_CHANNELS", "")

import pytest


@pytest.fixture
def data_dir() -> Path:
    return Path(_TMP)
