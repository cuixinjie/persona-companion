"""人物、档案提案、采纳、撤回与删除。"""

import json
import secrets
import sqlite3
from collections.abc import Iterable, Iterator
from datetime import datetime, timedelta
from pathlib import Path

from contracts import (CATEGORIES, LEVELS, ORIGINS, ContractError, array, boolean, choice,
                       digest, encode, fields, identifier, invalid, missing, new_id,
                       positive_integer, scope_label, string)
from materials import CONFIRMED_EVIDENCE_BY_ID, cleanup_files, get_source
from questions import list_questions
from storage import (advance_revision, audit, check_hash, check_revision, get_persona,
                     pending_request, replay, success, transaction)
from validation import utc_now

FALLBACK_STYLE = "简洁、尊重、少判断。不编造共同经历、感情承诺或未知偏好。"
HISTORICAL_KINDS = {"chat_image", "chat_text", "recollection"}


def settings(persona: sqlite3.Row) -> dict:
    return {name: bool(persona[name]) for name in ("memory_enabled", "development_enabled")}


def summary(persona: sqlite3.Row) -> dict:
    result = {key: persona[key] for key in ("display_name", "relationship", "target_period",
                                           "status", "active_version_id", "revision")}
    return {"persona_id": persona["id"], **result, "settings": settings(persona)}


def create_persona(db: sqlite3.Connection, payload: dict) -> dict:
    persona_id = new_id()
    name = string(payload["display_name"], "display_name", 80).strip()
    relationship = string(payload.get("relationship", "unknown"), "relationship")
    period = string(payload.get("target_period", "unknown"), "target_period")
    terms = fields(payload.get("address_terms", {}), "user_to_persona persona_to_user",
                   "", "address_terms")
    terms = {key: string(terms.get(key, "unknown"), key)
             for key in ("user_to_persona", "persona_to_user")}
    db.execute(
        "INSERT INTO personas (id,display_name,relationship,target_period,address_terms,status,created_at)"
        " VALUES (?, ?, ?, ?, ?, 'draft', ?)",
        (persona_id, name, relationship, period, encode(terms), utc_now()),
    )
    audit(db, persona_id, "persona.create", persona_id, 1)
    return summary(get_persona(db, persona_id))


def list_personas(db: sqlite3.Connection) -> dict:
    return {"personas": [summary(row) for row in db.execute(
        "SELECT * FROM personas WHERE status <> 'deleting' ORDER BY created_at, id"
    )]}


def update_persona(db: sqlite3.Connection, persona: sqlite3.Row, payload: dict) -> dict:
    if not payload:
        raise invalid("payload")
    persona_id = persona["id"]
    scope = {key: string(payload[key], key).strip() if key in payload else persona[key]
             for key in ("relationship", "target_period")}
    if any(value != persona[key] for key, value in scope.items()):
        db.execute("UPDATE personas SET relationship = ?, target_period = ? WHERE id = ?",
                   (scope["relationship"], scope["target_period"], persona_id))
        profile = active_profile(db, persona)
        if profile is not None:
            # 保留历史结论的范围，开启新版本以隔离旧范围下的模拟摘要及待发回复。
            append_version(db, get_persona(db, persona_id), profile["claims"],
                           profile["unknowns"], profile["fallback_accepted"])
    current = get_persona(db, persona_id)
    return {"persona_id": persona_id, **scope, "active_version_id": current["active_version_id"]}


def active_profile(db: sqlite3.Connection, persona: sqlite3.Row) -> dict | None:
    if persona["active_version_id"] is None:
        return None
    version = db.execute(
        "SELECT * FROM profile_versions WHERE persona_id = ? AND id = ?",
        (persona["id"], persona["active_version_id"]),
    ).fetchone()
    if version is None:
        raise ContractError("STORAGE_FAILED", "当前档案版本不可用。")
    return {
        "profile_version_id": version["id"], "version_no": version["version_no"],
        "development": [],
        **json.loads(version["snapshot_json"]),
        "provenance": json.loads(version["provenance_json"]),
    }


