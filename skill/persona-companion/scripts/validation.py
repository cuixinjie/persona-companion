"""路径边界与持久化业务输入校验。"""

import os
import ntpath
import stat
from datetime import datetime, timezone
from pathlib import Path

from contracts import ContractError, invalid

PACKAGE_DIR = Path(__file__).resolve().parents[1]


def checked_path(path: Path) -> Path:
    """拒绝所有祖先中的符号链接及 Windows junction/reparse point。"""
    if not path.is_absolute() or ".." in path.parts or path.drive.startswith("\\"):
        raise ContractError("PERMISSION_DENIED", "请使用本地绝对路径，且路径不得有上级目录分量。")
    for component in (*reversed(path.parents), path):
        if os.name == "nt" and component.name and (
            ":" in component.name or component.name.endswith((".", " "))
            or (ntpath.isreserved(component.name) if hasattr(ntpath, "isreserved") else component.is_reserved())
        ):
            raise ContractError("PERMISSION_DENIED", "路径包含保留名称或特殊文件路径。")
        try:
            metadata = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise ContractError("PERMISSION_DENIED", "路径不得经过链接或重解析点。")
        if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink > 1:
            raise ContractError("PERMISSION_DENIED", "路径不得指向具有多个硬链接的文件。")
    return path.resolve()


def data_root(value: str) -> Path:
    root = checked_path(Path(value))
    if root == Path(root.anchor) or root.is_relative_to(PACKAGE_DIR) or PACKAGE_DIR.is_relative_to(root):
        raise ContractError("PERMISSION_DENIED", "数据目录须为技能包外的独立私有目录。")
    if root.exists() and not root.is_dir():
        raise invalid("data_dir")
    return root


def scoped_path(root: Path, relative: Path) -> Path:
    path = root / relative
    if relative.is_absolute() or path == root or not path.is_relative_to(root):
        raise ContractError("PERMISSION_DENIED", "文件路径超出允许的数据目录。")
    path = checked_path(path)
    if not path.is_relative_to(root):
        raise ContractError("PERMISSION_DENIED", "文件路径超出允许的数据目录。")
    return path


def input_file(root: Path, value: str) -> Path:
    path = checked_path(Path(value))
    inbox = root / "tmp" / "imports"
    if path == inbox or not path.is_relative_to(inbox):
        raise ContractError("PERMISSION_DENIED", "请把要导入的文件放入 data-dir/tmp/imports。")
    if not path.is_file():
        raise ContractError("SOURCE_UNREADABLE", "导入文件不存在或无法读取。")
    return path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def utc_timestamp(value: str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and len(value) <= 40 and "T" in value:
        try:
            timestamp = datetime.fromisoformat(value)
            if timestamp.tzinfo is not None:
                return timestamp.astimezone(timezone.utc).isoformat(timespec="microseconds")
        except (ValueError, OverflowError):
            pass
    raise invalid("occurred_at")

