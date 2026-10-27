import sys; sys.path.insert(0, "tests")
from pathlib import Path
import tempfile, dummy_data, os
from types import SimpleNamespace
from test_connect import FakeDbx, FakeModels, _cfg
from structlab.connect import run_connect_check
t=Path(tempfile.mkdtemp()); src=dummy_data.make_all(t)
run_connect_check(_cfg(t, src), client=FakeDbx(src), ai_client=SimpleNamespace(models=FakeModels()))
