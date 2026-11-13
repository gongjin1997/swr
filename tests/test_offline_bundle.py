"""离线更新包拒绝损坏、穿越路径、依赖错配等会破坏部署的输入。"""
import json
import sys
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "deploy/offline"))
from release import create, create_patch, digest, extract, verify, verify_patch, version_name


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


# ---------------------------------------------------------------- 补丁包
def _source(tmp_path):
    source = tmp_path / "source"
    (source / "swr_builder/webui").mkdir(parents=True)
    (source / "swr_builder/store").mkdir()
    (source / "swr_builder/__main__.py").write_text("print('application')\n")
    (source / "swr_builder/webui/index.html").write_text("<html>patch</html>")
    (source / "swr_builder/store/sqlscripts.py").write_text((REPO / "swr_builder/store/sqlscripts.py").read_text())
    return source


def _sql(tmp_path, files):
    folder = tmp_path / "sql"
    for relative, text in files.items():
        (folder / relative).parent.mkdir(parents=True, exist_ok=True)
        (folder / relative).write_text(text)
    return folder


def _unpack(archive, tmp_path):
    directory = tmp_path / "unpacked-patch"
    directory.mkdir()
    extract(archive, directory)
    return directory


def test_patch_with_code_and_sql_roundtrip(tmp_path):
    sql = _sql(tmp_path, {"dml/001_fix.sql": "UPDATE t SET c = 1;\n", "ddl/001_index.sql": "CREATE INDEX i ON t (c);\n",
                          "zz_after.sql": "SELECT 1;\n", "README.md": "说明不打包"})
    readme = tmp_path / "README.md"
    readme.write_text("补丁说明")
    archive = create_patch(_source(tmp_path), tmp_path / "out", "2.0.1", "a" * 64, sql=sql, base="2.0.0",
                           description="修复", readme=readme)
    assert archive.name == "swr-patch-2.0.1.tar.gz"
    assert archive.with_suffix(".gz.sha256").read_text().startswith(digest(archive))
    directory = _unpack(archive, tmp_path)
    manifest = verify_patch(directory)
    assert manifest["kind"] == "patch" and manifest["base"] == "2.0.0" and manifest["code"] is True
    assert manifest["sql"] == ["ddl/001_index.sql", "dml/001_fix.sql", "zz_after.sql"]
    assert (directory / "README.md").read_text() == "补丁说明" and not (directory / "sql/README.md").exists()
    assert verify(directory / "code", "a" * 64)["version"] == "2.0.1"
    with pytest.raises(ValueError, match="输出已存在"):
        create_patch(_source(tmp_path / "again"), tmp_path / "out", "2.0.1", "a" * 64, sql=sql)


def test_sql_only_patch(tmp_path):
    archive = create_patch(_source(tmp_path), tmp_path / "out", "2.0.0-sql1", "a" * 64, code=False,
                           sql=_sql(tmp_path, {"dml/001_fix.sql": "UPDATE t SET c = 1;\n"}))
    directory = _unpack(archive, tmp_path)
    manifest = verify_patch(directory)
    assert manifest["code"] is False and not (directory / "code").exists()
    assert sorted(manifest["files"]) == ["sql/dml/001_fix.sql"]


@pytest.mark.parametrize("mutation,message", [
    ("modify_sql", "SHA256"), ("extra_file", "不认识的文件"), ("drop_sql_list", "SQL 清单"),
    ("code_version", "代码版本"), ("no_manifest", "patch.json"),
])
def test_patch_tampering_is_detected(tmp_path, mutation, message):
    archive = create_patch(_source(tmp_path), tmp_path / "out", "2.0.1", "a" * 64,
                           sql=_sql(tmp_path, {"dml/001_fix.sql": "UPDATE t SET c = 1;\n"}))
    directory = _unpack(archive, tmp_path)
    path = directory / "patch.json"
    manifest = json.loads(path.read_text())
    if mutation == "modify_sql":
        (directory / "sql/dml/001_fix.sql").write_text("DROP TABLE t;\n")
    elif mutation == "extra_file":
        (directory / "install.sh").write_text("rm -rf /")
        manifest["files"]["install.sh"] = digest(directory / "install.sh")
    elif mutation == "drop_sql_list":
        manifest["sql"] = []
    elif mutation == "code_version":
        manifest["version"] = "2.0.2"
    else:
        path.unlink()
    if path.exists():
        path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match=message):
        verify_patch(directory)


@pytest.mark.parametrize("files,message", [
    ({"dml/001_tx.sql": "BEGIN;\nUPDATE t SET c = 1;\nCOMMIT;\n"}, "BEGIN / COMMIT"),
    ({"dml/001_meta.sql": "\\i other.sql\n"}, "元命令"),
    ({"other/001.sql": "SELECT 1;\n"}, "子目录"),
    ({"README.md": "只有说明"}, "没有 .sql 脚本"),
])
def test_invalid_sql_is_rejected_when_building_the_patch(tmp_path, files, message):
    with pytest.raises(ValueError, match=message):
        create_patch(_source(tmp_path), tmp_path / "out", "2.0.1", "a" * 64, sql=_sql(tmp_path, files))
    assert not (tmp_path / "out/swr-patch-2.0.1.tar.gz").exists()


def test_patch_needs_code_or_sql(tmp_path):
    with pytest.raises(ValueError, match="至少要包含"):
        create_patch(_source(tmp_path), tmp_path / "out", "2.0.1", "a" * 64, code=False)
