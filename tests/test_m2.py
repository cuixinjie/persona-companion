"""M2 人物构建协议。素材与人物均为合成。"""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from unittest.mock import patch
from uuid import uuid4

from test_m1 import M1Fixture, SCRIPTS, persona_cli


class M2Tests(M1Fixture):
    def question(self, persona, revision, *, text="忙的时候他怎么回复？", claims=None):
        return self.ok("question.record", {
            "action": "create", "purpose": "context", "text": text, "target_claim_ids": claims or [],
        }, persona, revision)

    def test_sparse_profile_skipped_question_and_explicit_fallback(self):
        person, revision = self.create()
        question, revision = self.question(person, revision)
        skip = self.request("question.record", {"action": "skip", "question_id": question["question_id"]},
                            person, revision)
        code, skipped = self.cli(skip)
        self.assertEqual(code, 0)
        self.assertEqual(self.cli(skip, child=True), (code, skipped))
        revision = skipped["revision"]
        duplicate = {"action": "create", "purpose": "missing", "text": " 忙的时候他怎么回复? "}
        self.error(self.request("question.record", duplicate, person, revision), "QUESTION_SKIPPED")
        proposal, revision = self.propose(person, revision, [])
        self.error(self.request("profile.accept", {
            "proposal_id": proposal["proposal_id"], "reviewed_version": revision, "fallback_accepted": False,
        }, person, revision), "CONFIRMATION_REQUIRED")
        accepted, revision = self.accept(person, revision, proposal, fallback=True)
        data, revision = self.current(person, child=True)
        self.assertEqual(data["profile"]["profile_version_id"], accepted["profile_version_id"])
        self.assertEqual(data["profile"]["claims"], [])
        self.assertEqual(data["questions"][0]["status"], "skipped")
        self.assertEqual(data["relationship"], "unknown")
        self.assertIsNone(data["pending_proposal"])

        _, revision = self.ok("source.import", {"kind": "chat_text", "text": "待核对"}, person, revision)
        self.error(self.request("question.record", duplicate, person, revision), "QUESTION_SKIPPED")
        _, _, revision = self.import_confirm(person, revision, kind="style_preference")
        self.error(self.request("question.record", duplicate, person, revision), "QUESTION_SKIPPED")
        _, _, revision = self.import_confirm(person, revision, text="忙完再回你。")
        new_question, _ = self.ok("question.record", duplicate, person, revision)
        self.assertNotEqual(new_question["question_id"], question["question_id"])

    def test_answer_sources_stay_typed_and_require_confirmation(self):
        person, revision = self.create()
        other, other_revision = self.create("另一个人")
        source_ids = {}
        evidence_ids = {}
        for kind in ("recollection", "hypothetical", "style_preference"):
            question, revision = self.question(person, revision, text=f"补充 {kind}")
            answer = {"action": "answer", "question_id": question["question_id"],
                      "answer": {"kind": kind, "text": "我先听你说。", "context_period": "大学"}}
            self.error(self.request("question.record", answer, other, other_revision), "NOT_FOUND")
            request = self.request("question.record", answer, person, revision)
            if kind == "recollection":
                with patch.object(persona_cli.storage, "save_result", side_effect=sqlite3.OperationalError):
                    self.error(request, "STORAGE_FAILED")
                data, actual_revision = self.current(person, child=True)
                self.assertEqual(actual_revision, revision)
                self.assertEqual(data["sources"], [])
                self.assertEqual(data["questions"][0]["status"], "pending")
            code, result = self.cli(request, child=True)
            self.assertEqual(code, 0, result)
            self.assertEqual(self.cli(request), (code, result))
            revision = result["revision"]
            self.error(self.request("question.record", answer, person, revision), "QUESTION_CLOSED")
            source_ids[kind] = result["data"]["answer_source_id"]
            sources = self.current(person)[0]["sources"]
            source = next(source for source in sources if source["source_id"] == source_ids[kind])
            self.assertEqual((source["kind"], source["status"], source["context_period"]), (kind, "draft", "大学"))
            data, revision = self.ok("source.confirm", {
                "source_id": source_ids[kind], "target_speaker": "回忆或设定中的阿林",
                "corrected_segments": [{"locator": "text:1", "speaker": "回忆或设定中的阿林",
                                        "text": "我先听你说。"}],
            }, person, revision)
            evidence_ids[kind] = data["evidence_ids"]
        for kind in ("hypothetical", "style_preference"):
            self.error(self.request("profile.propose", {
                "claims": [self.claim(evidence_ids[kind], category="fact")], "unknowns": [],
            }, person, revision), "OUTPUT_INVALID")
        proposal, revision = self.propose(person, revision, [
            self.claim(evidence_ids["recollection"], origin="recalled"),
            self.claim(evidence_ids["hypothetical"], category="situational", evidence_level="tentative"),
            self.claim(evidence_ids["style_preference"], origin="user_defined"),
        ])
        _, revision = self.accept(person, revision, proposal)
        data, _ = self.current(person, child=True)
        self.assertEqual({row["answer_source_id"] for row in data["questions"]}, set(source_ids.values()))
        self.assertEqual([claim["origin"] for claim in data["profile"]["claims"]],
                         ["recalled", "inferred", "user_defined"])
        self.assertEqual(self.current(other)[0]["questions"], [])
        deletion = self.delete_request(person, revision)
        self.assertEqual(self.cli(deletion, child=True)[1]["data"]["status"], "deleted")
        self.assertEqual(self.query("SELECT count(*) FROM questions WHERE persona_id = ?", (person,)), [(0,)])
        self.assertEqual(self.query("PRAGMA foreign_key_check"), [])

    def test_conflict_scope_counterexample_and_revision(self):
        person, revision = self.create()
        _, brief, revision = self.import_confirm(person, revision, event="工作", text="晚点说。")
        _, talkative, revision = self.import_confirm(person, revision, event="私下", text="给你讲个故事……")
        claim = self.claim(brief[:1], category="trait", statement="冷漠",
                           contradicting_evidence_ids=talkative[:1])
        self.error(self.request("profile.propose", {"claims": [claim], "unknowns": []}, person, revision),
                   "OUTPUT_INVALID")
        proposal, revision = self.propose(person, revision, [{**claim, "evidence_level": "tentative"}])
        target = proposal["diff"]["after"]["claims"][0]["claim_id"]
        other, other_revision = self.create("其他")
        self.error(self.request("question.record", {
            "action": "create", "purpose": "counterexample", "target_claim_ids": [target], "text": "有例外吗？",
        }, other, other_revision), "NOT_FOUND")
        question, revision = self.ok("question.record", {
            "action": "create", "purpose": "conflict", "target_claim_ids": [target],
            "text": "工作简短和私下多聊，分别发生在什么情境？",
        }, person, revision)
        self.assertIsNone(self.current(person, child=True)[0]["pending_proposal"])
        self.error(self.request("profile.accept", {
            "proposal_id": proposal["proposal_id"], "reviewed_version": proposal["reviewed_version"],
            "fallback_accepted": False,
        }, person, revision), "PROPOSAL_STALE")
        _, revision = self.ok("question.record", {"action": "skip", "question_id": question["question_id"]},
                              person, revision)
        proposal, revision = self.propose(person, revision, [
            self.claim(brief[:1], statement="工作时回复较短", scope={
                "relationship": "朋友", "period": "unknown", "context": "工作",
            }),
            self.claim(talkative[:1], statement="私下会展开话题", scope={
                "relationship": "朋友", "period": "unknown", "context": "私下",
            }),
        ])
        _, revision = self.accept(person, revision, proposal)
        data, _ = self.current(person, child=True)
        self.assertEqual([claim["scope"]["context"] for claim in data["profile"]["claims"]], ["工作", "私下"])

    def test_correction_review_and_immediate_rejection_survive_restart(self):
        person, revision = self.create()
        source, evidence, revision = self.import_confirm(person, revision)
        initial, revision = self.propose(person, revision, [
            self.claim(evidence[:1]), self.claim([], statement="称呼小周", origin="user_defined"),
        ])
        before, revision = self.accept(person, revision, initial)
        first, second = initial["diff"]["after"]["claims"]
        correction = {"claim_id": first["claim_id"], "action": "replace", "reason": "原结论范围过宽",
                      "replacement": self.claim(evidence[:1], statement="这次谈话先听对方说")}
        other, other_revision = self.create("其他")
        self.error(self.request("profile.correct", correction, other, other_revision), "NOT_FOUND")
        proposed, revision = self.ok("profile.correct", correction, person, revision)
        pending, _ = self.current(person, child=True)
        self.assertEqual(pending["pending_proposal"], proposed)
        self.assertEqual(pending["applied_corrections"], [])
        self.assertEqual(pending["profile"]["profile_version_id"], before["profile_version_id"])
        after, revision = self.accept(person, revision, proposed)
        current, _ = self.current(person, child=True)
        self.assertEqual(current["profile"]["profile_version_id"], after["profile_version_id"])
        replaced, unchanged = current["profile"]["claims"]
        self.assertEqual(replaced["statement"], "这次谈话先听对方说")
        self.assertEqual(unchanged["claim_id"], second["claim_id"])
        self.assertEqual(self.query("SELECT supersedes_id FROM claims WHERE id = ?", (replaced["claim_id"],)),
                         [(first["claim_id"],)])

        for claim_id in (replaced["claim_id"], second["claim_id"]):
            request = self.request("profile.correct", {
                "claim_id": claim_id, "action": "reject", "reason": "用户明确要求删除这条结论",
                "apply_immediately": True,
            }, person, revision)
            code, result = self.cli(request, child=True)
            self.assertEqual(code, 0, result)
            self.assertEqual(self.cli(request), (code, result))
            revision = result["revision"]
        _, revision = self.ok("source.withdraw", {"source_id": source}, person, revision)
        final, _ = self.current(person, child=True)
        self.assertEqual(final["profile"]["claims"], [])
        self.assertFalse(final["profile"]["fallback_accepted"])
        self.assertEqual(final["status"], "draft")
        self.assertEqual([item["action"] for item in final["applied_corrections"]], ["replace", "reject", "reject"])
        self.assertEqual(final["applied_corrections"][0]["previous_claim"]["statement"], first["statement"])
        self.assertEqual(self.query("SELECT review_status FROM claims WHERE id = ?", (replaced["claim_id"],)),
                         [("rejected",)])

    def test_concurrent_corrections_and_stale_review(self):
        person, revision = self.create()
        _, evidence, revision = self.import_confirm(person, revision)
        proposal, revision = self.propose(person, revision, [self.claim(evidence[:1])])
        before, revision = self.accept(person, revision, proposal)
        claim_id = proposal["diff"]["after"]["claims"][0]["claim_id"]
        requests = [self.request("profile.correct", {
            "claim_id": claim_id, "action": "reject", "reason": reason,
        }, person, revision) for reason in ("改动甲", "改动乙")]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.cli, requests))
        self.assertEqual(sorted(code for code, _ in results), [0, 1])
        failed = next(result for code, result in results if code)
        self.assertEqual(failed["error"]["code"], "REVISION_CONFLICT")
        success = next(result for code, result in results if not code)
        _, revision = self.question(person, success["revision"])
        self.error(self.request("profile.accept", {
            "proposal_id": success["data"]["proposal_id"], "reviewed_version": success["revision"],
            "fallback_accepted": False,
        }, person, revision), "PROPOSAL_STALE", child=True)
        self.assertEqual(self.current(person, child=True)[0]["profile"]["profile_version_id"],
                         before["profile_version_id"])

    def test_migration_preserves_m1_data_and_is_atomic(self):
        self.root.mkdir()
        person, profile_id, proposal_id = (str(uuid4()) for _ in range(3))
        with closing(sqlite3.connect(self.root / "personas.sqlite3")) as db, db:
            db.executescript((SCRIPTS / "migrations" / "001_initial.sql").read_text(encoding="utf-8"))
            db.execute("PRAGMA user_version = 1")
            db.execute("INSERT INTO personas VALUES "
                       "(?,'旧人物','朋友','大学','{}','ready',NULL,1,0,6,'2026-11-15')", (person,))
            snapshot = {"claims": [], "unknowns": ["旧未知"], "fallback_accepted": True, "fallback_style": "尊重"}
            db.execute("INSERT INTO profile_versions VALUES (?,?,1,?,'{}','2026-11-15')",
                       (person, profile_id, json.dumps(snapshot)))
            db.execute("UPDATE personas SET active_version_id = ? WHERE id = ?", (profile_id, person))
            db.execute("INSERT INTO profile_proposals VALUES (?,?,6,'[]','[]','pending')", (person, proposal_id))
        with patch.object(persona_cli.storage, "MIGRATIONS",
                          ("001_initial.sql", "002_persona_construction.sql", "missing.sql")):
            self.error(self.request("persona.list"), "STORAGE_FAILED")
        self.assertEqual(self.query("PRAGMA user_version"), [(1,)])
        self.assertFalse(any(row[1] == "correction_json" for row in self.query("PRAGMA table_info(profile_proposals)")))
        restored, revision = self.current(person, child=True)
        self.assertEqual(restored["profile"]["profile_version_id"], profile_id)
        self.assertEqual(restored["profile"]["unknowns"], ["旧未知"])
        self.assertEqual(restored["pending_proposal"]["proposal_id"], proposal_id)
        self.assertEqual(self.query("PRAGMA user_version"), [(persona_cli.storage.SCHEMA_VERSION,)])
        self.assertEqual(self.query("SELECT snapshot_json FROM profile_versions"), [(json.dumps(snapshot),)])
        accepted, _ = self.accept(person, revision, restored["pending_proposal"], fallback=True)
        self.assertEqual(accepted["version_no"], 2)
        self.assertEqual(self.query("PRAGMA foreign_key_check"), [])