def get_profile(db: sqlite3.Connection, persona: sqlite3.Row, payload: dict) -> dict:
    data = {**summary(persona), "address_terms": json.loads(persona["address_terms"]),
            "interview_status": persona["interview_status"]}
    if boolean(payload.get("include_profile", False), "include_profile"):
        persona_id = persona["id"]
        data["profile"] = active_profile(db, persona)
        pending = db.execute(
            "SELECT * FROM profile_proposals WHERE persona_id = ? AND status = 'pending' AND reviewed_version = ?",
            (persona_id, persona["revision"]),
        ).fetchone()
        data["pending_proposal"] = None
        if pending is not None:
            try:
                claims, correction = load_proposal_claims(db, pending)
            except ContractError as error:
                if error.code != "PROPOSAL_STALE":
                    raise
                data["proposal_needing_review_id"] = pending["id"]
            else:
                data["pending_proposal"] = proposal_view(pending, data["profile"], claims, correction)
        if data["profile"] is not None:
            data["period_conflict_claim_ids"] = [
                claim["claim_id"] for claim in data["profile"]["claims"]
                if scope_label(claim["scope"]["period"]) != "unknown"
                and conflicting_periods(claim["scope"]["period"], claim_evidence_periods(db, persona_id, claim))
            ]
        data["applied_corrections"] = applied_corrections(db, persona_id)
        data["questions"] = list_questions(db, persona_id)
        data["sources"] = [dict(row) for row in db.execute(
            "SELECT id AS source_id, kind, original_path, original_text, context_period,"
            " target_speaker, status FROM sources WHERE persona_id = ? AND status <> 'withdrawn'"
            " ORDER BY created_at, id",
            (persona_id,),
        )]
        data["evidence"] = [dict(row) for row in db.execute(
            "SELECT e.id AS evidence_id, e.source_id, e.locator, e.text, e.speaker, e.context,"
            " e.occurred_at, e.event_key, e.verification FROM evidence e JOIN sources s"
            " ON s.persona_id = e.persona_id AND s.id = e.source_id"
            " WHERE e.persona_id = ? AND s.status = 'confirmed' ORDER BY e.source_id, e.locator",
            (persona_id,),
        )]
    return data


def applied_corrections(db: sqlite3.Connection, persona_id: str) -> list[dict]:
    corrections = []
    for row in db.execute(
        "SELECT correction_json FROM profile_proposals WHERE persona_id = ?"
        " AND status = 'accepted' AND correction_json IS NOT NULL ORDER BY reviewed_version",
        (persona_id,),
    ):
        correction = json.loads(row["correction_json"])
        prior = db.execute(
            "SELECT category, statement, scope FROM claims WHERE persona_id = ? AND id = ?",
            (persona_id, correction["claim_id"]),
        ).fetchone()
        corrections.append({**correction, "previous_claim": {**dict(prior), "scope": json.loads(prior["scope"])}})
    return corrections


def usable_evidence(db: sqlite3.Connection, persona_id: str, evidence_id: str) -> sqlite3.Row | None:
    return db.execute(
        "SELECT s.kind, s.context_period, e.event_key" + CONFIRMED_EVIDENCE_BY_ID,
        (persona_id, evidence_id),
    ).fetchone()


def conflicting_periods(period: str, evidence_periods: Iterable[str]) -> list[str]:
    requested = scope_label(period)
    if requested == "unknown":
        return []
    return sorted({value for value in evidence_periods if scope_label(value) not in ("unknown", requested)})


def historical_periods(evidence: Iterable[sqlite3.Row | None]) -> Iterator[str]:
    return (row["context_period"] for row in evidence if row is not None and row["kind"] in HISTORICAL_KINDS)


