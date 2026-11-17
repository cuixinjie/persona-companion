"""SQLite 初始化、事务、人物修订号和幂等记录。"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from contracts import ContractError, encode, missing, new_id
from validation import scoped_path, utc_now

MIGRATIONS = ("001_initial.sql", "002_persona_construction.sql", "003_dialogue.sql", "004_interview_status.sql")
SCHEMA_VERSION = len(MIGRATIONS)
MIGRATION_DIR = Path(__file__).resolve().parent / "migrations"


@contextmanager
def transaction(db: sqlite3.Connection, *, write: bool = True) -> Iterator[None]:
    db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
    try:
        yield
        db.commit()
    except BaseException:
        db.rollback()
        raise


def initialize(db: sqlite3.Connection) -> None:
    if db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION:
        return
    with transaction(db):
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION:  # 另一个进程可能刚完成初始化。
            return
        if (not 0 <= version < SCHEMA_VERSION
                or (version == 0 and db.execute("SELECT 1 FROM sqlite_schema LIMIT 1").fetchone())):
            raise ContractError("SCHEMA_UNSUPPORTED", "数据库版本不兼容，请使用匹配的脚本版本。")
        for name in MIGRATIONS[version:]:
            sql = ""
            for line in (MIGRATION_DIR / name).read_text(encoding="utf-8").splitlines(keepends=True):
                sql += line
                if sqlite3.complete_statement(sql):
                    db.execute(sql)
                    sql = ""
            if sql.strip():
                raise ContractError("STORAGE_FAILED", "数据库迁移脚本不完整。")
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


@contextmanager
def connect(root: Path) -> Iterator[sqlite3.Connection]:
    root.mkdir(parents=True, exist_ok=True)
    for suffix in ("-journal", "-wal", "-shm"):
        scoped_path(root, Path("personas.sqlite3" + suffix))
    database = scoped_path(root, Path("personas.sqlite3"))
    db = sqlite3.connect(database, timeout=3, isolation_level=None)
    try:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA secure_delete = ON")
        initialize(db)
        yield db
    finally:
        db.close()


def get_persona(db: sqlite3.Connection, persona_id: str) -> sqlite3.Row:
    row = db.execute("SELECT * FROM personas WHERE id = ? AND status <> 'deleting'",
                     (persona_id,)).fetchone()
    if row is None:
        raise missing()
    return row


def check_revision(persona: sqlite3.Row, expected: int) -> None:
    if persona["revision"] != expected:
        raise ContractError("REVISION_CONFLICT", "人物数据已更新，请重新读取后提交。",
                            current_revision=persona["revision"])


def advance_revision(db: sqlite3.Connection, persona: sqlite3.Row, event: str, object_id: str) -> int:
    revision = persona["revision"] + 1
    db.execute("UPDATE personas SET revision = ? WHERE id = ?", (revision, persona["id"]))
    audit(db, persona["id"], event, object_id, revision)
    return revision


def audit(db: sqlite3.Connection, persona_id: str, event: str, object_id: str, revision: int) -> None:
    db.execute("INSERT INTO audit VALUES (?, ?, ?, ?, ?, ?)",
               (persona_id, new_id(), event, object_id, revision, utc_now()))


def check_hash(saved: str, current: str) -> None:
    if saved != current:
        raise ContractError("REQUEST_REUSED", "此请求 ID 已用于另一份请求。")


def replay(db: sqlite3.Connection, request: dict, payload_hash: str) -> dict | None:
    request_id = request["request_id"]
    row = db.execute("SELECT * FROM operations WHERE request_id = ?", (request_id,)).fetchone()
    if row is not None:
        check_hash(row["payload_hash"], payload_hash)
        get_persona(db, row["persona_id"])
        return json.loads(row["result_json"])
    row = db.execute("SELECT payload_hash FROM deletion_tombstones WHERE request_id = ?",
                     (request_id,)).fetchone()
    if row is not None:
        if row["payload_hash"] is None:
            raise missing()
        check_hash(row["payload_hash"], payload_hash)
        return success(request, {"status": "deleted"}, None)
    return None


def pending_request(db: sqlite3.Connection, request_id: str) -> sqlite3.Row | None:
    return db.execute("SELECT * FROM pending_deletions WHERE request_id = ?", (request_id,)).fetchone()


def success(request: dict, data: dict, revision: int | None) -> dict:
    return {"ok": True, "request_id": request["request_id"], "data": data, "revision": revision}


def save_result(db: sqlite3.Connection, request: dict, persona_id: str,
                payload_hash: str, result: dict) -> None:
    db.execute("INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?)",
               (request["request_id"], request["operation"], persona_id, payload_hash,
                encode(result), utc_now()))
