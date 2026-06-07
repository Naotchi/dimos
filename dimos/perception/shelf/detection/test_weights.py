import pytest

from dimos.perception.shelf.detection import weights


def test_resolve_weights_returns_explicit_existing_path(tmp_path):
    p = tmp_path / "model.pth"
    p.write_bytes(b"x")
    assert weights.resolve_weights("l", weights=p) == p


def test_resolve_weights_missing_explicit_path_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        weights.resolve_weights("l", weights=tmp_path / "nope.pth")


def test_resolve_weights_unknown_size_raises():
    with pytest.raises(ValueError):
        weights.resolve_weights("zzz")


def test_resolve_weights_not_downloaded_no_download_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("DIMOS_RTDETRV4_DIR", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        weights.resolve_weights("l", download=False)


def test_resolve_config_unknown_size_raises():
    with pytest.raises(ValueError):
        weights.resolve_config("zzz")
