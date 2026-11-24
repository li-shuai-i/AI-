"""Energy and macro maths for a fat-loss oriented food log.

The chain is: body stats -> BMR (Mifflin-St Jeor) -> TDEE (activity factor)
-> daily calorie target (deficit for fat loss, clamped by a safety floor)
-> macro split (protein first, because preserving muscle while dieting is
the whole point, then fat, carbohydrate fills the rest).

Everything here is pure functions over plain dicts so it can be unit tested
without a server or a database.
"""

from __future__ import annotations

from datetime import date, timedelta
from statistics import mean
from typing import Iterable, Sequence

MEALS = [
    ("breakfast", "早餐"),
    ("lunch", "午餐"),
    ("dinner", "晚餐"),
    ("snack", "加餐"),
]
MEAL_LABELS = dict(MEALS)
MEAL_ORDER = [key for key, _ in MEALS]

ACTIVITY = {
    "sedentary": {"label": "久坐，几乎不运动", "factor": 1.2},
    "light": {"label": "每周运动 1–3 次", "factor": 1.375},
    "moderate": {"label": "每周运动 3–5 次", "factor": 1.55},
    "active": {"label": "几乎每天运动 / 体力工作", "factor": 1.725},
}

GOALS = {
    "lose": {"label": "减脂", "protein_per_kg": 1.6},
    "keep": {"label": "保持", "protein_per_kg": 1.3},
    "gain": {"label": "增肌", "protein_per_kg": 1.8},
}

# 1 kg of body fat is conventionally treated as ~7700 kcal.
KCAL_PER_KG_FAT = 7700.0

DEFAULT_PROFILE = {
    "sex": "female",
    "age": 30,
    "height_cm": 165.0,
    "weight_kg": 60.0,
    "activity": "light",
    "goal": "lose",
    "deficit": 400,
    "name": "",
}

VEGETABLE_ENERGY_FLOOR = 1200.0


def _num(value, fallback: float) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return fallback
    if out != out or out in (float("inf"), float("-inf")):  # NaN / inf
        return fallback
    return out


def normalise_profile(raw: dict | None) -> dict:
    """Fill in defaults and clamp nonsense to something workable."""
    raw = raw or {}
    base = dict(DEFAULT_PROFILE)
    base.update({k: v for k, v in raw.items() if k in DEFAULT_PROFILE})

    if base["sex"] not in ("female", "male"):
        base["sex"] = "female"
    if base["activity"] not in ACTIVITY:
        base["activity"] = "light"
    if base["goal"] not in GOALS:
        base["goal"] = "lose"

    base["age"] = int(max(14, min(100, _num(base["age"], 30))))
    base["height_cm"] = round(max(120.0, min(230.0, _num(base["height_cm"], 165.0))), 1)
    base["weight_kg"] = round(max(35.0, min(250.0, _num(base["weight_kg"], 60.0))), 1)
    base["deficit"] = int(max(0, min(1000, _num(base["deficit"], 400))))
    base["name"] = str(base["name"] or "").strip()[:24]
    return base


def bmr(profile: dict) -> float:
    """Mifflin-St Jeor resting metabolic rate in kcal/day."""
    weight = profile["weight_kg"]
    height = profile["height_cm"]
    age = profile["age"]
    score = 10 * weight + 6.25 * height - 5 * age
    return score + (5 if profile["sex"] == "male" else -161)


def tdee(profile: dict) -> float:
    return bmr(profile) * ACTIVITY[profile["activity"]]["factor"]


