"""离线更新包拒绝损坏、穿越路径、依赖错配等会破坏部署的输入。"""
import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy/offline"))
from release import create, digest, extract, verify, version_name


@pytest.fixture
def package(tmp_path):
    source = tmp_path / "source"
    (source / "swr_builder/webui").mkdir(parents=True)
    (source / "swr_builder/__main__.py").write_text("print('application')\n")
    (source / "swr_builder/webui/index.html").write_text("<html>test</html>")
    contract = "a" * 64
    archive = create(source, tmp_path / "out", "test-r1", contract)
    directory = tmp_path / "unpacked"
    directory.mkdir()
    extract(archive, directory)
    return archive, directory, contract


def test_release_roundtrip_and_checksum(package):
    archive, directory, contract = package
    assert verify(directory, contract)["version"] == "test-r1"
    assert archive.with_suffix(".gz.sha256").read_text().startswith(digest(archive))


@pytest.mark.parametrize("mutation", ["modify", "delete", "extra", "nested_manifest"])
def test_release_detects_payload_changes(package, mutation):
    _, directory, contract = package
    path = directory / "swr_builder/__main__.py"
    if mutation == "modify":
        path.write_text("different")
    elif mutation == "delete":
        path.unlink()
    elif mutation == "extra":
        (directory / "extra.py").write_text("extra")
    else:
        (directory / "swr_builder/release.json").write_text("{}")
    with pytest.raises(ValueError, match="SHA256"):
        verify(directory, contract)


def test_runtime_mismatch_is_rejected(package):
    _, directory, _ = package
    with pytest.raises(ValueError, match="运行依赖"):
        verify(directory, "b" * 64)


@pytest.mark.parametrize("name,kind", [("../escape", "file"), ("/escape", "file"),
                                      ("link", "symlink"), ("hard", "hardlink"),
                                      ("pipe", "fifo"), ("duplicate", "duplicate")])
def test_unsafe_archives_are_rejected(tmp_path, name, kind):
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo(name)
        if kind in ("symlink", "hardlink"):
            info.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
            info.linkname = "/etc/passwd"
        elif kind == "fifo":
            info.type = tarfile.FIFOTYPE
        tar.addfile(info)
        if kind == "duplicate":
            tar.addfile(info)
    with pytest.raises(ValueError):
        extract(archive, tmp_path / "output")


@pytest.mark.parametrize("value", ["..", "../x", "/x", "a/b", "", "a" * 81])
def test_versions_cannot_escape_release_directory(value):
    with pytest.raises(ValueError):
        version_name(value)
