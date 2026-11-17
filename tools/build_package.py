"""以明确的文件清单生成可复现的本地 M4 安装包。"""

import argparse
import hashlib
import re
import stat
from pathlib import Path
from tempfile import NamedTemporaryFile
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
SKILL_FILES = (
    "SKILL.md",
    "references/dialogue-guide.md", "references/evidence-rules.md",
    "references/interview-guide.md", "references/operation-contract.md",
    "scripts/contracts.py", "scripts/dialogue.py", "scripts/materials.py", "scripts/memory.py",
    "scripts/persona_cli.py", "scripts/profiles.py", "scripts/questions.py", "scripts/storage.py",
    "scripts/validation.py", "scripts/migrations/001_initial.sql",
    "scripts/migrations/002_persona_construction.sql", "scripts/migrations/003_dialogue.sql",
    "scripts/migrations/004_interview_status.sql",
)
PACKAGE_FILES = {
    **{f"persona-companion/{name}": f"skill/persona-companion/{name}" for name in SKILL_FILES},
    "install.md": "docs/install.md",
    "privacy.md": "docs/privacy.md",
    "synthetic-example.md": "docs/synthetic-example.md",
    "examples/create_persona.json": "tests/fixtures/create_persona.json",
    "examples/synthetic-chat.png": "tests/fixtures/m2-clear.png",
}
SCREENSHOT_SHA256 = "e9ad61adcde14b52a6000344b43b23eb431bdc6305f9f958e5551f1185af20f6"
PRIVATE_TEXT = re.compile(
    r"(?:[A-Z]:[\\/](?:users|开发学习文件夹)[\\/]|/(?:home|Users)/[^/\s<>]+/|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:sk-[\w-]{20,}|gh[pousr]_[A-Za-z0-9_]{20,}|"
    r"AKIA[A-Z0-9]{16})\b)", re.IGNORECASE,
)


def read_entry(root: Path, source: str) -> bytes:
    path = root / source
    for component in (path, *path.parents):
        if component == root:
            break
        attrs = component.lstat()
        if stat.S_ISLNK(attrs.st_mode) or getattr(attrs, "st_file_attributes", 0) & 0x400:
            raise ValueError(f"包源文件经过链接：{source}")
    if not path.is_file() or path.stat().st_nlink > 1:
        raise ValueError(f"包源文件不是独立文件：{source}")
    content = path.read_bytes()
    if path.suffix == ".png":
        if hashlib.sha256(content).hexdigest() != SCREENSHOT_SHA256:
            raise ValueError("合成截图校验失败。")
    else:
        normalized = content.decode("utf-8").replace("\r\n", "\n")
        if PRIVATE_TEXT.search(normalized) or "\0" in normalized:
            raise ValueError(f"包源文件含私有路径、疑似凭据或二进制数据：{source}")
        content = normalized.encode("utf-8")
    return content


def build(output: Path, *, root: Path = ROOT) -> str:
    if output.suffix.lower() != ".zip":
        raise ValueError("输出文件须为 ZIP。")
    contents = {name: read_entry(root, source) for name, source in PACKAGE_FILES.items()}
    output.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=output.parent, suffix=".tmp", delete=False) as file:
        staged = Path(file.name)
    try:
        with ZipFile(staged, "w") as archive:
            for name, content in sorted(contents.items()):
                entry = ZipInfo(name)
                entry.create_system = 3
                entry.external_attr = 0o100644 << 16
                archive.writestr(entry, content, compress_type=ZIP_DEFLATED, compresslevel=9)
        with ZipFile(staged) as archive:
            if archive.testzip() is not None or set(archive.namelist()) != contents.keys():
                raise ValueError("安装包完整性校验失败。")
        digest = hashlib.sha256(staged.read_bytes()).hexdigest()
        staged.replace(output)
        return digest
    finally:
        staged.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/persona-companion-m4.zip")
    args = parser.parse_args()
    try:
        checksum = build(args.output)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    print(f"{checksum}  {args.output}")