def claim_evidence_periods(db: sqlite3.Connection, persona_id: str, claim: dict) -> list[str]:
    ids = claim["evidence_ids"] + claim.get("contradicting_evidence_ids", [])
    return list(historical_periods(usable_evidence(db, persona_id, evidence_id) for evidence_id in ids))


def claim_matches_period(db: sqlite3.Connection, persona_id: str, claim: dict, period: str) -> bool:
    claim_period = claim["scope"]["period"]
    if scope_label(claim_period) == scope_label(period) == "unknown":
        return True
    evidence_periods = claim_evidence_periods(db, persona_id, claim)
    return not (conflicting_periods(claim_period, evidence_periods) or conflicting_periods(period, evidence_periods))


def claim_supported(claim: dict, support: list[sqlite3.Row], contradict: list[sqlite3.Row]) -> bool:
    level = claim["evidence_level"]
    origin = claim["origin"]
    category = claim["category"]
    kinds = {row["kind"] for row in support}
    if conflicting_periods(claim["scope"]["period"], historical_periods(support + contradict)):
        return False
    if origin == "user_defined" and category != "style":
        return False
    if level == "unknown":
        return not support and not contradict
    if contradict and level == "supported":
        return False
    if origin == "user_defined":
        return kinds <= {"style_preference"}
    if not support:
        return False
    if kinds - HISTORICAL_KINDS:
        if kinds - HISTORICAL_KINDS != {"hypothetical"} or category != "situational" or level != "tentative":
            return False
    if origin == "recalled" and kinds != {"recollection"}:
        return False
    if category == "trait" and level == "supported":
        events = {row["event_key"] for row in support if row["event_key"] not in (None, "unknown")}
        return len(events) >= 2
    return True


def proposal_claim(db: sqlite3.Connection, persona_id: str, value: dict,
                   *, supersedes_id: str | None = None) -> dict:
    fields(value, "category statement evidence_level origin scope evidence_ids contradicting_evidence_ids",
           "category statement evidence_level origin scope evidence_ids", "claim")
    scope = fields(value["scope"], "relationship period context", "relationship period context", "scope")
    claim = {
        "claim_id": new_id(),
        "category": choice(value["category"], "category", CATEGORIES),
        "statement": string(value["statement"], "statement"),
        "evidence_level": choice(value["evidence_level"], "evidence_level", LEVELS),
        "origin": choice(value["origin"], "origin", ORIGINS),
        "scope": {key: string(item, key) for key, item in scope.items()},
    }
    cited = set()
    links = []
    evidence_by_relation = {}
    for field, relation in (("evidence_ids", "support"), ("contradicting_evidence_ids", "contradict")):
        evidence_by_relation[relation] = []
        claim[field] = []
        for value_id in array(value.get(field, []), field):
            evidence_id = identifier(value_id, field)
            if evidence_id in cited:
                raise invalid(field)
            row = usable_evidence(db, persona_id, evidence_id)
            if row is None:
                raise missing()
            cited.add(evidence_id)
            claim[field].append(evidence_id)
            links.append((persona_id, claim["claim_id"], evidence_id, relation))
            evidence_by_relation[relation].append(row)
        claim[field].sort()
    if not claim_supported(claim, evidence_by_relation["support"], evidence_by_relation["contradict"]):
        periods = conflicting_periods(claim["scope"]["period"], historical_periods(
            evidence_by_relation["support"] + evidence_by_relation["contradict"]))
        if periods:
            raise ContractError("OUTPUT_INVALID", "结论时期与所引用的材料时期不一致，请重新核对。",
                                field="scope.period", claim_period=claim["scope"]["period"], evidence_periods=periods)
        raise ContractError("OUTPUT_INVALID", "结论的来源、支持程度或适用范围需要重新核对。")
    db.execute(
        "INSERT INTO claims VALUES (?, ?, ?, ?, ?, ?, ?, 'proposed', ?)",
        (persona_id, claim["claim_id"], claim["category"], claim["statement"],
         claim["evidence_level"], claim["origin"], encode(claim["scope"]), supersedes_id),
    )
    db.executemany("INSERT INTO claim_evidence VALUES (?, ?, ?, ?)", links)
    return claim


