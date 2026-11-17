"""材料导入、人工核对与私有附件生命周期。"""

import os
import re
import sqlite3
import stat
import zlib
from pathlib import Path

from contracts import (SOURCE_KINDS, ContractError, array, choice, digest, fields,
                       identifier, invalid, missing, new_id, string)
from validation import input_file, scoped_path, utc_now, utc_timestamp

TEXT_LIMIT = 2 * 1024**2
IMAGE_LIMIT = 10 * 1024**2
IMAGE_EXTENSIONS = {".png": ".png", ".jpg": ".jpg", ".jpeg": ".jpg", ".webp": ".webp"}
UUID_PATTERN = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
CONFIRMED_EVIDENCE_BY_ID = (
    " FROM evidence e JOIN sources s ON s.persona_id = e.persona_id AND s.id = e.source_id"
    " WHERE e.persona_id = ? AND e.id = ? AND s.status = 'confirmed'"
    " AND e.verification = 'user_confirmed' AND e.speaker = s.target_speaker"
)


def unreadable() -> ContractError:
    return ContractError("SOURCE_UNREADABLE", "材料无法读取、超过大小限制或格式不符。")


def png_valid(content: bytes) -> bool:
    if not content.startswith(b"\x89PNG\r\n\x1a\n"):
        return False
    position = 8
    has_image_data = False
    while position + 12 <= len(content):
        size = int.from_bytes(content[position:position + 4], "big")
        end = position + size + 12
        if end > len(content):
            return False
        chunk = content[position + 4:end - 4]
        if zlib.crc32(chunk) != int.from_bytes(content[end - 4:end], "big"):
            return False
        name = chunk[:4]
        if position == 8 and (name != b"IHDR" or size != 13
                              or not any(chunk[4:8]) or not any(chunk[8:12])):
            return False
        if name == b"IDAT" and size:
            has_image_data = True
        if name == b"IEND":
            return size == 0 and has_image_data and end == len(content)
        position = end
    return False


def jpeg_valid(content: bytes) -> bool:
    if not content.startswith(b"\xff\xd8") or not content.endswith(b"\xff\xd9"):
        return False
    position = 2
    has_frame = False
    while position + 4 < len(content) and content[position] == 0xff:
        while position < len(content) and content[position] == 0xff:
            position += 1
        if position + 3 > len(content):
            return False
        marker = content[position]
        size = int.from_bytes(content[position + 1:position + 3], "big")
        end = position + 1 + size
        if size < 2 or end >= len(content) - 2:
            return False
        if marker in {0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7,
                      0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf}:
            has_frame = size >= 8 and any(content[position + 4:position + 6]) and any(
                content[position + 6:position + 8]
            )
        if marker == 0xda:
            return has_frame and size >= 6
        position = end
    return False


def webp_valid(content: bytes) -> bool:
    if (content[:4] != b"RIFF" or content[8:12] != b"WEBP"
            or int.from_bytes(content[4:8], "little") != len(content) - 8):
        return False
    position = 12
    has_image_data = False
    while position + 8 <= len(content):
        kind = content[position:position + 4]
        size = int.from_bytes(content[position + 4:position + 8], "little")
        start = position + 8
        position = start + size + size % 2
        if position > len(content):
            return False
        if kind == b"VP8 " and size >= 10 and content[start + 3:start + 6] == b"\x9d\x01\x2a":
            has_image_data = True
        if kind == b"VP8L" and size >= 5 and content[start] == 0x2f:
            has_image_data = True
        if kind == b"ANMF" and size >= 24 and content[start + 16:start + 20] in (b"VP8 ", b"VP8L", b"ALPH"):
            has_image_data = True
    return position == len(content) and has_image_data


def import_content(root: Path, payload: dict, kind: str) -> tuple[bytes, str | None, str | None]:
    if ("input_path" in payload) == ("text" in payload):
        raise invalid("input_path/text")
    if "text" in payload:
        if kind == "chat_image":
            raise invalid("input_path")
        text = string(payload["text"], "text", TEXT_LIMIT)
        content = text.encode("utf-8")
        if len(content) > TEXT_LIMIT:
            raise unreadable()
        return content, text, None

    if kind not in ("chat_image", "chat_text"):
        raise invalid("text")
    path = input_file(root, string(payload["input_path"], "input_path", 4096))
    extension = path.suffix.lower()
    try:
        with path.open("rb") as file:
            content = file.read((IMAGE_LIMIT if kind == "chat_image" else TEXT_LIMIT) + 1)
    except OSError as error:
        raise unreadable() from error
    if kind == "chat_text":
        if extension != ".txt" or len(content) > TEXT_LIMIT:
            raise unreadable()
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise unreadable() from error
        return content, string(text, "text", TEXT_LIMIT), None
    extension = IMAGE_EXTENSIONS.get(extension)
    validators = {".png": png_valid, ".jpg": jpeg_valid, ".webp": webp_valid}
    if extension is None or len(content) > IMAGE_LIMIT or not validators[extension](content):
        raise unreadable()
    return content, None, extension


