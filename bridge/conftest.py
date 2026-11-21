import os
import sys
from pathlib import Path

# Unit tests always run against the default (demo) contract, whatever the CI/repo variables say.
os.environ["BRIDGE_ACCOUNT_MODE"] = "demo"
os.environ.pop("BRIDGE_EXPECTED_SERVER", None)
sys.path.insert(0, str(Path(__file__).parent))
