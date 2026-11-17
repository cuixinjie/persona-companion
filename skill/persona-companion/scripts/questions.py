"""场景补问、跳过与回答来源。"""

import json
import sqlite3
import unicodedata
from pathlib import Path

from contracts import (ContractError, array, choice, encode, fields, identifier,
                       invalid, missing, new_id, string)
from materials import import_source

PURPOSES = ("missing", "conflict", "context", "counterexample")
ANSWER_KINDS = ("recollection", "hypothetical", "style_preference")


def list_questions(db: sqlite3.Connection, persona_id: str) -> list[dict]:
    return [
        {**dict(row), "target_claim_ids": json.loads(row["target_claim_ids"])}
        for row in db.execute(
            "SELECT q.id AS question_id, q.purpose, q.text, q.target_claim_ids, q.status,"
            " s.id AS answer_source_id"
            " FROM questions q LEFT JOIN sources s ON s.persona_id = q.persona_id"
            " AND s.id = q.answer_source_id AND s.status <> 'withdrawn'"
            " WHERE q.persona_id = ? ORDER BY q.rowid",
            (persona_id,),
        )
    ]


def create_question(db: sqlite3.Connection, persona: sqlite3.Row, payload: dict) -> dict:
    fields(payload, "action purpose target_claim_ids text", "purpose text", "payload")
    if persona["interview_status"] == "closed":
        raise ContractError("INTERVIEW_CLOSED", "访谈已结束。用户明确要求继续时再恢复。")
    persona_id = persona["id"]
    purpose = choice(payload["purpose"], "purpose", PURPOSES)
    message = string(payload["text"], "text").strip()
    key = " ".join(unicodedata.normalize("NFKC", message).casefold().split())
    key = key.rstrip("?。.!！ ")
    if not key:
        raise invalid("text")
    previous = db.execute(
        "SELECT id, status, skipped_revision FROM questions"
        " WHERE persona_id = ? AND question_key = ?"
        " ORDER BY rowid DESC LIMIT 1",
        (persona_id, key),
    ).fetchone()
    if previous is not None and previous["status"] == "pending":
        raise ContractError("QUESTION_PENDING", "此问题已在等待回答。", question_id=previous["id"])
    if previous is not None and previous["status"] == "answered":
        raise ContractError("QUESTION_CLOSED", "此问题已有回答。", question_id=previous["id"])
    if previous is not None and previous["skipped_revision"] is not None:
        new_material = db.execute(
            "SELECT 1 FROM audit a JOIN sources s ON s.persona_id = a.persona_id AND s.id = a.object_id"
            " WHERE a.persona_id = ? AND a.event = 'source.confirm' AND a.revision > ?"
            " AND s.status = 'confirmed' AND s.kind IN ('chat_image','chat_text','recollection') LIMIT 1",
            (persona_id, previous["skipped_revision"]),
        ).fetchone()
        if new_material is None:
            raise ContractError("QUESTION_SKIPPED", "此问题已跳过，目前没有新核对的历史材料。",
                                question_id=previous["id"])

    target_ids = [identifier(value, "target_claim_ids") for value in
                  array(payload.get("target_claim_ids", []), "target_claim_ids")]
    if len(set(target_ids)) != len(target_ids):
        raise invalid("target_claim_ids")
    pending = db.execute(
        "SELECT claim_ids FROM profile_proposals"
        " WHERE persona_id = ? AND status = 'pending' AND reviewed_version = ?",
        (persona_id, persona["revision"]),
    ).fetchone()
    current_ids = set(json.loads(pending["claim_ids"])) if pending is not None else set()
    current_ids.update(row[0] for row in db.execute(
        "SELECT id FROM claims WHERE persona_id = ? AND review_status = 'accepted'", (persona_id,),
    ))
    if not current_ids.issuperset(target_ids):
        raise missing()
    question_id = new_id()
    db.execute(
        "INSERT INTO questions (persona_id,id,target_claim_ids,purpose,text,status,question_key)"
        " VALUES (?, ?, ?, ?, ?, 'pending', ?)",
        (persona_id, question_id, encode(target_ids), purpose, message, key),
    )
    return {"question_id": question_id, "status": "pending"}


def record_question(db: sqlite3.Connection, root: Path, persona: sqlite3.Row, payload: dict) -> dict:
    action = choice(payload["action"], "action", ("create", "answer", "skip", "stop", "resume"))
    if action in ("stop", "resume"):
        fields(payload, "action", "action", "payload")
        return set_interview_status(db, persona, "closed" if action == "stop" else "open")
    if action == "create":
        return create_question(db, persona, payload)
    mode_fields = "action question_id answer" if action == "answer" else "action question_id"
    fields(payload, mode_fields, mode_fields, "payload")
    persona_id = persona["id"]
    question_id = identifier(payload["question_id"], "question_id")
    question = db.execute(
        "SELECT status FROM questions WHERE persona_id = ? AND id = ?", (persona_id, question_id),
    ).fetchone()
    if question is None:
        raise missing()
    if question["status"] != "pending":
        raise ContractError("QUESTION_CLOSED", "此问题已回答或跳过。")
    source_id = None
    skipped_revision = None
    if action == "answer":
        answer = fields(payload["answer"], "kind text context_period", "kind text", "answer")
        choice(answer["kind"], "answer.kind", ANSWER_KINDS)
        source_id = import_source(db, root, persona_id, answer)["source_id"]
        status = "answered"
    else:
        status = "skipped"
        skipped_revision = persona["revision"] + 1
    db.execute(
        "UPDATE questions SET status = ?, answer_source_id = ?, skipped_revision = ?"
        " WHERE persona_id = ? AND id = ?",
        (status, source_id, skipped_revision, persona_id, question_id),
    )
    return {"question_id": question_id, "status": status, "answer_source_id": source_id}


def set_interview_status(db: sqlite3.Connection, persona: sqlite3.Row, status: str) -> dict:
    if status != persona["interview_status"]:
        persona_id = persona["id"]
        db.execute("UPDATE personas SET interview_status = ? WHERE id = ?", (status, persona_id))
        if status == "closed":
            db.execute("UPDATE questions SET status = 'skipped', skipped_revision = ?"
                       " WHERE persona_id = ? AND status = 'pending'", (persona["revision"] + 1, persona_id))
        # 已准备的回复仍使用旧访谈状态。原会话可重新准备一轮。
        db.execute("UPDATE sessions SET turn_key = randomblob(32) WHERE persona_id = ? AND status = 'active'",
                   (persona_id,))
    return {"interview_status": status}