def targets(profile: dict) -> dict:
    """Daily calorie and macro targets plus the reasoning behind them."""
    profile = normalise_profile(profile)
    resting = bmr(profile)
    total = tdee(profile)

    goal = profile["goal"]
    if goal == "lose":
        raw_target = total - profile["deficit"]
    elif goal == "gain":
        raw_target = total + 300
    else:
        raw_target = total

    # Never push intake below resting metabolism by much, and never under a
    # hard floor — crash dieting costs muscle and adherence, not just fat.
    floor = max(resting * 1.10, VEGETABLE_ENERGY_FLOOR if profile["sex"] == "female" else 1500.0)
    kcal = max(raw_target, floor) if goal == "lose" else raw_target
    kcal = round(kcal, 0)

    protein = GOALS[goal]["protein_per_kg"] * profile["weight_kg"]
    fat = max(kcal * 0.25 / 9.0, 0.6 * profile["weight_kg"])
    carb = (kcal - protein * 4 - fat * 9) / 4.0
    if carb < 50:  # keep the split usable when protein is very high
        carb = 50.0
        fat = max((kcal - protein * 4 - carb * 4) / 9.0, 0.4 * profile["weight_kg"])

    actual_deficit = round(total - kcal)
    return {
        "bmr": round(resting),
        "tdee": round(total),
        "kcal": int(round(kcal)),
        "protein": round(protein),
        "carb": round(carb),
        "fat": round(fat),
        "deficit": actual_deficit,
        "floor": round(floor),
        "floored": bool(goal == "lose" and raw_target < floor),
        "projected_weekly_loss_kg": round(max(actual_deficit, 0) * 7 / KCAL_PER_KG_FAT, 2),
        "bmi": round(profile["weight_kg"] / ((profile["height_cm"] / 100) ** 2), 1),
    }


def empty_totals() -> dict:
    return {"kcal": 0.0, "protein": 0.0, "carb": 0.0, "fat": 0.0}


def _add(into: dict, item: dict) -> None:
    for key in ("kcal", "protein", "carb", "fat"):
        into[key] += _num(item.get(key), 0.0)


def _round_totals(totals: dict) -> dict:
    return {key: round(value, 1) for key, value in totals.items()}


def day_summary(day: str, entries: Sequence[dict], day_targets: dict) -> dict:
    """Group one day's entries by meal with subtotals and target progress."""
    by_meal: dict[str, dict] = {}
    day_totals = empty_totals()

    for meal in MEAL_ORDER:
        items = [e for e in entries if e.get("date") == day and e.get("meal") == meal]
        items.sort(key=lambda e: e.get("ts", 0))
        subtotal = empty_totals()
        for item in items:
            _add(subtotal, item)
            _add(day_totals, item)
        by_meal[meal] = {
            "key": meal,
            "label": MEAL_LABELS[meal],
            "items": items,
            "totals": _round_totals(subtotal),
        }

    # Entries with an unknown meal key still count towards the day total.
    orphans = [e for e in entries if e.get("date") == day and e.get("meal") not in MEAL_LABELS]
    for item in orphans:
        _add(day_totals, item)

    progress = {}
    for key in ("kcal", "protein", "carb", "fat"):
        goal = _num(day_targets.get(key), 0)
        progress[key] = {
            "value": round(day_totals[key], 1),
            "target": goal,
            "percent": round(day_totals[key] / goal * 100, 1) if goal else 0.0,
            "remaining": round(goal - day_totals[key], 1),
        }

    return {
        "date": day,
        "meals": [by_meal[m] for m in MEAL_ORDER],
        "totals": _round_totals(day_totals),
        "progress": progress,
        "entry_count": sum(len(by_meal[m]["items"]) for m in MEAL_ORDER) + len(orphans),
        "insights": insights(entries, day_totals, day_targets),
    }


