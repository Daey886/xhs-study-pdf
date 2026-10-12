"""home_edition.desktop.tray 单元测试。"""

import json
import os

from home_edition.desktop.tray import (
    CHOICE_EXIT,
    CHOICE_MINIMIZE,
    REMEMBER_FILE,
    TrayController,
)


def test_load_remembered_empty(tmp_path, monkeypatch):
    monkeypatch.setattr("home_edition.desktop.tray.REMEMBER_FILE", str(tmp_path / "none.json"))
    assert TrayController.load_remembered() is None


def test_save_and_load_remembered(tmp_path, monkeypatch):
    p = str(tmp_path / "choice.json")
    monkeypatch.setattr("home_edition.desktop.tray.REMEMBER_FILE", p)
    TrayController.save_remembered(CHOICE_MINIMIZE)
    assert TrayController.load_remembered() == CHOICE_MINIMIZE

    TrayController.save_remembered(CHOICE_EXIT)
    assert TrayController.load_remembered() == CHOICE_EXIT


def test_load_remembered_invalid(tmp_path, monkeypatch):
    p = str(tmp_path / "bad.json")
    monkeypatch.setattr("home_edition.desktop.tray.REMEMBER_FILE", p)
    with open(p, "w") as f:
        json.dump({"choice": "nonsense"}, f)
    assert TrayController.load_remembered() is None


def test_clear_remembered(tmp_path, monkeypatch):
    p = str(tmp_path / "c.json")
    monkeypatch.setattr("home_edition.desktop.tray.REMEMBER_FILE", p)
    TrayController.save_remembered(CHOICE_EXIT)
    assert os.path.exists(p)
    TrayController.clear_remembered()
    assert not os.path.exists(p)


def test_apply_minimize_calls_callback():
    calls = []
    ctrl = TrayController(on_minimize=lambda: calls.append("min"), on_exit=lambda: calls.append("exit"))
    ctrl._apply(CHOICE_MINIMIZE)
    assert calls == ["min"]


def test_apply_exit_calls_callback():
    calls = []
    ctrl = TrayController(on_minimize=lambda: calls.append("min"), on_exit=lambda: calls.append("exit"))
    ctrl._apply(CHOICE_EXIT)
    assert calls == ["exit"]


def test_remember_file_is_in_home():
    assert os.path.basename(REMEMBER_FILE) == ".xhs_study_pdf_close_choice.json"
