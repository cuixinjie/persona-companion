"""唯一入口：读一份 UTF-8 请求，按契约输出一份 JSON。"""

import argparse
import sqlite3
import sys
from pathlib import Path

import dialogue
import materials
import memory
import profiles
import questions
import storage
from contracts import (READ_OPERATIONS, ContractError, confirmation_token, decode_request,
                       digest, encode, identifier, invalid, request_fields)
from validation import checked_path, data_root

REQUEST_LIMIT = 16 * 1024**2
QUIET_WRITES = {"chat.open", "chat.close", "memory.record"}


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ContractError("INVALID_CONFIG", "须显式提供 --data-dir 和 --request-file。")


def read_request(value: str) -> dict:
    path = checked_path(Path(value))
    if not path.is_file():
        raise invalid("request_file")
    try:
        with path.open("rb") as file:
            raw = file.read(REQUEST_LIMIT + 1)
    except OSError as error:
        raise invalid("request_file") from error
    if len(raw) > REQUEST_LIMIT:
        raise invalid("request_file")
    return decode_request(raw)


def apply_write(db: sqlite3.Connection, root: Path, persona: sqlite3.Row,
                operation: str, payload: dict) -> tuple[dict, str]:
    persona_id = persona["id"]
    if operation == "persona.update":
        return profiles.update_persona(db, persona, payload), persona_id
    if operation == "source.import":
        data = materials.import_source(db, root, persona_id, payload)
        return data, data["source_id"]
    if operation == "source.confirm":
        data = materials.confirm_source(db, persona_id, payload)
        return data, data["source_id"]
    if operation == "source.withdraw":
        data = profiles.withdraw_source(db, persona, payload)
        return data, data["source_id"]
    if operation == "profile.propose":
        data = profiles.propose_profile(db, persona, payload)
        return data, data["proposal_id"]
    if operation == "profile.accept":
        data = profiles.accept_profile(db, persona, payload)
        return data, data["profile_version_id"]
    if operation == "profile.correct":
        data = profiles.correct_profile(db, persona, payload)
        return data, data["proposal_id"]
    if operation == "question.record":
        data = questions.record_question(db, root, persona, payload)
        return data, data.get("question_id", persona_id)
    if operation == "settings.update":
        return memory.update_settings(db, persona, payload), persona_id
    if operation == "development.propose":
        data = memory.propose(db, persona, payload)
        return data, data["proposal_id"]
    if operation in ("development.accept", "development.reject"):
        data = memory.resolve_proposal(db, persona, payload, accept=operation == "development.accept")
        return data, data["proposal_id"]
    raise RuntimeError("Unregistered write operation")


def apply_read(db: sqlite3.Connection, persona: sqlite3.Row, request: dict) -> dict:
    operation, payload = request["operation"], request["payload"]
    if operation == "persona.get":
        return profiles.get_profile(db, persona, payload)
    if operation == "chat.prepare":
        return dialogue.prepare(db, persona, request)
    if operation == "chat.validate":
        return dialogue.validate_turn(db, persona, payload)
    if operation == "memory.list":
        return memory.list_memories(db, persona, payload)
    if operation == "development.list":
        return memory.list_development(db, persona)
    raise RuntimeError("Unregistered read operation")


def apply_quiet_write(db: sqlite3.Connection, persona: sqlite3.Row, request: dict) -> dict:
    operation = request["operation"]
    if operation == "memory.record":
        return memory.record(db, persona, request["payload"])
    storage.check_revision(persona, request["expected_revision"])
    if operation == "chat.open":
        return dialogue.open_session(db, persona)
    return dialogue.close_session(db, persona, request["payload"])


def execute(root: Path, request: dict) -> dict:
    payload = request["payload"]
    operation = request["operation"]
    with storage.connect(root) as db:
        if operation in READ_OPERATIONS:
            with storage.transaction(db, write=False):
                if operation == "persona.list":
                    return storage.success(request, profiles.list_personas(db), None)
                persona = storage.get_persona(db, request["persona_id"])
                return storage.success(request, apply_read(db, persona, request), persona["revision"])

        payload_hash = digest(encode(request).encode("utf-8"))
        if operation == "persona.delete.commit":
            token = confirmation_token(payload["confirmation_token"])
            return profiles.delete_persona(db, root, request, payload_hash, token)

        with storage.transaction(db):
            previous = storage.replay(db, request, payload_hash)
            if previous is not None:
                return previous
            pending = storage.pending_request(db, request["request_id"])
            if pending is not None:
                storage.check_hash(pending["payload_hash"], payload_hash)
            if operation == "persona.create":
                data = profiles.create_persona(db, payload)
                persona_id, revision = data["persona_id"], data["revision"]
            else:
                persona_id = request["persona_id"]
                persona = storage.get_persona(db, persona_id)
                if operation in QUIET_WRITES:
                    data = apply_quiet_write(db, persona, request)
                    revision = persona["revision"]
                else:
                    storage.check_revision(persona, request["expected_revision"])
                    if operation == "persona.delete.preview":
                        data = profiles.preview_delete(db, persona)
                        revision = persona["revision"]
                    else:
                        data, object_id = apply_write(db, root, persona, operation, payload)
                        revision = storage.advance_revision(db, persona, operation, object_id)
                        profiles.expire_proposals(db, persona_id, revision)
                        memory.expire_proposals(db, persona_id, revision)
            result = storage.success(request, data, revision)
            storage.save_result(db, request, persona_id, payload_hash, result)
            return result


def run(arguments: list[str] | None = None) -> tuple[int, dict]:
    request_id = None
    try:
        parser = Parser(allow_abbrev=False, description="执行本地人物数据操作")
        parser.add_argument("--data-dir", required=True)
        parser.add_argument("--request-file", required=True)
        args = parser.parse_args(arguments)
        raw = read_request(args.request_file)
        if isinstance(raw, dict) and "request_id" in raw:
            request_id = identifier(raw["request_id"], "request_id")
        request = request_fields(raw)
        return 0, execute(data_root(args.data_dir), request)
    except ContractError as error:
        return (2 if error.code == "INVALID_CONFIG" else 1), error.result(request_id)
    except sqlite3.Error as error:
        code = getattr(error, "sqlite_errorcode", 0) & 0xFF
        if code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            failure = ContractError("STORAGE_BUSY", "本地存储正忙，请重试同一请求。", retryable=True)
        else:
            failure = ContractError("STORAGE_FAILED", "本地存储操作未完成，可重试同一请求。", retryable=True)
        return 1, failure.result(request_id)
    except OSError:
        failure = ContractError("STORAGE_FAILED", "本地文件操作未完成，可重试同一请求。", retryable=True)
        return 1, failure.result(request_id)


if __name__ == "__main__":
    code, result = run()
    sys.stdout.buffer.write((encode(result) + "\n").encode("utf-8"))
    raise SystemExit(code)

