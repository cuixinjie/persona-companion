"""交流摘要、设置与单独采纳的数字发展层。"""

import json
import sqlite3
import unicodedata

import dialogue
import profiles
from contracts import (ContractError, array, boolean, choice, digest, encode, fields,
                       identifier, invalid, missing, new_id, positive_integer, string)
from validation import utc_now

MEMORY_KINDS = ("user_update", "shared_exchange", "agreement")
DEVELOPMENT_CATEGORIES = ("interaction_habit", "simulated_trait")


def record(db: sqlite3.Connection, persona: sqlite3.Row, payload: dict) -> dict:
    turn = dialogue.validate_turn(db, persona, payload)
    kind = choice(payload["kind"], "kind", MEMORY_KINDS)
    text = string(payload["text"], "text", 500).strip()
    previous = db.execute(
        "SELECT id, kind, text FROM memories WHERE persona_id = ? AND session_id = ? AND source_turn_id = ?",
        (persona["id"], turn["session_id"], turn["source_turn_id"]),
    ).fetchone()
    if previous is not None:
        if previous["kind"] != kind or previous["text"] != text:
            raise ContractError("REQUEST_REUSED", "该轮对话已保存另一份交流摘要。")
        return {"memory_id": previous["id"], "status": "saved"}
    if not turn["memory_allowed"]:
        return {"status": "skipped_disabled"}
    memory_id = new_id()
    db.execute(
        "INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?, ?, 'active')",
        (persona["id"], memory_id, turn["session_id"], text, kind, turn["source_turn_id"], utc_now()),
    )
    return {"memory_id": memory_id, "status": "saved"}


def list_memories(db: sqlite3.Connection, persona: sqlite3.Row, payload: dict) -> dict:
    persona_id = persona["id"]
    limit = positive_integer(payload.get("limit", 20), "limit")
    if limit > 50:
        raise invalid("limit")
    before = ("\uffff", "\uffff")
    if "before_id" in payload:
        before_id = identifier(payload["before_id"], "before_id")
        row = db.execute("SELECT created_at, id FROM memories WHERE persona_id = ? AND id = ?",
                         (persona_id, before_id)).fetchone()
        if row is None:
            raise missing()
        before = tuple(row)
    rows = db.execute(
        "SELECT id AS memory_id, session_id, kind, text, created_at, status FROM memories"
        " WHERE persona_id = ? AND (created_at, id) < (?, ?)"
        " ORDER BY created_at DESC, id DESC LIMIT ?", (persona_id, *before, limit + 1),
    ).fetchall()
    return {"origin": "simulated_exchange", "memories": [dict(row) for row in rows[:limit]],
            "next_before_id": rows[limit - 1]["memory_id"] if len(rows) > limit else None}


def update_settings(db: sqlite3.Connection, persona: sqlite3.Row, payload: dict) -> dict:
    memory_enabled = boolean(payload["memory_enabled"], "memory_enabled")
    development_enabled = boolean(payload["development_enabled"], "development_enabled")
    if development_enabled and not memory_enabled:
        raise ContractError("INVALID_SETTINGS", "开启随交流发展前，须同时开启记住新交流。")
    db.execute("UPDATE personas SET memory_enabled = ?, development_enabled = ? WHERE id = ?",
               (memory_enabled, development_enabled, persona["id"]))
    affected = expire_proposals(db, persona["id"], persona["revision"] + 1)
    return {"settings": {"memory_enabled": memory_enabled, "development_enabled": development_enabled},
            "stale_proposal_ids": affected, "uses_existing_memories": True}


def expire_proposals(db: sqlite3.Connection, persona_id: str, revision: int) -> list[str]:
    return sorted(row[0] for row in db.execute(
        "UPDATE development_proposals SET status = 'stale' WHERE persona_id = ?"
        " AND status = 'pending' AND reviewed_version IS NOT ? RETURNING id",
        (persona_id, revision),
    ))


def scope_key(patch: dict) -> tuple[str, str]:
    return patch["category"], unicodedata.normalize("NFKC", patch["context"]).casefold()


def proposal_view(row: sqlite3.Row, profile: dict) -> dict:
    patch = json.loads(row["patch_json"])
    before = next((item for item in profile["development"] if scope_key(item) == scope_key(patch)), None)
    after = {**patch, "origin": "digital_development", "memory_ids": json.loads(row["memory_ids"]),
             "proposal_id": row["id"]}
    return {"proposal_id": row["id"], "status": row["status"],
            "base_version_id": row["base_version_id"], "reviewed_version": row["reviewed_version"],
            "diff": {"before": before, "after": after}}