def propose_profile(db: sqlite3.Connection, persona: sqlite3.Row, payload: dict) -> dict:
    persona_id = persona["id"]
    unknowns = [string(item, "unknowns") for item in array(payload["unknowns"], "unknowns")]
    if len(set(unknowns)) != len(unknowns):
        raise invalid("unknowns")
    claims = [proposal_claim(db, persona_id, item) for item in array(payload["claims"], "claims")]
    return create_proposal(db, persona, claims, unknowns)


def create_proposal(db: sqlite3.Connection, persona: sqlite3.Row, claims: list[dict],
                    unknowns: list[str], *, correction: dict | None = None) -> dict:
    persona_id = persona["id"]
    proposal_id = new_id()
    reviewed_version = persona["revision"] + 1
    db.execute("UPDATE profile_proposals SET status = 'stale' WHERE persona_id = ? AND status = 'pending'",
               (persona_id,))
    proposal = db.execute(
        "INSERT INTO profile_proposals"
        " (persona_id,id,reviewed_version,claim_ids,unknowns,status,correction_json)"
        " VALUES (?, ?, ?, ?, ?, 'pending', ?) RETURNING *",
        (persona_id, proposal_id, reviewed_version,
         encode([claim["claim_id"] for claim in claims]), encode(unknowns),
         encode(correction) if correction is not None else None),
    ).fetchone()
    if persona["status"] == "draft":
        db.execute("UPDATE personas SET status = 'reviewing' WHERE id = ?", (persona_id,))
    return proposal_view(proposal, active_profile(db, persona), claims, correction)


def proposal_view(proposal: sqlite3.Row, profile: dict | None, claims: list[dict],
                  correction: dict | None) -> dict:
    result = {
        "proposal_id": proposal["id"], "reviewed_version": proposal["reviewed_version"],
        "validation_issues": [],
        "diff": {"before": profile, "after": {"claims": claims, "unknowns": json.loads(proposal["unknowns"])}},
    }
    if correction is not None:
        result["correction"] = correction
    return result


def expire_proposals(db: sqlite3.Connection, persona_id: str, revision: int) -> None:
    changed = db.execute(
        "UPDATE profile_proposals SET status = 'stale' WHERE persona_id = ?"
        " AND status = 'pending' AND reviewed_version <> ?",
        (persona_id, revision),
    )
    if changed.rowcount:
        db.execute("UPDATE personas SET status = 'draft' WHERE id = ? AND status = 'reviewing'",
                   (persona_id,))


def claim_detail(db: sqlite3.Connection, persona_id: str, row: sqlite3.Row) -> tuple[dict, dict]:
    claim = {key: row[key] for key in ("category", "statement", "evidence_level", "origin")}
    claim.update(claim_id=row["id"], scope=json.loads(row["scope"]),
                 evidence_ids=[], contradicting_evidence_ids=[])
    linked = {"support": [], "contradict": []}
    for link in db.execute(
        "SELECT evidence_id, relation FROM claim_evidence WHERE persona_id = ? AND claim_id = ? ORDER BY evidence_id",
        (persona_id, row["id"]),
    ):
        evidence = usable_evidence(db, persona_id, link["evidence_id"])
        if evidence is not None:
            relation = link["relation"]
            linked[relation].append(evidence)
            key = "evidence_ids" if relation == "support" else "contradicting_evidence_ids"
            claim[key].append(link["evidence_id"])
    return claim, linked


