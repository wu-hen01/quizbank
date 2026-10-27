"""间隔重复调度（SM-2 简化版）。"""
from datetime import datetime, timedelta, timezone
from typing import Dict, Tuple


def schedule(ease: float, interval: float, reps: int, grade: int) -> Tuple[str, float, float, int]:
    """grade: 0-5。返回 (下次到期时间, 新间隔天数, 新 ease, 新 reps)。"""
    grade = max(0, min(5, int(grade)))
    ease = float(ease or 2.5)
    interval = float(interval or 0.0)
    reps = int(reps or 0)

    if grade < 3:
        reps = 0
        interval = 1.0
        ease = max(1.3, ease - 0.2)
    else:
        if reps == 0:
            interval = 1.0
        elif reps == 1:
            interval = 3.0
        else:
            interval = max(1.0, round(interval * ease, 2))
        reps += 1
        ease = max(1.3, ease + (0.1 - (5 - grade) * (0.08 + (5 - grade) * 0.02)))

    due = datetime.now(timezone.utc) + timedelta(days=interval)
    return due.isoformat(timespec="seconds"), interval, round(ease, 3), reps


def describe(interval: float) -> str:
    days = float(interval or 0)
    if days <= 0:
        return "今天"
    if days < 1:
        return "今天"
    if days < 30:
        return f"{days:.0f} 天后"
    return f"{days / 30:.1f} 个月后"
