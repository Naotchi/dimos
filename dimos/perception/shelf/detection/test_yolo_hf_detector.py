import pytest

from dimos.perception.shelf.detection import yolo_hf_detector as yd


def test_resolve_local_path_returns_it(tmp_path):
    p = tmp_path / "best.pt"
    p.write_bytes(b"x")
    assert yd.resolve_yolo_weights(p) == str(p)


def test_resolve_unknown_no_repo_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        yd.resolve_yolo_weights(str(tmp_path / "missing.pt"))
