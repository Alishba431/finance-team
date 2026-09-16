"""Central place for the data directories this app reads/writes at runtime.

Nothing under here ships with sample data - these directories are created
empty on startup and populated only by what a user uploads / what the tool
saves while running.
"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
OUTPUTS_DIR = DATA_DIR / "outputs"
ATTACHMENTS_DIR = DATA_DIR / "attachments"


def ensure_dirs():
    for d in (DATA_DIR, UPLOADS_DIR, OUTPUTS_DIR, ATTACHMENTS_DIR):
        d.mkdir(parents=True, exist_ok=True)
