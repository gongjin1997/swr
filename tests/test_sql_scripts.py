"""补丁包与铺底数据中的 SQL 脚本：顺序、事务、只执行一次、内容校验与命令行。"""
import json

import pytest

from swr_builder.__main__ import main
from swr_builder.store import sqlscripts
from tests.dbutil import new_store


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def patch_dir(tmp_path):
    root = tmp_path / "sql"
    write(root / "ddl/001_create.sql", "CREATE TABLE site_notes (id BIGSERIAL PRIMARY KEY, body TEXT NOT NULL);\n"
                                         "CREATE INDEX idx_site_notes_body ON site_notes (body);\n")
    write(root / "ddl/002_function.sql", "CREATE OR REPLACE FUNCTION note_count() RETURNS BIGINT AS $$\n"
                                         "BEGIN\n  RETURN (SELECT COUNT(*) FROM site_notes);\nEND;\n$$ LANGUAGE plpgsql;\n")
    write(root / "dml/001_seed.sql", "-- 两条铺底数据\nINSERT INTO site_notes(body) VALUES ('a; b'), ('100%');\n")
    write(root / "README.md", "说明文件不会执行")
    return root


def test_discover_orders_ddl_then_dml_then_root(tmp_path, patch_dir):
    write(patch_dir / "zz_last.sql", "SELECT 1;")
    assert [s.path for s in sqlscripts.discover(patch_dir)] == [
        "ddl/001_create.sql", "ddl/002_function.sql", "dml/001_seed.sql", "zz_last.sql"]
    assert sqlscripts.discover(tmp_path / "missing") == []


@pytest.mark.parametrize("name,text,message", [
    ("ddl/003.txt", "SELECT 1;", "不是 .sql"),
    ("dml/002_tx.sql", "BEGIN;\nUPDATE site_notes SET body = 'x';\nCOMMIT;\n", "BEGIN / COMMIT"),
    ("dml/003_meta.sql", "\\c other\nSELECT 1;\n", "元命令"),
    ("dml/004_empty.sql", "-- 只有注释\n", "没有可执行的语句"),
    ("other/001.sql", "SELECT 1;", "子目录"),
])
def test_invalid_scripts_are_rejected_before_anything_runs(patch_dir, name, text, message):
    write(patch_dir / name, text)
    with pytest.raises(ValueError, match=message):
        sqlscripts.discover(patch_dir)


def test_scripts_run_once_in_order_and_failures_roll_back(patch_dir):
    store = new_store()
    try:
        results = sqlscripts.apply_scripts(store, "2.0.1", sqlscripts.discover(patch_dir), log=lambda _m: None)
        assert [r["status"] for r in results] == ["applied"] * 3
        with store._db() as db:
            assert db.execute("SELECT note_count()").fetchone()[0] == 2
            assert {r[0] for r in db.execute("SELECT body FROM site_notes").fetchall()} == {"a; b", "100%"}
        # 再次执行同一补丁：内容相同的脚本跳过
        again = sqlscripts.apply_scripts(store, "2.0.1", sqlscripts.discover(patch_dir), log=lambda _m: None)
        assert [r["status"] for r in again] == ["skipped"] * 3
        assert [s["path"] for s in sqlscripts.applied_scripts(store, "2.0.1")] == [
            "ddl/001_create.sql", "ddl/002_function.sql", "dml/001_seed.sql"]
        # 已执行的脚本被修改：拒绝
        write(patch_dir / "dml/001_seed.sql", "INSERT INTO site_notes(body) VALUES ('changed');\n")
        with pytest.raises(RuntimeError, match="内容与本次不同"):
            sqlscripts.apply_scripts(store, "2.0.1", sqlscripts.discover(patch_dir), log=lambda _m: None)
        # 失败的脚本整体回滚，后面的脚本不执行，也不留执行记录
        bad = patch_dir.parent / "bad"
        write(bad / "dml/001_partial.sql", "INSERT INTO site_notes(body) VALUES ('kept?');\n"
                                           "INSERT INTO site_notes(id, body) VALUES (1, 'duplicate id');\n")
        write(bad / "dml/002_never.sql", "INSERT INTO site_notes(body) VALUES ('never');\n")
        with pytest.raises(RuntimeError, match="001_partial.sql 失败（已回滚该脚本）"):
            sqlscripts.apply_scripts(store, "2.0.2", sqlscripts.discover(bad), log=lambda _m: None)
        with store._db() as db:
            assert db.execute("SELECT COUNT(*) FROM site_notes").fetchone()[0] == 2
        assert sqlscripts.applied_scripts(store, "2.0.2") == []
        status = sqlscripts.status(store)
        assert status["scripts"] == 3 and status["active"] == {"jobs": 0, "deployments": 0}
        assert 4 in status["migrations"]
    finally:
        store.close()


