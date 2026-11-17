"""本地 JSON 协议的字段、错误及规范化编码。"""

import hashlib
import json
import re
from typing import Any
from uuid import UUID, uuid4

SOURCE_KINDS = ("chat_image", "chat_text", "recollection", "hypothetical", "style_preference")
CATEGORIES = ("style", "situational", "relational", "trait", "fact")
LEVELS = ("supported", "tentative", "unknown")
ORIGINS = ("inferred", "recalled", "user_defined")
READ_OPERATIONS = {"persona.get", "persona.list", "chat.prepare", "chat.validate",
                   "memory.list", "development.list"}
TURN_FIELDS = "session_id source_turn_id profile_version_id turn_token"
PAYLOAD_FIELDS = {
    "persona.create": ("display_name relationship target_period address_terms", "display_name"),
    "persona.update": ("relationship target_period", ""),
    "persona.list": ("", ""),
    "persona.get": ("include_profile", ""),
    "source.import": ("kind input_path text context_period", "kind"),
    "source.confirm": ("source_id corrected_segments target_speaker",
                       "source_id corrected_segments target_speaker"),
    "profile.propose": ("claims unknowns", "claims unknowns"),
    "profile.accept": ("proposal_id reviewed_version fallback_accepted",
                       "proposal_id reviewed_version fallback_accepted"),
    "profile.correct": ("claim_id action replacement reason apply_immediately", "claim_id action reason"),
    "question.record": ("action question_id purpose target_claim_ids text answer", "action"),
    "chat.open": ("", ""),
    "chat.close": ("session_id", "session_id"),
    "chat.prepare": ("session_id user_message context period relationship", "session_id user_message"),
    "chat.validate": (TURN_FIELDS, TURN_FIELDS),
    "memory.record": (TURN_FIELDS + " kind text", TURN_FIELDS + " kind text"),
    "memory.list": ("before_id limit", ""),
    "development.list": ("", ""),
    "development.propose": ("patch memory_ids", "patch memory_ids"),
    "development.accept": ("proposal_id reviewed_version", "proposal_id reviewed_version"),
    "development.reject": ("proposal_id reviewed_version", "proposal_id reviewed_version"),
    "settings.update": ("memory_enabled development_enabled", "memory_enabled development_enabled"),
    "source.withdraw": ("source_id", "source_id"),
    "persona.delete.preview": ("", ""),
    "persona.delete.commit": ("confirmation_token", "confirmation_token"),
}


class ContractError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False, **details: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details

    def result(self, request_id: str | None) -> dict:
        return {"ok": False, "request_id": request_id, "error": {
            "code": self.code, "message": self.message,
            "retryable": self.retryable, "details": self.details,
        }}


def invalid(field: str) -> ContractError:
    return ContractError("INVALID_INPUT", "输入字段或格式无效。", field=field)


def missing() -> ContractError:
    return ContractError("NOT_FOUND", "当前人物下无此可用记录。")


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def new_id() -> str:
    return str(uuid4())


def identifier(value: Any, field: str) -> str:
    if isinstance(value, str):
        try:
            if str(UUID(value)) == value:
                return value
        except ValueError:
            pass
    raise invalid(field)


def string(value: Any, field: str, limit: int = 2000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\0" in value:
        raise invalid(field)
    return value


def scope_label(value: str) -> str:
    return value.strip().casefold()


def choice(value: Any, field: str, options: tuple[str, ...]) -> str:
    if value not in options:
        raise invalid(field)
    return value


def boolean(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise invalid(field)
    return value


def positive_integer(value: Any, field: str) -> int:
    if type(value) is not int or not 1 <= value <= 2**63 - 1:
        raise invalid(field)
    return value


def array(value: Any, field: str, maximum: int = 2000) -> list:
    if not isinstance(value, list) or len(value) > maximum:
        raise invalid(field)
    return value


def fields(value: Any, allowed: str, required: str, field: str) -> dict:
    if (not isinstance(value, dict) or set(value) - set(allowed.split())
            or set(required.split()) - set(value)):
        raise invalid(field)
    return value


def decode_request(raw: bytes) -> Any:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict:
        result = dict(pairs)
        if len(result) != len(pairs):
            raise invalid("request")
        return result

    def reject_constant(value: str) -> None:
        raise invalid("request")

    try:
        request = json.loads(raw.decode("utf-8"), object_pairs_hook=object_pairs,
                             parse_constant=reject_constant)
        # 也拒绝 JSON 转义产生的孤立代理项和无法规范编码的数值。
        encode(request).encode("utf-8")
    except (ValueError, RecursionError) as error:
        raise invalid("request") from error
    return request


def request_fields(value: Any) -> dict:
    request = fields(value, "schema_version request_id operation persona_id expected_revision payload",
                     "schema_version request_id operation payload", "request")
    identifier(request["request_id"], "request_id")
    if positive_integer(request["schema_version"], "schema_version") != 1:
        raise invalid("schema_version")
    operation = string(request["operation"], "operation", 80)
    if operation not in PAYLOAD_FIELDS:
        raise invalid("operation")
    if operation in {"persona.create", "persona.list"}:
        if "persona_id" in request or "expected_revision" in request:
            raise invalid("request")
    else:
        identifier(request.get("persona_id"), "persona_id")
        if operation in READ_OPERATIONS or operation == "memory.record":
            if "expected_revision" in request:
                raise invalid("expected_revision")
        else:
            positive_integer(request.get("expected_revision"), "expected_revision")
    fields(request["payload"], *PAYLOAD_FIELDS[operation], "payload")
    return request


def confirmation_token(value: Any, field: str = "confirmation_token") -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise invalid(field)
    return value