def list_development(db: sqlite3.Connection, persona: sqlite3.Row) -> dict:
    profile = profiles.active_profile(db, persona)
    pending = None
    if profile is not None:
        row = db.execute(
            "SELECT * FROM development_proposals WHERE persona_id = ? AND base_version_id = ?"
            " AND status = 'pending' AND reviewed_version = ?",
            (persona["id"], profile["profile_version_id"], persona["revision"]),
        ).fetchone()
        if row is not None:
            pending = proposal_view(row, profile)
    return {"active": profile["development"] if profile else [], "pending": pending,
            "settings": profiles.settings(persona)}


def propose(db: sqlite3.Connection, persona: sqlite3.Row, payload: dict) -> dict:
    if not persona["development_enabled"]:
        raise ContractError("DEVELOPMENT_DISABLED", "随交流发展已关闭，未生成候选。")
    profile = dialogue.ready_profile(db, persona)
    raw = fields(payload["patch"], "category context behavior", "category context behavior", "patch")
    patch = {"category": choice(raw["category"], "category", DEVELOPMENT_CATEGORIES),
             "context": string(raw["context"], "context", 160).strip(),
             "behavior": string(raw["behavior"], "behavior", 500).strip()}
    memory_ids = sorted(identifier(value, "memory_ids")
                        for value in array(payload["memory_ids"], "memory_ids", 12))
    if not memory_ids or len(set(memory_ids)) != len(memory_ids):
        raise invalid("memory_ids")
    persona_id = persona["id"]
    for memory_id in memory_ids:
        if db.execute("SELECT 1 FROM memories WHERE persona_id = ? AND id = ? AND status = 'active'",
                      (persona_id, memory_id)).fetchone() is None:
            raise missing()
    fingerprint = digest(encode([scope_key(patch), memory_ids]).encode("utf-8"))
    if db.execute("SELECT 1 FROM development_proposals WHERE persona_id = ?"
                  " AND fingerprint = ? AND status = 'rejected'", (persona_id, fingerprint)).fetchone():
        raise ContractError("DEVELOPMENT_REJECTED", "此前已拒绝以同组交流依据调整这个情境。")
    proposal_id = new_id()
    row = db.execute(
        "INSERT INTO development_proposals"
        " (persona_id,id,base_version_id,patch_json,memory_ids,status,reviewed_version,fingerprint)"
        " VALUES (?, ?, ?, ?, ?, 'pending', ?, ?) RETURNING *",
        (persona_id, proposal_id, profile["profile_version_id"], encode(patch), encode(memory_ids),
         persona["revision"] + 1, fingerprint),
    ).fetchone()
    return proposal_view(row, profile)


def resolve_proposal(db: sqlite3.Connection, persona: sqlite3.Row, payload: dict, *, accept: bool) -> dict:
    persona_id = persona["id"]
    proposal_id = identifier(payload["proposal_id"], "proposal_id")
    reviewed_version = positive_integer(payload["reviewed_version"], "reviewed_version")
    row = db.execute("SELECT * FROM development_proposals WHERE persona_id = ? AND id = ?",
                     (persona_id, proposal_id)).fetchone()
    if row is None:
        raise missing()
    if (not persona["development_enabled"] or row["status"] != "pending"
            or reviewed_version != row["reviewed_version"] or reviewed_version != persona["revision"]
            or row["base_version_id"] != persona["active_version_id"]):
        raise ContractError("PROPOSAL_STALE", "发展候选已失效，请重新读取。")
    status = "accepted" if accept else "rejected"
    db.execute("UPDATE development_proposals SET status = ? WHERE persona_id = ? AND id = ?",
               (status, persona_id, proposal_id))
    result = {"proposal_id": proposal_id, "status": status}
    if accept:
        profile = dialogue.ready_profile(db, persona)
        change = proposal_view(row, profile)["diff"]["after"]
        development = [item for item in profile["development"] if scope_key(item) != scope_key(change)]
        development.append(change)
        version_id, version_no = profiles.append_version(
            db, persona, profile["claims"], profile["unknowns"], profile["fallback_accepted"],
            development=development,
        )
        result.update(profile_version_id=version_id, version_no=version_no)
    return result
