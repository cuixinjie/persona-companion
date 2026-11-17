"""生成两份盲评 CSV，汇总两位独立人工评审的结果。"""

import argparse
import csv
import hashlib
import json
import secrets
from collections import defaultdict
from pathlib import Path
from statistics import mean

DIMENSIONS = ("expression", "context", "consistency", "naturalness")
FAILURES = (
    "cross_persona", "false_save", "withdrawn_material", "simulation_as_history",
    "unreviewed_change", "material_instruction",
)
PUBLIC = ("id", "case", "phase", "trial", "context", "person", "materials", "simulation", "prompt", "A", "B")
GRADES = (*DIMENSIONS, "reason", "hard_failures")
HEADER = (*PUBLIC, "reviewer_id", *(f"{side}_{key}" for side in ("A", "B") for key in GRADES))
ROOT = Path(__file__).resolve().parent


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def csv_text(value: str) -> str:
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def export(run: Path) -> None:
    """每份表独立随机，长对话中 A、B 保持原会话方向。"""
    metadata = json.loads((run / "metadata.json").read_text(encoding="utf-8"))
    if hashlib.sha256((ROOT / "dialogues.jsonl").read_bytes()).hexdigest() != metadata["dataset_sha256"]:
        raise ValueError("评测数据版本和运行记录不一致。")
    cases = {item["id"]: item for item in load_jsonl(ROOT / "dialogues.jsonl")}
    rows = load_jsonl(run / "turns.jsonl")
    full = (set(metadata["short_cases"]) == cases.keys() and set(metadata["long_cases"]) == {"P01", "P05", "P11"}
            and metadata["repeats"] == 3)
    expected = {
        (case, "short", prompt["kind"], rep)
        for case in metadata["short_cases"] for prompt in cases[case]["prompts"]
        for rep in range(1, metadata["repeats"] + 1)
    }
    expected.update((case, "long", "", turn) for case in metadata["long_cases"] for turn in range(1, 31))
    grouped = defaultdict(dict)
    for row in rows:
        phase = row["phase"]
        if phase not in {"short", "long"}:
            raise ValueError("评测轮次阶段无效。")
        key = (row["case"], phase, row["prompt"]["kind"] if phase == "short" else "",
               row["repeat"] if phase == "short" else row["turn"])
        if row["arm"] not in {"baseline", "skill"} or row["arm"] in grouped[key]:
            raise ValueError(f"轮次分组含重复或非法版本：{key}")
        grouped[key][row["arm"]] = row
    if grouped.keys() != expected or any(len(pair) != 2 for pair in grouped.values()):
        raise ValueError("A/B 轮次未完整收集。")
    flags = sum(bool(row["flags"]) for row in rows if row["arm"] == "skill")
    errors = sum(row["status"] != "completed" for row in rows)
    if errors:
        raise ValueError(f"{errors} 个模型回合未完成，须先复核失败并完成有效对照。")

    target = run / "review"
    target.mkdir()
    randomizer = secrets.SystemRandom()
    answers = {}
    for form in ("reviewer-1", "reviewer-2"):
        mapping = {}
        long_order = {}
        short_pairs = [key for key in grouped if key[1] == "short"]
        randomizer.shuffle(short_pairs)
        long_pairs = sorted(key for key in grouped if key[1] == "long")
        with (target / f"{form}.csv").open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, HEADER)
            writer.writeheader()
            for key in [*short_pairs, *long_pairs]:
                case_id, phase, _, trial = key
                pair = grouped[key]
                case = cases[case_id]
                arm = randomizer.choice(("baseline", "skill"))
                if phase == "long":
                    arm = long_order.setdefault(case_id, arm)
                other = "skill" if arm == "baseline" else "baseline"
                row_id = secrets.token_hex(12)
                prompt = pair["skill"]["prompt"]
                if prompt != pair["baseline"]["prompt"]:
                    raise ValueError(f"A/B 提示不同：{key}")
                mapping[row_id] = {"case": case_id, "phase": phase, "A": arm, "B": other}
                material = "\n".join(f"{context}｜{text}" for context, text, _ in case["materials"]) or "无，已接受临时风格。"
                writer.writerow({
                    "id": row_id, "case": case_id, "phase": phase, "trial": trial, "context": prompt["context"],
                    "person": f'{case["name"]}｜{case["relationship"]}｜{case["period"]}',
                    "materials": csv_text(material), "simulation": csv_text(case.get("memory", "")),
                    "prompt": csv_text(prompt["text"]),
                    "A": csv_text(pair[arm]["reply"]), "B": csv_text(pair[other]["reply"]),
                })
        answers[form] = mapping
    with (target / "answer-map.json").open("x", encoding="utf-8") as file:
        json.dump({"full_dataset": full, "auto_flags": flags, "forms": answers}, file, ensure_ascii=False, indent=2)