def test_no_transaction_scripts_run_outside_a_transaction(tmp_path):
    store = new_store()
    try:
        folder = tmp_path / "sql"
        write(folder / "ddl/001_table.sql", "CREATE TABLE big_notes (id BIGINT PRIMARY KEY, body TEXT);\n")
        # CREATE INDEX CONCURRENTLY 不能在事务里执行
        write(folder / "ddl/002_index.sql", "-- swr:no-transaction\nCREATE INDEX CONCURRENTLY idx_big_notes_body ON big_notes (body);\n")
        scripts = sqlscripts.discover(folder)
        assert [s.transactional for s in scripts] == [True, False]
        sqlscripts.apply_scripts(store, "2.0.3", scripts, log=lambda _m: None)
        with store._db() as db:
            assert db.execute("SELECT COUNT(*) FROM pg_indexes WHERE indexname = 'idx_big_notes_body'").fetchone()[0] == 1
        assert [s["transactional"] for s in sqlscripts.applied_scripts(store, "2.0.3")] == [True, False]
    finally:
        store.close()


def test_command_line_apply_and_status(patch_dir, capsys, monkeypatch):
    store = new_store()
    try:
        monkeypatch.setenv("BUILDER_DATABASE_URL", store._conninfo)
        monkeypatch.delenv("BUILDER_CONFIG", raising=False)
        with pytest.raises(SystemExit) as done:
            main(["db", "apply-sql", "--dir", str(patch_dir), "--patch", "2.0.1", "--json"])
        assert done.value.code == 0
        output = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert [r["status"] for r in output["scripts"]] == ["applied"] * 3
        with pytest.raises(SystemExit):
            main(["db", "status", "--json"])
        report = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert report["scripts"] == 3 and report["active"]["jobs"] == 0
        assert report["pending"] == [] and report["unknown"] == [] and report["version"]
        write(patch_dir / "dml/002_broken.sql", "INSERT INTO missing_table VALUES (1);\n")
        # --check 只校验：新脚本列为将执行，已执行的列为跳过，不会真正执行
        with pytest.raises(SystemExit) as checked:
            main(["db", "apply-sql", "--dir", str(patch_dir), "--patch", "2.0.1", "--check", "--json"])
        assert checked.value.code == 0
        plan = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert [(p["path"], p["status"]) for p in plan["scripts"]][-2:] == [
            ("dml/001_seed.sql", "skipped"), ("dml/002_broken.sql", "pending")]
        assert sqlscripts.status(store)["scripts"] == 3
        with pytest.raises(SystemExit) as failed:
            main(["db", "apply-sql", "--dir", str(patch_dir), "--patch", "2.0.1"])
        assert failed.value.code == 1 and "002_broken.sql 失败" in capsys.readouterr().err
        write(patch_dir / "dml/001_seed.sql", "INSERT INTO site_notes(body) VALUES ('edited');\n")
        with pytest.raises(SystemExit) as edited:
            main(["db", "apply-sql", "--dir", str(patch_dir), "--patch", "2.0.1", "--check"])
        assert edited.value.code == 1 and "内容与本次不同" in capsys.readouterr().err
    finally:
        store.close()


def test_check_and_status_before_the_history_table_exists(patch_dir):
    from swr_builder.store.migrations import MIGRATIONS, apply_migrations
    store = new_store(migrate=False)
    try:
        apply_migrations(store, MIGRATIONS[:3])
        plan = sqlscripts.plan_scripts(store, "2.0.1", sqlscripts.discover(patch_dir))
        assert [p["status"] for p in plan] == ["pending"] * 3
        report = sqlscripts.status(store)
        assert report["migrations"] == [1, 2, 3] and report["pending"] == [v for v, _, _ in MIGRATIONS[3:]]
        assert report["scripts"] == 0 and report["unknown"] == []
        # 数据库由更新的程序迁移过：旧程序据此判断不能用它启动
        with store._db() as db:
            db.execute("INSERT INTO schema_migrations(version, name, checksum, applied_at) "
                       "VALUES (999, 'future', 'x', now())")
        assert sqlscripts.status(store)["unknown"] == [999]
    finally:
        store.close()