def append_version(db: sqlite3.Connection, persona: sqlite3.Row, claims: list[dict],
                   unknowns: list[str], fallback: bool, *,
                   development: list[dict] | None = None) -> tuple[str, int]:
    persona_id = persona["id"]
    version_id = new_id()
    version_no = db.execute("SELECT COALESCE(MAX(version_no), 0) + 1 FROM profile_versions WHERE persona_id = ?",
                            (persona_id,)).fetchone()[0]
    if development is None:
        # 历史层变更后，早先的模拟摘要和发展可能复述已退出的依据。
        db.execute("UPDATE memories SET status = 'archived' WHERE persona_id = ? AND status = 'active'",
                   (persona_id,))
    snapshot = {"claims": claims, "unknowns": unknowns, "fallback_accepted": fallback,
                "fallback_style": FALLBACK_STYLE if fallback else None,
                "development": development if development is not None else []}
    provenance = {"accepted_claim_ids": [claim["claim_id"] for claim in claims], "settings": settings(persona)}
    db.execute("INSERT INTO profile_versions VALUES (?, ?, ?, ?, ?, ?)",
               (persona_id, version_id, version_no, encode(snapshot), encode(provenance), utc_now()))
    ready = fallback or has_known_claim(db, persona, claims)
    db.execute("UPDATE personas SET active_version_id = ?, status = ? WHERE id = ?",
               (version_id, "ready" if ready else "draft", persona_id))
    return version_id, version_no


def has_known_claim(db: sqlite3.Connection, persona: sqlite3.Row, claims: list[dict]) -> bool:
    relationship = scope_label(persona["relationship"])
    period = scope_label(persona["target_period"])
    return any(claim["evidence_level"] != "unknown"
               and (relationship == "unknown" or scope_label(claim["scope"]["relationship"]) in ("unknown", relationship))
               and (period == "unknown" or scope_label(claim["scope"]["period"]) in ("unknown", period))
               and claim_matches_period(db, persona["id"], claim, period)
               for claim in claims)


def accept_profile(db: sqlite3.Connection, persona: sqlite3.Row, payload: dict) -> dict:
    persona_id = persona["id"]
    proposal_id = identifier(payload["proposal_id"], "proposal_id")
    reviewed_version = positive_integer(payload["reviewed_version"], "reviewed_version")
    fallback = boolean(payload["fallback_accepted"], "fallback_accepted")
    proposal = db.execute("SELECT * FROM profile_proposals WHERE persona_id = ? AND id = ?",
                          (persona_id, proposal_id)).fetchone()
    if proposal is None:
        raise missing()
    if proposal["status"] != "pending" or proposal["reviewed_version"] != reviewed_version \
            or reviewed_version != persona["revision"]:
        raise ContractError("PROPOSAL_STALE", "提案已变化，请重新展示并审阅。")
    return apply_proposal(db, persona, proposal, fallback)


def load_proposal_claims(db: sqlite3.Connection, proposal: sqlite3.Row) -> tuple[list[dict], dict | None]:
    persona_id = proposal["persona_id"]
    correction = json.loads(proposal["correction_json"]) if proposal["correction_json"] is not None else None
    allowed = {"proposed", "accepted"} if correction is not None else {"proposed"}
    claims = []
    for claim_id in json.loads(proposal["claim_ids"]):
        row = db.execute("SELECT * FROM claims WHERE persona_id = ? AND id = ?",
                         (persona_id, claim_id)).fetchone()
        if row is None or row["review_status"] not in allowed:
            raise ContractError("PROPOSAL_STALE", "提案含已失效的结论。")
        claim, linked = claim_detail(db, persona_id, row)
        if not claim_supported(claim, linked["support"], linked["contradict"]):
            raise ContractError("PROPOSAL_STALE", "提案的来源已失效。")
        claims.append(claim)
    return claims, correction