def insights(entries: Sequence[dict], totals: dict, day_targets: dict) -> list[dict]:
    """Short, concrete advice — never more than three lines, never empty."""
    if not entries:
        return [{"level": "info", "text": "今天还没记录。吃完随手记一笔，比什么都重要。"}]

    out: list[dict] = []
    kcal_target = _num(day_targets.get("kcal"), 0)
    protein_target = _num(day_targets.get("protein"), 0)
    fat_target = _num(day_targets.get("fat"), 0)
    protein = totals["protein"]
    kcal = totals["kcal"]

    if kcal_target and kcal > kcal_target:
        over = kcal - kcal_target
        # Roughly 3.5 kcal per minute of brisk walking for an average adult.
        out.append({
            "level": "warn",
            "text": f"超出目标 {over:.0f} kcal，大约要慢走 {over / 3.5:.0f} 分钟才能抵掉。",
        })
    elif kcal_target and kcal_target - kcal > 500:
        out.append({
            "level": "info",
            "text": f"还有 {kcal_target - kcal:.0f} kcal 额度。别吃太少，掉肌肉比掉脂肪快。",
        })

    if protein_target:
        gap = protein_target - protein
        if gap > protein_target * 0.2:
            out.append({
                "level": "warn",
                "text": f"蛋白质差 {gap:.0f} g。一个鸡蛋约 6 g，一份鸡胸约 25 g。",
            })
        elif gap > 0:
            out.append({
                "level": "info",
                "text": f"蛋白质只差 {gap:.0f} g，再补一点就达标了。",
            })
        else:
            out.append({
                "level": "good",
                "text": "蛋白质达标了，减脂期这是最该守住的一项。",
            })

    if not any(e.get("category") in ("蔬菜", "水果") for e in entries):
        out.append({"level": "info", "text": "今天还没吃蔬菜或水果，来一份西兰花或一个苹果。"})

    if fat_target and totals["fat"] > fat_target * 1.2:
        out.append({"level": "warn", "text": "脂肪明显偏高，多半是炒菜油或坚果吃多了。"})

    return out[:3]


def week_report(entries: Sequence[dict], day_targets: dict, end_day: str, days: int = 7) -> dict:
    """Daily totals for the trailing window plus averages and streak info."""
    end = date.fromisoformat(end_day)
    start = end - timedelta(days=days - 1)
    series = []
    logged_days = 0
    total_kcal = 0.0
    protein_hit = 0

    for offset in range(days):
        day = (start + timedelta(days=offset)).isoformat()
        day_entries = [e for e in entries if e.get("date") == day]
        totals = empty_totals()
        for item in day_entries:
            _add(totals, item)
        if day_entries:
            logged_days += 1
            total_kcal += totals["kcal"]
            if day_targets.get("protein") and totals["protein"] >= day_targets["protein"] * 0.9:
                protein_hit += 1
        series.append({
            "date": day,
            "label": day[5:].replace("-", "/"),
            "kcal": round(totals["kcal"]),
            "protein": round(totals["protein"]),
            "carb": round(totals["carb"]),
            "fat": round(totals["fat"]),
            "logged": bool(day_entries),
        })

    avg_kcal = round(total_kcal / logged_days) if logged_days else 0
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "days": series,
        "logged_days": logged_days,
        "avg_kcal": avg_kcal,
        "protein_hit_days": protein_hit,
        "target_kcal": day_targets.get("kcal", 0),
        "avg_delta": round(avg_kcal - _num(day_targets.get("kcal"), 0)) if logged_days else 0,
    }


def weight_summary(weights: Iterable[dict]) -> dict:
    """Latest weight, trend over the logged window and BMI."""
    rows = sorted(
        ({"date": w["date"], "kg": round(_num(w.get("kg"), 0), 1)}
         for w in weights if w.get("date") and _num(w.get("kg"), 0) > 0),
        key=lambda w: w["date"],
    )
    if not rows:
        return {"latest": None, "change_7d": None, "change_all": None, "series": []}

    latest = rows[-1]
    by_date = {row["date"]: row["kg"] for row in rows}

    def change_over(days: int):
        target = date.fromisoformat(latest["date"]) - timedelta(days=days)
        earlier = [row for row in rows if date.fromisoformat(row["date"]) <= target]
        if not earlier:
            earlier = rows[:-1]
        if not earlier:
            return None
        return round(latest["kg"] - earlier[-1]["kg"], 1)

    return {
        "latest": latest,
        "change_7d": change_over(7),
        "change_30d": change_over(30),
        "change_all": round(latest["kg"] - rows[0]["kg"], 1) if len(rows) > 1 else None,
        "low": min(row["kg"] for row in rows),
        "high": max(row["kg"] for row in rows),
        "series": rows[-30:],
    }
