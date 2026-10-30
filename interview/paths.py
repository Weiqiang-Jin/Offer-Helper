"""Keep bundled resources separate from writable per-user application data."""
import os
import sys
from pathlib import Path

RESOURCE_DIR = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
DATA_DIR = Path(os.environ.get('OFFER_HELPER_DATA_DIR') or (
    Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'OfferHelper'
    if getattr(sys, 'frozen', False) else RESOURCE_DIR))
DATA_DIR.mkdir(parents=True, exist_ok=True)


def overlay_command(port):
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--overlay', str(port)]
    return [sys.executable, '-m', 'interview.overlay', str(port)]
