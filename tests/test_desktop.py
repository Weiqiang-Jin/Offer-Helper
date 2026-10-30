import importlib.util
from pathlib import Path
import sys


def load_paths():
    source = Path(__file__).resolve().parents[1] / 'interview' / 'paths.py'
    spec = importlib.util.spec_from_file_location('isolated_desktop_paths', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frozen_resources_and_user_data_are_separate(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, '_MEIPASS', str(tmp_path / 'bundle'), raising=False)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'user'))
    monkeypatch.delenv('OFFER_HELPER_DATA_DIR', raising=False)
    paths = load_paths()
    assert paths.RESOURCE_DIR == tmp_path / 'bundle'
    assert paths.DATA_DIR == tmp_path / 'user' / 'OfferHelper'
    assert paths.DATA_DIR.is_dir()
    assert not paths.RESOURCE_DIR.exists()
    assert paths.overlay_command(8765) == [sys.executable, '--overlay', '8765']


def test_source_overlay_uses_python_module_and_isolated_data(tmp_path, monkeypatch):
    monkeypatch.delattr(sys, 'frozen', raising=False)
    monkeypatch.setenv('OFFER_HELPER_DATA_DIR', str(tmp_path))
    paths = load_paths()
    assert paths.DATA_DIR == tmp_path
    assert paths.overlay_command(8766) == [sys.executable, '-m', 'interview.overlay', '8766']