def apply_proposal(db: sqlite3.Connection, persona: sqlite3.Row, proposal: sqlite3.Row,
                   fallback: bool) -> dict:
    persona_id = persona["id"]
    claims, correction = load_proposal_claims(db, proposal)
    if correction is None and not fallback and not has_known_claim(db, persona, claims):
        raise ContractError("CONFIRMATION_REQUIRED", "资料不足，须明确接受临时风格才能采纳。")
    if correction is None:
        db.execute("UPDATE claims SET review_status = 'superseded'"
                   " WHERE persona_id = ? AND review_status = 'accepted'", (persona_id,))
    else:
        status = "rejected" if correction["action"] == "reject" else "superseded"
        db.execute("UPDATE claims SET review_status = ? WHERE persona_id = ? AND id = ?",
                   (status, persona_id, correction["claim_id"]))
    db.executemany("UPDATE claims SET review_status = 'accepted'"
                   " WHERE persona_id = ? AND id = ? AND review_status = 'proposed'",
                   ((persona_id, claim["claim_id"]) for claim in claims))
    db.execute("UPDATE profile_proposals SET status = 'accepted' WHERE persona_id = ? AND id = ?",
               (persona_id, proposal["id"]))
    version_id, version_no = append_version(db, persona, claims, json.loads(proposal["unknowns"]), fallback)
    return {"profile_version_id": version_id, "version_no": version_no}


def correct_profile(db: sqlite3.Connection, persona: sqlite3.Row, payload: dict) -> dict:
    persona_id = persona["id"]
    claim_id = identifier(payload["claim_id"], "claim_id")
    action = choice(payload["action"], "action", ("replace", "reject"))
    reason = string(payload["reason"], "reason")
    immediate = boolean(payload.get("apply_immediately", False), "apply_immediately")
    if ("replacement" in payload) != (action == "replace"):
        raise invalid("replacement")
    profile = active_profile(db, persona)
    if profile is None or not any(claim["claim_id"] == claim_id for claim in profile["claims"]):
        raise missing()
    replacement = None
    if action == "replace":
        replacement = proposal_claim(db, persona_id, payload["replacement"], supersedes_id=claim_id)
    claims = []
    for claim in profile["claims"]:
        if claim["claim_id"] != claim_id:
            claims.append(claim)
        elif replacement is not None:
            claims.append(replacement)
    correction = {"claim_id": claim_id, "action": action, "reason": reason}
    data = create_proposal(db, persona, claims, profile["unknowns"], correction=correction)
    if immediate:
        proposal = db.execute("SELECT * FROM profile_proposals WHERE persona_id = ? AND id = ?",
                              (persona_id, data["proposal_id"])).fetchone()
        data.update(apply_proposal(db, persona, proposal, profile["fallback_accepted"]))
    return data


def withdraw_source(db: sqlite3.Connection, persona: sqlite3.Row, payload: dict) -> dict:
    persona_id = persona["id"]
    source_id = identifier(payload["source_id"], "source_id")
    get_source(db, persona_id, source_id)
    db.execute("UPDATE sources SET status = 'withdrawn' WHERE persona_id = ? AND id = ?", (persona_id, source_id))
    affected = db.execute(
        "SELECT DISTINCT c.* FROM claims c JOIN claim_evidence ce"
        " ON c.persona_id = ce.persona_id AND c.id = ce.claim_id"
        " JOIN evidence e ON e.persona_id = ce.persona_id AND e.id = ce.evidence_id"
        " WHERE c.persona_id = ? AND e.source_id = ? AND c.review_status IN ('accepted','proposed')"
        " ORDER BY c.id",
        (persona_id, source_id),
    ).fetchall()
    invalidated = []
    active_changes = {}
    for row in affected:
        claim, linked = claim_detail(db, persona_id, row)
        valid = (row["review_status"] == "accepted" and not linked["contradict"]
                 and claim_supported(claim, linked["support"], linked["contradict"])
                 and (claim["origin"] != "user_defined" or bool(linked["support"])))
        if row["review_status"] == "accepted":
            active_changes[row["id"]] = claim if valid else None
        if not valid:
            db.execute("UPDATE claims SET review_status = 'needs_review' WHERE persona_id = ? AND id = ?",
                       (persona_id, row["id"]))
            invalidated.append(row["id"])
    version_id = None
    if active_changes:
        profile = active_profile(db, persona)
        claims = [active_changes.get(claim["claim_id"], claim) for claim in profile["claims"]]
        version_id, _ = append_version(db, persona, [claim for claim in claims if claim is not None],
                                       profile["unknowns"], profile["fallback_accepted"])
    db.execute("UPDATE profile_proposals SET status = 'stale' WHERE persona_id = ? AND status = 'pending'",
               (persona_id,))
    if persona["status"] == "reviewing":
        db.execute("UPDATE personas SET status = 'draft' WHERE id = ?", (persona_id,))
    return {"source_id": source_id, "invalidated_claim_ids": invalidated, "new_version_id": version_id}