def owned_files(root: Path, relative_dir: Path, pattern: str, *, strict: bool = False) -> list[Path]:
    directory = scoped_path(root, relative_dir)
    if not directory.exists():
        return []
    result = []
    for file in directory.iterdir():
        if not re.fullmatch(pattern, file.name):
            if strict:
                raise ContractError("STORAGE_FAILED", "附件目录含有额外文件，删除仍待完成。",
                                    retryable=True)
            continue
        checked = scoped_path(root, relative_dir / file.name)
        if not stat.S_ISREG(checked.stat().st_mode):
            raise ContractError("STORAGE_FAILED", "附件路径不是普通文件。", retryable=True)
        result.append(checked)
    return result


def cleanup_files(db: sqlite3.Connection, root: Path, persona_id: str, *, deleting: bool = False) -> None:
    # 必须持有写事务，避免把另一个尚未提交的导入识别为孤儿。
    directory = Path("attachments") / persona_id
    active = {
        scoped_path(root, Path(row["original_path"]))
        for row in db.execute("SELECT original_path FROM sources WHERE persona_id = ? AND original_path IS NOT NULL",
                              (persona_id,))
    }
    files = owned_files(root, directory, UUID_PATTERN + r"\.(png|jpg|webp)", strict=deleting)
    staged = owned_files(root, Path("tmp"), persona_id + r"\." + UUID_PATTERN + r"\.part")
    for file in staged:
        file.unlink(missing_ok=True)
    for file in files:
        if deleting or file not in active:
            file.unlink(missing_ok=True)
    if deleting:
        attachment_dir = scoped_path(root, directory)
        if attachment_dir.exists():
            attachment_dir.rmdir()


def import_source(db: sqlite3.Connection, root: Path, persona_id: str, payload: dict) -> dict:
    kind = choice(payload["kind"], "kind", SOURCE_KINDS)
    period = string(payload.get("context_period", "unknown"), "context_period")
    content, text, extension = import_content(root, payload, kind)
    source_id = new_id()
    relative = None
    cleanup_files(db, root, persona_id)
    if extension:
        relative = Path("attachments") / persona_id / (source_id + extension)
        staged = scoped_path(root, Path("tmp") / f"{persona_id}.{source_id}.part")
        target = scoped_path(root, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        staged.parent.mkdir(parents=True, exist_ok=True)
        with staged.open("xb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        staged.replace(target)
    db.execute(
        "INSERT INTO sources VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'draft', ?)",
        (persona_id, source_id, kind, relative.as_posix() if relative else None,
         text, digest(content), period, utc_now()),
    )
    return {"source_id": source_id, "status": "draft"}


def get_source(db: sqlite3.Connection, persona_id: str, source_id: str) -> sqlite3.Row:
    row = db.execute("SELECT * FROM sources WHERE persona_id = ? AND id = ? AND status <> 'withdrawn'",
                     (persona_id, source_id)).fetchone()
    if row is None:
        raise missing()
    return row


def confirm_source(db: sqlite3.Connection, persona_id: str, payload: dict) -> dict:
    source_id = identifier(payload["source_id"], "source_id")
    source = get_source(db, persona_id, source_id)
    if source["status"] != "draft":
        raise ContractError("INVALID_INPUT", "仅可确认待核对材料。")
    target = string(payload["target_speaker"], "target_speaker", 120).strip()
    if target.casefold() in {"unknown", "未知", "不明", "?"}:
        raise ContractError("SPEAKER_UNKNOWN", "确认前须标记目标说话人。")
    pattern = r"image:[1-9][0-9]*:[1-9][0-9]*" if source["kind"] == "chat_image" else r"text:[1-9][0-9]*"
    segments = []
    locators = set()
    for segment in array(payload["corrected_segments"], "corrected_segments"):
        fields(segment, "locator text speaker context occurred_at event_key", "locator text speaker", "segment")
        locator = string(segment["locator"], "locator", 64)
        if not re.fullmatch(pattern, locator) or locator in locators:
            raise invalid("locator")
        locators.add(locator)
        segments.append((
            persona_id, new_id(), source_id, locator, string(segment["text"], "text", 16000),
            string(segment["speaker"], "speaker", 120).strip(),
            string(segment.get("context", "unknown"), "context"),
            utc_timestamp(segment.get("occurred_at")),
            event_key(segment),
        ))
    if not any(segment[5] == target for segment in segments):
        raise ContractError("SPEAKER_UNKNOWN", "至少标记一条属于目标说话人的已核对内容。")
    db.executemany("INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'user_confirmed')", segments)
    db.execute("UPDATE sources SET target_speaker = ?, status = 'confirmed' WHERE persona_id = ? AND id = ?",
               (target, persona_id, source_id))
    return {"source_id": source_id, "evidence_ids": [segment[1] for segment in segments]}


def event_key(segment: dict) -> str | None:
    if "event_key" not in segment:
        return None
    value = string(segment["event_key"], "event_key", 120).strip().casefold()
    return None if value in {"unknown", "未知", "不明", "?"} else value
