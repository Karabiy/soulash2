#!/usr/bin/env python3
"""
Verify the `better_weaponsmith` mod against vanilla `core_2`.

Goal
----
For every weaponsmith NPC, compare the list of learnable recipes (skill
"milestones") in three sets:

    * VANILLA  - the recipes the NPC teaches in core_2 (the version the mod overrides)
    * MOD      - the recipes the NPC teaches in better_weaponsmith
    * FULL     - every recipe learnable from the core_2 weaponsmith skill tree

This answers the question "are there more recipes learnable in vanilla?" by
reporting, per NPC:

    * REMOVED   - taught in vanilla but dropped by the mod        (regression)
    * ADDED     - taught by the mod but not in vanilla            (the mod's job)
    * MISSING   - learnable in the full weaponsmith tree but this smith never teaches it
    * INVALID   - milestone id referenced that does not exist in core_2 (typo/stale)
    * NON-SMITH - milestone that exists but is not a weaponsmith milestone
    * DUPLICATE - milestone listed more than once inside the same NPC

It also reports vanilla weaponsmith NPCs the mod does not override, and a
global coverage summary (recipes no modded smith teaches).

Usage
-----
    python verify_weaponsmith.py [--report]

`--report` also writes a Markdown report to
`better_weaponsmith/differences/weaponsmith_diff.md`.

Exit code is non-zero if any hard errors (INVALID references, JSON parse
errors, or REMOVED recipes) are found, so it can be used in CI.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

WEAPONSMITH_SKILL = "core_2_weaponsmith"

# Weaponsmith-side recipe counts the mod advertises per smith.
# Keyed by the NPC label (NPC name with a trailing " Weaponsmith" stripped),
# so pure smiths key on their race ("Human") and hybrid crafters key on their
# full name ("Elf Metalbender").
ADVERTISED_COUNTS = {
    "Human": 35,
    "Dwarf": 30,
    "Goblin": 38,
    "Orc": 34,
    "Mushman": 26,
    "Rasimi": 34,
    "Skeleton": 28,
    "Gnoll": 26,
    "Troll": 30,
    "Reptilion": 28,
    "Ursan": 26,
    "Vampire": 30,
    # Hybrid crafters (weaponsmith-side recipe count only):
    "Elf Metalbender": 21,
    "Bone Wraith Spearmaker": 18,
    "Bone Wraith Smith": 21,
}


def smith_label(name: str) -> str:
    """'Human Weaponsmith' -> 'Human'; hybrids keep their full name."""
    suffix = " Weaponsmith"
    return name[: -len(suffix)] if name.endswith(suffix) else name

ROOT = Path(__file__).resolve().parent
CORE = ROOT / "core_2"
MOD = ROOT / "better_weaponsmith"

CORE_ENTITIES = CORE / "entities"
CORE_MILESTONES = CORE / "milestones"
MOD_ENTITIES = MOD / "entities"


# --------------------------------------------------------------------------- #
# Loading helpers
# --------------------------------------------------------------------------- #
def load_json(path: Path):
    """Return (data, error_message)."""
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh), None
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc}"
    except OSError as exc:
        return None, f"cannot read: {exc}"


def load_milestones(milestones_dir: Path):
    """
    Scan every milestone file. Return:
        by_id        : id -> {name, skill_id, file}
        weaponsmith  : set of milestone ids whose skill_id is the weaponsmith skill
        errors       : list of (file, message)
    """
    by_id: dict[str, dict] = {}
    weaponsmith: set[str] = set()
    errors: list[tuple[Path, str]] = []

    for path in sorted(milestones_dir.rglob("*.json")):
        data, err = load_json(path)
        if err:
            errors.append((path, err))
            continue
        mid = data.get("id")
        if not mid:
            errors.append((path, "milestone has no 'id'"))
            continue
        info = {
            "name": data.get("name", mid),
            "skill_id": data.get("skill_id"),
            "file": path,
        }
        if mid in by_id:
            errors.append((path, f"duplicate milestone id '{mid}' "
                                 f"(also in {by_id[mid]['file'].name})"))
        by_id[mid] = info
        if data.get("skill_id") == WEAPONSMITH_SKILL:
            weaponsmith.add(mid)

    return by_id, weaponsmith, errors


def is_weaponsmith_entity(data: dict) -> bool:
    skills = (data.get("skills") or {}).get("skills") or []
    return WEAPONSMITH_SKILL in skills


def entity_milestones(data: dict) -> list[str]:
    return list((data.get("skills") or {}).get("milestones") or [])


def load_weaponsmith_entities(entities_dir: Path):
    """
    Return:
        by_id  : id -> {name, file, milestones (list, order preserved)}
        errors : list of (file, message)
    Only entities that have the weaponsmith skill are included.
    """
    by_id: dict[str, dict] = {}
    errors: list[tuple[Path, str]] = []

    if not entities_dir.is_dir():
        errors.append((entities_dir, "entities directory not found"))
        return by_id, errors

    for path in sorted(entities_dir.glob("*.json")):
        data, err = load_json(path)
        if err:
            errors.append((path, err))
            continue
        if not is_weaponsmith_entity(data):
            continue
        eid = data.get("id")
        if not eid:
            errors.append((path, "entity has no 'id'"))
            continue
        by_id[eid] = {
            "name": data.get("name", eid),
            "file": path,
            "milestones": entity_milestones(data),
            "skill_list": list((data.get("skills") or {}).get("skills") or []),
        }
    return by_id, errors


# --------------------------------------------------------------------------- #
# Reporting helpers
# --------------------------------------------------------------------------- #
def names(ids, milestone_by_id):
    """Turn a collection of milestone ids into 'Weapon Name (id)' strings, sorted by name."""
    out = []
    for mid in ids:
        info = milestone_by_id.get(mid)
        label = info["name"] if info else "??? UNKNOWN"
        out.append((label, mid))
    return [f"{label} ({mid})" for label, mid in sorted(out, key=lambda x: x[0].lower())]


def main(write_report: bool) -> int:
    lines: list[str] = []

    def emit(text: str = ""):
        print(text)
        lines.append(text)

    # ---- load everything -------------------------------------------------- #
    if not CORE.is_dir():
        print(f"ERROR: core_2 not found at {CORE}", file=sys.stderr)
        return 2

    milestone_by_id, weaponsmith_ids, ms_errors = load_milestones(CORE_MILESTONES)
    vanilla, vanilla_errors = load_weaponsmith_entities(CORE_ENTITIES)
    mod, mod_errors = load_weaponsmith_entities(MOD_ENTITIES)

    full_set = set(weaponsmith_ids)

    emit("=" * 78)
    emit("BETTER WEAPONSMITH - VERIFICATION REPORT")
    emit("=" * 78)
    emit(f"core_2 weaponsmith milestones (full learnable set): {len(full_set)}")
    emit(f"vanilla weaponsmith NPCs found : {len(vanilla)}")
    emit(f"modded  weaponsmith NPCs found : {len(mod)}")
    emit("")

    hard_errors = 0

    # ---- core_2-internal issues (not the mod's fault, informational) ------ #
    if ms_errors or vanilla_errors:
        emit("-" * 78)
        emit("CORE_2 INTERNAL NOTES (not caused by the mod)")
        emit("-" * 78)
        for path, msg in ms_errors + vanilla_errors:
            emit(f"  [note] {path.name}: {msg}")
        emit("")

    # ---- mod load / structural errors (count against the mod) ------------ #
    if mod_errors:
        emit("-" * 78)
        emit("MOD LOAD / STRUCTURAL ERRORS")
        emit("-" * 78)
        for path, msg in mod_errors:
            hard_errors += 1
            emit(f"  [ERROR] {path}: {msg}")
        emit("")

    # ---- per-NPC diff ----------------------------------------------------- #
    emit("-" * 78)
    emit("PER-NPC DIFF  (mod vs vanilla vs full learnable set)")
    emit("-" * 78)

    union_mod: set[str] = set()
    stats: list[dict] = []

    for eid in sorted(mod):
        m = mod[eid]
        mod_list = m["milestones"]
        mod_set = set(mod_list)
        union_mod |= (mod_set & full_set)

        v = vanilla.get(eid)
        vanilla_set = set(v["milestones"]) if v else set()

        # classification
        counts = Counter(mod_list)
        duplicates = {k for k, c in counts.items() if c > 1}
        invalid = {mid for mid in mod_set if mid not in milestone_by_id}
        non_smith = {mid for mid in mod_set
                     if mid in milestone_by_id and mid not in full_set}
        removed = (vanilla_set & full_set) - mod_set     # vanilla taught, mod dropped
        added = (mod_set & full_set) - vanilla_set       # mod added vs vanilla
        missing = full_set - mod_set                     # never taught by this smith

        # hybrid crafters carry extra skills (armorsmith / carpentry); their
        # non-weaponsmith milestones are intentional, not warnings.
        other_skills = [s for s in m.get("skill_list", []) if s != WEAPONSMITH_SKILL]
        is_hybrid = bool(other_skills)

        # advertised-count check
        label = smith_label(m["name"])
        raw_len = len(mod_list)
        valid_count = len(mod_set & full_set)
        advertised = ADVERTISED_COUNTS.get(label)
        stats.append({
            "name": m["name"], "race": label, "raw": raw_len,
            "valid": valid_count, "advertised": advertised,
            "dups": len(duplicates), "invalid": len(invalid),
            "hybrid": is_hybrid,
        })

        emit("")
        hyb = f"  [hybrid: +{', '.join(other_skills)}]" if is_hybrid else ""
        emit(f"### {m['name']}  (id: {eid}){hyb}")
        emit(f"    file        : {m['file'].relative_to(ROOT)}")
        if v:
            emit(f"    vanilla file: {v['file'].relative_to(ROOT)}")
            emit(f"    recipes     : vanilla {len(vanilla_set & full_set)}"
                 f" -> mod {len(mod_set & full_set)}"
                 f"   (full set = {len(full_set)})")
        else:
            emit("    vanilla file: (none - NPC added by mod, no vanilla counterpart)")
            emit(f"    recipes     : mod {len(mod_set & full_set)}"
                 f"   (full set = {len(full_set)})")

        if advertised is not None:
            note = "matches advertised" if valid_count == advertised else \
                   f"MISMATCH vs advertised {advertised}"
            extra = ""
            if raw_len != valid_count:
                extra = (f"  (raw list has {raw_len} entries; "
                         f"{duplicates and str(len(duplicates)) + ' dup ' or ''}"
                         f"{invalid and str(len(invalid)) + ' invalid' or ''}".rstrip()
                         + ")")
            emit(f"    advertised  : {advertised}  ->  actual valid {valid_count}"
                 f"  [{note}]{extra}")

        if invalid:
            hard_errors += len(invalid)
            emit(f"    [ERROR] INVALID milestone ids ({len(invalid)}): "
                 + ", ".join(sorted(invalid)))
        if removed:
            hard_errors += len(removed)
            emit(f"    [ERROR] REMOVED vs vanilla ({len(removed)}) "
                 f"- taught in vanilla, dropped by mod:")
            for label in names(removed, milestone_by_id):
                emit(f"              - {label}")
        if duplicates:
            emit(f"    [WARN]  DUPLICATE entries ({len(duplicates)}): "
                 + ", ".join(names(duplicates, milestone_by_id)))
        if non_smith:
            if is_hybrid:
                emit(f"    [info]  other-skill recipes kept ({len(non_smith)}) "
                     f"- from {', '.join(other_skills)}, preserved as-is")
            else:
                emit(f"    [WARN]  NON-weaponsmith milestones ({len(non_smith)}): "
                     + ", ".join(sorted(non_smith)))
        if added:
            emit(f"    [ok]    ADDED vs vanilla ({len(added)}):")
            for label in names(added, milestone_by_id):
                emit(f"              + {label}")
        if missing:
            emit(f"    [info]  MISSING from full learnable set ({len(missing)}) "
                 f"- learnable in vanilla tree but this smith never teaches:")
            for label in names(missing, milestone_by_id):
                emit(f"              . {label}")
        if not (invalid or removed or duplicates or non_smith or missing):
            emit("    [ok]    teaches every learnable weaponsmith recipe, no issues")

    # ---- vanilla NPCs the mod does not override -------------------------- #
    not_overridden = sorted(set(vanilla) - set(mod))
    if not_overridden:
        emit("")
        emit("-" * 78)
        emit("VANILLA WEAPONSMITH NPCs NOT OVERRIDDEN BY THE MOD")
        emit("-" * 78)
        for eid in not_overridden:
            v = vanilla[eid]
            taught = len(set(v["milestones"]) & full_set)
            emit(f"  - {v['name']} (id: {eid})  teaches {taught} recipes  "
                 f"[{v['file'].name}]")

    # ---- advertised count summary table ---------------------------------- #
    emit("")
    emit("-" * 78)
    emit("ADVERTISED vs ACTUAL RECIPE COUNTS")
    emit("-" * 78)
    emit(f"  {'Smith':<24}{'advert':>7}{'actual':>7}{'raw':>6}{'dup':>5}{'inval':>6}  status")
    for s in sorted(stats, key=lambda x: x["name"]):
        adv = s["advertised"]
        if adv is None:
            status = "no advertised count"
            adv_str = "-"
        elif s["valid"] == adv:
            status = "OK"
            adv_str = str(adv)
        else:
            status = f"MISMATCH ({s['valid'] - adv:+d})"
            adv_str = str(adv)
        tag = " (hybrid)" if s.get("hybrid") else ""
        emit(f"  {s['race']:<24}{adv_str:>7}{s['valid']:>7}{s['raw']:>6}"
             f"{s['dups']:>5}{s['invalid']:>6}  {status}{tag}")

    # ---- global coverage -------------------------------------------------- #
    emit("")
    emit("-" * 78)
    emit("GLOBAL COVERAGE  (union of all modded smiths vs full learnable set)")
    emit("-" * 78)
    never_taught = full_set - union_mod
    emit(f"  recipes taught by at least one modded smith: {len(union_mod)}/{len(full_set)}")
    if never_taught:
        emit(f"  recipes NO modded smith teaches ({len(never_taught)}):")
        for label in names(never_taught, milestone_by_id):
            emit(f"      . {label}")
    else:
        emit("  every learnable weaponsmith recipe is taught by at least one modded smith")

    # ---- summary ---------------------------------------------------------- #
    emit("")
    emit("=" * 78)
    if hard_errors:
        emit(f"RESULT: FAIL - {hard_errors} hard error(s) "
             f"(invalid refs / removed recipes / load errors)")
    else:
        emit("RESULT: PASS - no invalid references, no recipes dropped vs vanilla")
    emit("=" * 78)

    # ---- optional markdown report ---------------------------------------- #
    if write_report:
        report_dir = MOD / "differences"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / "weaponsmith_diff.md"
        report_path.write_text(
            "# Better Weaponsmith - verification report\n\n```\n"
            + "\n".join(lines)
            + "\n```\n",
            encoding="utf-8",
        )
        print(f"\nReport written to {report_path.relative_to(ROOT)}")

    return 1 if hard_errors else 0


if __name__ == "__main__":
    sys.exit(main(write_report="--report" in sys.argv[1:]))