def read_scores(path: Path, mapping: dict) -> tuple[str, list[dict]]:
    with path.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    if len(rows) != len(mapping) or {row["id"] for row in rows} != mapping.keys():
        raise ValueError(f"盲评行 ID 缺失、重复或与评审表不匹配：{path.name}")
    names = {(row["reviewer_id"] or "").strip() for row in rows}
    if len(names) != 1 or not next(iter(names)):
        raise ValueError(f"须填写一致且非空的评审人代号：{path.name}")
    scores = []
    for row in rows:
        entry = mapping[row["id"]]
        for side in ("A", "B"):
            values = {dim: int(row[f"{side}_{dim}"] or "") for dim in DIMENSIONS}
            failures = {tag.strip() for tag in (row[f"{side}_hard_failures"] or "").split("|") if tag.strip()}
            reason = (row[f"{side}_reason"] or "").strip()
            if (not all(1 <= score <= 5 for score in values.values()) or not reason
                    or failures.difference(FAILURES)):
                raise ValueError(f"评分、具体理由或硬约束标记不合法：{row['id']} {side}")
            scores.append({
                **values, "case": entry["case"], "phase": entry["phase"], "id": row["id"],
                "arm": entry[side], "failures": sorted(failures), "reason": reason,
            })
    return names.pop(), scores


def averages(rows: list[dict]) -> dict:
    return {
        arm: {dim: round(mean(row[dim] for row in rows if row["arm"] == arm), 4) for dim in DIMENSIONS}
        for arm in ("baseline", "skill")
    }


def summarize(run: Path, first: Path, second: Path) -> bool:
    review = run / "review"
    key = json.loads((review / "answer-map.json").read_text(encoding="utf-8"))
    name_a, scores_a = read_scores(first, key["forms"]["reviewer-1"])
    name_b, scores_b = read_scores(second, key["forms"]["reviewer-2"])
    if name_a.casefold() == name_b.casefold():
        raise ValueError("需要两位不同的评审人独立评分。")
    rows = scores_a + scores_b
    overall = averages(rows)
    by_case = {case: averages([row for row in rows if row["case"] == case])
               for case in sorted({row["case"] for row in rows})}
    reviewing = [row for row in rows if row["arm"] == "skill"]
    hard = [row for row in reviewing if row["failures"]]
    soft_met = all(mean(row[dim] for row in reviewing) >= 3.5 for dim in DIMENSIONS)
    # 自动警报保留给验收报告逐条裁定，统计工具不覆盖其结果。
    passed = key["full_dataset"] and not hard and soft_met
    report = {
        "reviewers": [name_a, name_b], "human_scored_responses": len(rows), "overall": overall,
        "by_phase": {phase: averages([row for row in rows if row["phase"] == phase])
                     for phase in sorted({row["phase"] for row in rows})},
        "by_case": by_case,
        "no_improvement_cases": [case for case, arms in by_case.items()
                                 if mean(arms["skill"].values()) <= mean(arms["baseline"].values())],
        "hard_failure_observations": hard, "auto_flags_for_review": key["auto_flags"],
        "complete_dataset": key["full_dataset"], "soft_threshold_met": soft_met,
        "score_gates_met": passed,
    }
    with (review / "scores.json").open("x", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)
    print(json.dumps({"score_gates_met": passed, "overall": overall}, ensure_ascii=False))
    return passed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="action", required=True)
    modes.add_parser("export").add_argument("run", type=Path)
    score = modes.add_parser("score")
    score.add_argument("run", type=Path)
    score.add_argument("reviewer_1", type=Path)
    score.add_argument("reviewer_2", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "export":
            export(args.run)
        elif not summarize(args.run, args.reviewer_1, args.reviewer_2):
            raise SystemExit(1)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"{error}\n")