def preview_delete(db: sqlite3.Connection, persona: sqlite3.Row) -> dict:
    token = secrets.token_hex(32)
    expires_at = (datetime.fromisoformat(utc_now()) + timedelta(minutes=10)).isoformat(timespec="microseconds")
    db.execute("DELETE FROM deletion_confirmations WHERE persona_id = ? AND expires_at <= ?",
               (persona["id"], utc_now()))
    db.execute("INSERT INTO deletion_confirmations VALUES (?, ?, ?, ?)",
               (persona["id"], digest(token.encode("ascii")), persona["revision"], expires_at))
    return {"persona_id": persona["id"], "display_name": persona["display_name"],
            "confirmation_token": token, "expires_at": expires_at,
            "deletes": ["本人物在技能数据库中的记录及其管理的附件"],
            "excludes": ["私有备份", "宿主会话历史与附件", "导入原件及请求文件"]}


def delete_persona(db: sqlite3.Connection, root: Path, request: dict,
                   payload_hash: str, token: str) -> dict:
    persona_id = request["persona_id"]
    request_id = request["request_id"]
    with transaction(db):
        previous = replay(db, request, payload_hash)
        if previous is not None:
            return previous
        pending = pending_request(db, request_id)
        if pending is not None:
            check_hash(pending["payload_hash"], payload_hash)
        else:
            persona = get_persona(db, persona_id)
            check_revision(persona, request["expected_revision"])
            confirmation = db.execute(
                "SELECT * FROM deletion_confirmations WHERE persona_id = ? AND token_hash = ?",
                (persona_id, digest(token.encode("ascii"))),
            ).fetchone()
            if confirmation is None or confirmation["revision"] != persona["revision"]:
                raise ContractError("CONFIRMATION_REQUIRED", "请重新预览并确认本人物的删除范围。")
            if confirmation["expires_at"] <= utc_now():
                raise ContractError("CONFIRMATION_EXPIRED", "删除确认已过期，请重新预览。")
            db.execute("INSERT INTO pending_deletions VALUES (?, ?, ?)", (persona_id, request_id, payload_hash))
            db.execute("UPDATE personas SET status = 'deleting' WHERE id = ?", (persona_id,))
            advance_revision(db, persona, "persona.delete.pending", persona_id)

    # pending 必须先持久化。文件移除中断时，读取仍被阻止，原请求可从此继续。
    with transaction(db):
        previous = replay(db, request, payload_hash)
        if previous is not None:
            return previous
        cleanup_files(db, root, persona_id, deleting=True)
        db.execute("INSERT INTO deletion_tombstones SELECT request_id, NULL FROM operations WHERE persona_id = ?",
                   (persona_id,))
        db.execute("UPDATE personas SET active_version_id = NULL WHERE id = ?", (persona_id,))
        db.execute("DELETE FROM personas WHERE id = ?", (persona_id,))
        db.execute("INSERT INTO deletion_tombstones VALUES (?, ?)", (request_id, payload_hash))
    return success(request, {"status": "deleted"}, None)

