"""M4 验证真实文件错误、延迟轮次和安装包边界。"""

import errno
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from test_m1 import M1Fixture, PNG

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_package
import dialogue
import materials


class M4FlowTests(M1Fixture):
    def test_multi_event_profile_keeps_context_and_sources_after_restart(self):
        persona, revision = self.create(child=True)
        records = [
            ("第一次复习", "复习", "先做会的，再圈出错题。"),
            ("第二次复习", "复习", "把错题留到最后一起看。"),
            ("周末出门", "出门", "晚一点细聊。"),
            ("考试之后", "受挫", "缓一会儿，我先听。"),
        ]
        claims = []
        for event, context, text in records:
            _, evidence, revision = self.import_confirm(persona, revision, event=event, text=text)
            claims.append(self.claim(evidence[:1], category="situational", statement=text, scope={
                "relationship": "unknown", "period": "unknown", "context": context,
            }))
        proposal, revision = self.propose(persona, revision, claims)
        self.assertIsNone(self.current(persona, child=True)[0]["profile"])
        version, revision = self.accept(persona, revision, proposal)
        active, _ = self.current(persona, child=True)
        self.assertEqual(len(active["profile"]["claims"]), len(records))
        session, _ = self.ok("chat.open", {}, persona, revision, child=True)
        payload = {**session, "context": "复习", "user_message": "我卡在一道错题上。"}
        first, _ = self.ok("chat.prepare", payload, persona, child=True)
        self.ok("chat.close", session, persona, revision, child=True)
        new_session, _ = self.ok("chat.open", {}, persona, revision, child=True)
        resumed, _ = self.ok("chat.prepare", {**payload, **new_session}, persona, child=True)
        self.assertEqual(resumed["profile_version_id"], version["profile_version_id"])
        self.assertEqual(resumed["context_sections"], first["context_sections"])
        self.assertCountEqual(
            [(item["kind"], item["text"]) for item in resumed["context_sections"]["source_excerpts_untrusted"]],
            [("chat_text", text) for _, context, text in records if context == "复习"],
        )


class M4FaultTests(M1Fixture):
    def test_attachment_disk_full_leaves_old_profile_and_supports_retry(self):
        persona, revision = self.create()
        first, evidence, revision = self.import_confirm(persona, revision)
        proposal, revision = self.propose(persona, revision, [self.claim(evidence[:1])])
        version, revision = self.accept(persona, revision, proposal)
        self.inbox.mkdir(parents=True, exist_ok=True)
        image = self.inbox / "synthetic.png"
        image.write_bytes(PNG)
        request = self.request("source.import", {"kind": "chat_image", "input_path": str(image)}, persona, revision)

        with patch.object(materials.os, "fsync", side_effect=OSError(errno.ENOSPC, "synthetic disk full")):
            self.assertTrue(self.error(request, "STORAGE_FAILED")["retryable"])
        loaded, current_revision = self.current(persona, child=True)
        self.assertEqual(revision, current_revision)
        self.assertEqual(loaded["profile"]["profile_version_id"], version["profile_version_id"])
        self.assertEqual([item["source_id"] for item in loaded["sources"]], [first])
        self.assertEqual(list((self.root / "attachments" / persona).iterdir()), [])
        self.assertEqual(len(list((self.root / "tmp").glob("*.part"))), 1)

        code, accepted = self.cli(request, child=True)
        self.assertEqual(code, 0, accepted)
        self.assertEqual(self.cli(request, child=True), (0, accepted))
        self.assertEqual(len(list((self.root / "attachments" / persona).iterdir())), 1)
        self.assertEqual(list((self.root / "tmp").glob("*.part")), [])

    def test_late_model_reply_cannot_write_memory_or_validate(self):
        persona, revision = self.create()
        proposal, revision = self.propose(persona, revision, [])
        version, revision = self.accept(persona, revision, proposal, fallback=True)
        session, _ = self.ok("chat.open", {}, persona, revision)
        prepare = {"session_id": session["session_id"], "user_message": "模拟中的暂停提示为纸船。"}
        # 在生产 CLI 生成一张过去有效的凭证，重启后的正常时钟负责拒绝。
        with patch.object(dialogue.time, "time", return_value=1_000_000):
            turn, _ = self.ok("chat.prepare", prepare, persona)
        binding = {key: turn[key] for key in ("session_id", "source_turn_id", "profile_version_id", "turn_token")}
        self.error(self.request("chat.validate", binding, persona), "TURN_STALE", child=True)
        self.error(self.request("memory.record", {**binding, "kind": "agreement", "text": "模拟中约定纸船。"}, persona),
                   "TURN_STALE", child=True)
        self.assertEqual(self.query("SELECT COUNT(*) FROM memories"), [(0,)])
        loaded, current_revision = self.current(persona, child=True)
        self.assertEqual(revision, current_revision)
        self.assertEqual(loaded["profile"]["profile_version_id"], version["profile_version_id"])
        fresh, _ = self.ok("chat.prepare", prepare, persona, child=True)
        self.ok("chat.validate", {key: fresh[key] for key in binding}, persona, child=True)


class M4PackageTests(M1Fixture):
    def test_package_reproducible_and_runs_outside_source(self):
        package = self.workspace / "persona-companion-m4.zip"
        first = build_package.build(package)
        self.assertEqual(first, build_package.build(self.workspace / "rebuild.zip"))
        extracted = self.workspace / "extracted"
        with ZipFile(package) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(set(archive.namelist()), build_package.PACKAGE_FILES.keys())
            self.assertFalse(any(part.startswith(".") for name in archive.namelist() for part in name.split("/")))
            archive.extractall(extracted)
        self.assertTrue((extracted / "persona-companion/SKILL.md").is_file())
        process = subprocess.run([
            sys.executable, "-E", "-B", str(extracted / "persona-companion/scripts/persona_cli.py"),
            "--data-dir", str(self.root), "--request-file", str(extracted / "examples/create_persona.json"),
        ], cwd=self.workspace, capture_output=True, timeout=15)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertTrue(json.loads(process.stdout)["ok"])
        self.assertEqual(self.query("PRAGMA user_version"), [(4,)])

    def test_allowlist_rejects_private_fixture_and_hardlinks(self):
        path = self.workspace / "example.md"
        path.write_text("C:/Users/synthetic-account/private/chat.txt", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "私有路径"):
            build_package.read_entry(self.workspace, path.name)
        path.write_text("合成示例", encoding="utf-8")
        try:
            os.link(path, self.workspace / "link.md")
        except OSError:
            self.skipTest("测试文件系统不允许硬链接")
        with self.assertRaisesRegex(ValueError, "独立文件"):
            build_package.read_entry(self.workspace, path.name)


if __name__ == "__main__":
    unittest.main()
