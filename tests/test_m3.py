"""M3 合成资料端到端、并发与兼容性回归。"""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from test_m1 import M1Fixture, SCRIPTS


class M3Tests(M1Fixture):
    @staticmethod
    def turn(prepared):
        return {key: prepared[key] for key in (
            "session_id", "source_turn_id", "profile_version_id", "turn_token",
        )}

    def prepare(self, persona, session, *, child=False, context="聊天"):
        result, _ = self.ok("chat.prepare", {
            "session_id": session["session_id"], "context": context,
            "user_message": "这是一条仅供当前对话使用的合成消息。",
        }, persona, child=child)
        return result

    def ready(self, *, image=False):
        persona, rev = self.create()
        if image:
            content = (Path(__file__).parent / "fixtures" / "m2-clear.png").read_bytes()
            source, rev = self.import_image(persona, rev, content=content)
            evidence, rev = self.ok("source.confirm", {
                "source_id": source["source_id"], "target_speaker": "阿林", "corrected_segments": [
                    {"locator": "image:1:1", "speaker": "小周", "text": "Still at work?"},
                    {"locator": "image:1:2", "speaker": "阿林",
                     "text": "In a meeting. Talk later.", "context": "聊天"},
                ],
            }, persona, rev)
            evidence_id = evidence["evidence_ids"][1]
            source_id = source["source_id"]
        else:
            source_id, evidence, rev = self.import_confirm(persona, rev)
            evidence_id = evidence[0]
        claim = self.claim([evidence_id], scope={"relationship": "unknown", "period": "unknown",
                                                "context": "聊天"})
        proposal, rev = self.propose(persona, rev, [claim])
        _, rev = self.accept(persona, rev, proposal, fallback=True)
        return persona, source_id, proposal["diff"]["after"]["claims"][0], rev

    def settings(self, persona, rev, *, memory=True, development=False):
        return self.ok("settings.update", {"memory_enabled": memory, "development_enabled": development},
                       persona, rev)

    def record(self, persona, prepared, text="用户希望先把话说完。"):
        return self.ok("memory.record", {**self.turn(prepared), "kind": "shared_exchange", "text": text}, persona)

    def proposal(self, memory_id, behavior="模拟交流时先邀请用户讲完。"):
        return {"patch": {"category": "interaction_habit", "context": "聊天", "behavior": behavior},
                "memory_ids": [memory_id]}

    def test_confirmed_text_and_image_to_chat_and_restart(self):
        for image in (False, True):
            with self.subTest(image=image):
                persona, _, claim, rev = self.ready(image=image)
                session, _ = self.ok("chat.open", {}, persona, rev, child=True)
                prepared = self.prepare(persona, session, child=True)
                sections = prepared["context_sections"]
                self.assertEqual(sections["persona_version"]["claims"], [claim])
                self.assertEqual([row["kind"] for row in sections["source_excerpts_untrusted"]],
                                 ["chat_image" if image else "chat_text"])
                self.assertEqual(sections["source_excerpts_untrusted"][0]["locator"],
                                 "image:1:2" if image else "text:1")
                self.assertNotIn("original_path", json.dumps(sections))
                self.assertEqual(self.prepare(persona, session, context="工作")["context_sections"]
                                 ["persona_version"]["claims"], [])
                saved, same_rev = self.record(persona, prepared)
                self.assertEqual(rev, same_rev)
                self.ok("chat.validate", self.turn(prepared), persona, child=True)
                reopened, _ = self.ok("chat.open", {}, persona, rev, child=True)
                resumed = self.prepare(persona, reopened, child=True)
                self.assertEqual(resumed["profile_version_id"], prepared["profile_version_id"])
                self.assertEqual(resumed["context_sections"]["exchange_memories_simulation"][0]["memory_id"],
                                 saved["memory_id"])
        raw = (self.root / "personas.sqlite3").read_bytes()
        self.assertNotIn("这是一条仅供当前对话使用的合成消息。".encode(), raw)

    def test_fallback_requires_acceptance(self):
        persona, rev = self.create()
        self.error(self.request("chat.open", {}, persona, rev), "PROFILE_NOT_READY")
        proposal, rev = self.propose(persona, rev, [])
        _, rev = self.accept(persona, rev, proposal, fallback=True)
        session, _ = self.ok("chat.open", {}, persona, rev)
        profile = self.prepare(persona, session)["context_sections"]["persona_version"]
        self.assertEqual(profile["claims"], [])
        self.assertTrue(profile["fallback_accepted"])
        self.assertTrue(profile["fallback_style"])

    def test_interview_stop_persists_and_blocks_rephrased_questions(self):
        persona, rev = self.create()
        _, rev = self.ok("question.record", {"action": "create", "purpose": "missing", "text": "他哪次安慰过你？"},
                         persona, rev)
        stopped, rev = self.ok("question.record", {"action": "stop"}, persona, rev, child=True)
        self.assertEqual(stopped["interview_status"], "closed")
        question = {"action": "create", "purpose": "missing", "text": "你对他怎么安慰你有印象吗？"}
        self.error(self.request("question.record", question, persona, rev), "INTERVIEW_CLOSED", child=True)
        self.error(self.request("chat.open", {}, persona, rev), "PROFILE_NOT_READY")
        proposal, rev = self.propose(persona, rev, [])
        _, rev = self.accept(persona, rev, proposal, fallback=True)
        current, _ = self.current(persona, child=True)
        self.assertEqual(current["interview_status"], "closed")
        self.assertEqual(current["questions"][0]["status"], "skipped")
        session, _ = self.ok("chat.open", {}, persona, rev, child=True)
        old_turn = self.prepare(persona, session, child=True)
        sections = old_turn["context_sections"]
        self.assertEqual(sections["interview_status"], "closed")
        self.assertIn("访谈已结束", " ".join(sections["system_rules"]))
        _, rev = self.ok("question.record", {"action": "resume"}, persona, rev, child=True)
        self.error(self.request("chat.validate", self.turn(old_turn), persona), "TURN_STALE")
        self.assertEqual(self.prepare(persona, session)["context_sections"]["interview_status"], "open")
        self.ok("question.record", question, persona, rev)

    def test_persona_scope_update_keeps_id_and_isolates_previous_scope(self):
        created, rev = self.ok("persona.create", {"display_name": "阿林", "relationship": "朋友", "target_period": "大学"})
        persona = created["persona_id"]
        _, evidence, rev = self.import_confirm(persona, rev, period="大学")
        claim = self.claim([evidence[0]], scope={"relationship": "朋友", "period": "大学", "context": "聊天"})
        proposal, rev = self.propose(persona, rev, [claim])
        _, rev = self.accept(persona, rev, proposal, fallback=True)
        session, _ = self.ok("chat.open", {}, persona, rev)
        old_turn = self.prepare(persona, session)
        saved, _ = self.record(persona, old_turn)
        pending, rev = self.propose(persona, rev, [claim])
        update = self.request("persona.update", {"relationship": "共创伙伴", "target_period": "毕业后"}, persona, rev)
        result = self.cli(update, child=True)
        self.assertEqual(result[0], 0, result)
        self.assertEqual(self.cli(update, child=True), result)
        self.error(self.request("persona.update", {"relationship": "同事"}, persona, rev), "REVISION_CONFLICT")
        current, rev = self.current(persona, child=True)
        self.assertEqual((current["persona_id"], current["relationship"], current["target_period"]),
                         (persona, "共创伙伴", "毕业后"))
        self.assertIsNone(current["pending_proposal"])
        self.assertEqual(current["profile"]["claims"][0]["scope"], claim["scope"])
        self.assertNotEqual(current["active_version_id"], old_turn["profile_version_id"])
        self.error(self.request("profile.accept", {"proposal_id": pending["proposal_id"],
                   "reviewed_version": pending["reviewed_version"], "fallback_accepted": True}, persona, rev),
                   "PROPOSAL_STALE")
        self.error(self.request("chat.validate", self.turn(old_turn), persona), "TURN_STALE")
        sections = self.prepare(persona, session, child=True)["context_sections"]
        self.assertEqual(sections["persona_version"]["period"], "毕业后")
        self.assertEqual(sections["persona_version"]["claims"], [])
        self.assertEqual(sections["exchange_memories_simulation"], [])
        memory, _ = self.ok("memory.list", {}, persona)
        self.assertEqual((memory["memories"][0]["memory_id"], memory["memories"][0]["status"]),
                         (saved["memory_id"], "archived"))
        self.ok("persona.update", {"relationship": "unknown"}, persona, rev)
        current, _ = self.current(persona, child=True)
        self.assertEqual((current["relationship"], current["target_period"]), ("unknown", "毕业后"))

    def test_chat_scope_uses_explicit_historical_period_if_unspecified(self):
        persona, _, claim, rev = self.ready()
        historical = self.claim(claim["evidence_ids"], scope={**claim["scope"], "period": "大学"})
        proposal, rev = self.propose(persona, rev, [historical])
        _, rev = self.accept(persona, rev, proposal)
        session, _ = self.ok("chat.open", {}, persona, rev)
        self.assertEqual(self.prepare(persona, session)["context_sections"]["persona_version"]["claims"], [])
        payload = {**session, "user_message": "还记得当时怎么聊天吗？", "context": "聊天", "period": "大学"}
        prepared, _ = self.ok("chat.prepare", payload, persona)
        self.assertEqual(prepared["context_sections"]["persona_version"]["claims"], proposal["diff"]["after"]["claims"])

    def test_mismatched_evidence_period_rejected_and_legacy_claim_quarantined(self):
        persona, rev = self.create()
        _, early, rev = self.import_confirm(persona, rev, period="大学", text="在课室等我。")
        _, late, rev = self.import_confirm(persona, rev, period="工作后", text="下班后到楼下等我。")
        scope = {"relationship": "unknown", "period": "大学", "context": "聊天"}
        correct = self.claim([early[0]], scope=scope)
        wrong = self.claim([late[0]], scope=scope, statement="在楼下碰面")
        request = self.request("profile.propose", {"claims": [correct, wrong], "unknowns": []}, persona, rev)
        error = self.error(request, "OUTPUT_INVALID", child=True)
        self.assertEqual(error["details"], {"field": "scope.period", "claim_period": "大学", "evidence_periods": ["工作后"]})
        self.assertEqual(self.current(persona)[1], rev)

        # 用升级前允许的路径生成已有活动版和待审案，随后恢复新版校验。
        with patch("profiles.conflicting_periods", return_value=[]):
            proposal, rev = self.propose(persona, rev, [wrong])
            _, rev = self.accept(persona, rev, proposal, fallback=True)
            pending, rev = self.propose(persona, rev, [wrong])
        current, _ = self.current(persona, child=True)
        self.assertEqual(current["proposal_needing_review_id"], pending["proposal_id"])
        self.assertEqual(current["period_conflict_claim_ids"], [current["profile"]["claims"][0]["claim_id"]])
        self.error(self.request("profile.accept", {"proposal_id": pending["proposal_id"],
                   "reviewed_version": rev, "fallback_accepted": True}, persona, rev), "PROPOSAL_STALE")
        session, _ = self.ok("chat.open", {}, persona, rev)
        payload = {**session, "user_message": "大学时在哪儿等？", "context": "聊天", "period": "大学"}
        sections = self.ok("chat.prepare", payload, persona, child=True)[0]["context_sections"]
        self.assertEqual(sections["persona_version"]["claims"], [])
        self.assertEqual(sections["source_excerpts_untrusted"], [])

        broad = {**wrong, "scope": {**scope, "period": "unknown"}}
        proposal, rev = self.propose(persona, rev, [correct, broad])
        self.accept(persona, rev, proposal, fallback=True)
        sections = self.ok("chat.prepare", payload, persona, child=True)[0]["context_sections"]
        self.assertEqual(sections["persona_version"]["claims"], [proposal["diff"]["after"]["claims"][0]])
        self.assertEqual([item["evidence_id"] for item in sections["source_excerpts_untrusted"]], [early[0]])

    def test_settings_matrix_and_latest_switch(self):
        persona, _, _, rev = self.ready()
        session, _ = self.ok("chat.open", {}, persona, rev)
        original, _ = self.record(persona, self.prepare(persona, session))
        proposal = self.proposal(original["memory_id"])
        self.error(self.request("development.propose", proposal, persona, rev), "DEVELOPMENT_DISABLED")
        _, rev = self.settings(persona, rev, development=True)
        candidate, rev = self.ok("development.propose", proposal, persona, rev)
        prepared = self.prepare(persona, session)
        self.error(self.request("settings.update", {"memory_enabled": False, "development_enabled": True},
                                persona, rev), "INVALID_SETTINGS")
        settings, rev = self.settings(persona, rev, memory=False)
        self.assertEqual(settings["stale_proposal_ids"], [candidate["proposal_id"]])
        self.assertEqual(self.record(persona, prepared)[0], {"status": "skipped_disabled"})
        self.assertEqual(self.prepare(persona, session)["context_sections"]["exchange_memories_simulation"][0]
                         ["memory_id"], original["memory_id"])
        self.error(self.request("development.accept", {
            "proposal_id": candidate["proposal_id"], "reviewed_version": candidate["reviewed_version"],
        }, persona, rev), "PROPOSAL_STALE")
        prepared_while_disabled = self.prepare(persona, session)
        self.settings(persona, rev)
        self.assertEqual(self.record(persona, prepared_while_disabled)[0], {"status": "skipped_disabled"})
        self.assertEqual(len(self.ok("memory.list", {}, persona)[0]["memories"]), 1)

    def test_development_is_separate_reviewed_and_rejection_is_persistent(self):
        persona, _, claim, rev = self.ready()
        session, _ = self.ok("chat.open", {}, persona, rev)
        prepared = self.prepare(persona, session)
        saved, _ = self.record(persona, prepared, "模拟交流中角色曾随口说喜欢画画。")
        memory_id = saved["memory_id"]
        self.error(self.request("source.import", {"kind": "shared_exchange", "text": "喜欢画画"}, persona, rev),
                   "INVALID_INPUT")
        self.error(self.request("profile.propose", {"claims": [self.claim([memory_id])], "unknowns": []},
                                persona, rev), "NOT_FOUND")
        _, rev = self.settings(persona, rev, development=True)
        candidate, rev = self.ok("development.propose", self.proposal(memory_id), persona, rev)
        review = {key: candidate[key] for key in ("proposal_id", "reviewed_version")}
        self.assertEqual(self.prepare(persona, session)["context_sections"]["digital_development_simulation"], [])
        _, rev = self.ok("development.reject", review, persona, rev)
        self.error(self.request("development.propose", self.proposal(memory_id, "让用户先讲。"), persona, rev),
                   "DEVELOPMENT_REJECTED", child=True)
        other, _ = self.record(persona, self.prepare(persona, session), "我们约定先听后问。")
        candidate, rev = self.ok("development.propose", self.proposal(other["memory_id"]), persona, rev)
        review = {key: candidate[key] for key in ("proposal_id", "reviewed_version")}
        accepted, rev = self.ok("development.accept", review, persona, rev)
        _, rev = self.settings(persona, rev)
        current = self.prepare(persona, session, child=True)
        self.assertEqual(current["profile_version_id"], accepted["profile_version_id"])
        self.assertEqual(current["context_sections"]["persona_version"]["claims"], [claim])
        self.assertEqual(current["context_sections"]["digital_development_simulation"],
                         [candidate["diff"]["after"]])
        self.error(self.request("chat.validate", self.turn(prepared), persona), "TURN_STALE")
        self.ok("profile.correct", {"claim_id": claim["claim_id"], "action": "reject",
                                    "reason": "这只是当时的情况", "apply_immediately": True}, persona, rev)
        corrected = self.prepare(persona, session, child=True)["context_sections"]
        self.assertEqual(corrected["digital_development_simulation"], [])
        self.assertEqual(corrected["exchange_memories_simulation"], [])
        self.assertEqual(corrected["persona_version"]["claims"], [])

    def test_correction_withdrawal_and_deletion_invalidate_chat(self):
        for operation in ("profile.correct", "source.withdraw"):
            with self.subTest(operation=operation):
                persona, source_id, claim, rev = self.ready()
                session, _ = self.ok("chat.open", {}, persona, rev)
                prepared = self.prepare(persona, session)
                saved, _ = self.record(persona, prepared, claim["statement"])
                payload = ({"claim_id": claim["claim_id"], "action": "reject", "reason": "过度归纳",
                            "apply_immediately": True} if operation == "profile.correct" else
                           {"source_id": source_id})
                _, rev = self.ok(operation, payload, persona, rev)
                self.error(self.request("chat.validate", self.turn(prepared), persona), "TURN_STALE", child=True)
                replacement = self.prepare(persona, session, child=True)
                self.assertNotIn(claim["statement"], json.dumps(replacement, ensure_ascii=False))
                self.assertEqual(replacement["context_sections"]["source_excerpts_untrusted"], [])
                memories, _ = self.ok("memory.list", {}, persona)
                self.assertEqual((memories["memories"][0]["memory_id"], memories["memories"][0]["status"]),
                                 (saved["memory_id"], "archived"))
                self.assertEqual(self.cli(self.delete_request(persona, rev))[0], 0)
                self.error(self.request("chat.validate", self.turn(replacement), persona), "NOT_FOUND")
                self.assertFalse(self.query("SELECT 1 FROM memories WHERE persona_id = ?", (persona,)))
                self.assertFalse(self.query("SELECT 1 FROM sessions WHERE persona_id = ?", (persona,)))

    def test_failed_development_commit_can_retry_without_partial_acceptance(self):
        persona, _, claim, rev = self.ready()
        session, _ = self.ok("chat.open", {}, persona, rev)
        prepared = self.prepare(persona, session)
        saved, _ = self.record(persona, prepared)
        _, rev = self.settings(persona, rev, development=True)
        candidate, rev = self.ok("development.propose", self.proposal(saved["memory_id"]), persona, rev)
        review = {key: candidate[key] for key in ("proposal_id", "reviewed_version")}
        request = self.request("development.accept", review, persona, rev)
        with closing(sqlite3.connect(self.root / "personas.sqlite3")) as db, db:
            db.execute("CREATE TRIGGER fail_version BEFORE INSERT ON profile_versions"
                       " BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
        self.error(request, "STORAGE_FAILED", child=True)
        self.assertEqual(self.ok("development.list", {}, persona)[0]["pending"], candidate)
        self.ok("chat.validate", self.turn(prepared), persona)
        with closing(sqlite3.connect(self.root / "personas.sqlite3")) as db, db:
            db.execute("DROP TRIGGER fail_version")
        self.assertEqual(self.cli(request, child=True)[0], 0)
        profile, _ = self.current(persona, child=True)
        self.assertEqual(profile["profile"]["claims"], [claim])
        self.assertEqual(profile["profile"]["development"], [candidate["diff"]["after"]])

    def test_switch_isolation_token_and_concurrent_record(self):
        persona, _, _, rev = self.ready()
        other, _, _, _ = self.ready()
        session, _ = self.ok("chat.open", {}, persona, rev)
        prepared = self.prepare(persona, session)
        binding = self.turn(prepared)
        self.error(self.request("chat.validate", binding, other), "NOT_FOUND")
        self.error(self.request("chat.validate", {**binding, "source_turn_id": str(uuid4())}, persona), "TURN_STALE")
        malformed = {**binding, "turn_token": binding["turn_token"].rsplit(".", 1)[0] + ".bad"}
        error = self.error(self.request("chat.validate", malformed, persona), "INVALID_INPUT")
        self.assertEqual(error["details"]["field"], "turn_token")
        payload = {**binding, "kind": "agreement", "text": "模拟交流时先听完。"}
        requests = [self.request("memory.record", payload, persona) for _ in range(2)]
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(self.cli, requests))
        self.assertEqual([code for code, _ in results], [0, 0])
        self.assertEqual(results[0][1]["data"], results[1][1]["data"])
        self.assertEqual(self.cli(requests[0]), results[0])
        self.error(self.request("memory.record", {**payload, "text": "更改"}, persona), "REQUEST_REUSED")
        self.assertEqual(len(self.query("SELECT id FROM memories")), 1)
        self.assertEqual(self.ok("memory.list", {}, other)[0]["memories"], [])
        self.ok("chat.close", session, persona, rev)
        self.error(self.request("chat.validate", binding, persona), "TURN_STALE")
        self.error(self.request("memory.record", payload, persona), "TURN_STALE")

    def test_upgrade_real_m2_snapshot(self):
        self.root.mkdir()
        persona = str(uuid4())
        version = str(uuid4())
        with closing(sqlite3.connect(self.root / "personas.sqlite3")) as db, db:
            for name in ("001_initial.sql", "002_persona_construction.sql"):
                db.executescript((SCRIPTS / "migrations" / name).read_text(encoding="utf-8"))
            db.execute("PRAGMA user_version = 2")
            db.execute("INSERT INTO personas (id,display_name,relationship,target_period,address_terms,status,"
                       "created_at) VALUES (?, '旧库', 'unknown', 'unknown', '{}', 'ready', '2026-01-01')",
                       (persona,))
            snapshot = {"claims": [], "unknowns": ["经历"], "fallback_accepted": True, "fallback_style": "简洁"}
            db.execute("INSERT INTO profile_versions VALUES (?, ?, 1, ?, '{}', '2026-01-01')",
                       (persona, version, json.dumps(snapshot)))
            db.execute("UPDATE personas SET active_version_id = ?", (version,))
        session, _ = self.ok("chat.open", {}, persona, 1, child=True)
        prepared = self.prepare(persona, session)
        self.assertEqual(prepared["profile_version_id"], version)
        self.assertEqual(prepared["context_sections"]["persona_version"]["fallback_style"], "简洁")
        self.assertEqual(self.query("PRAGMA user_version"), [(4,)])
        self.assertEqual(prepared["context_sections"]["interview_status"], "open")
        self.assertEqual(self.query("PRAGMA foreign_key_check"), [])
