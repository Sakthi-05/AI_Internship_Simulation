"""Dependency-free SM-2 style spaced repetition scheduler."""
from dataclasses import dataclass
from datetime import date, timedelta

MIN_EASE_FACTOR = 1.3
DEFAULT_EASE_FACTOR = 2.5

@dataclass
class ReviewResult:
    interval_days: int
    repetitions: int
    ease_factor: float
    next_review: date

def calculate_next_review(quality: int, repetitions: int = 0,
                          ease_factor: float = DEFAULT_EASE_FACTOR,
                          previous_interval: int = 0,
                          review_date: date | None = None) -> ReviewResult:
    if not isinstance(quality, int) or isinstance(quality, bool):
        raise TypeError("quality must be an integer from 0 to 5")
    if not 0 <= quality <= 5:
        raise ValueError("quality must be between 0 and 5")
    if repetitions < 0:
        raise ValueError("repetitions cannot be negative")
    if ease_factor < MIN_EASE_FACTOR:
        raise ValueError("ease_factor cannot be below 1.3")
    if previous_interval < 0:
        raise ValueError("previous_interval cannot be negative")

    d = 5 - quality
    new_ease = max(MIN_EASE_FACTOR, ease_factor + (0.1 - d * (0.08 + d * 0.02)))

    if quality < 3:
        new_repetitions = 0
        interval = 1
    else:
        new_repetitions = repetitions + 1
        if repetitions == 0:
            interval = 1
        elif repetitions == 1:
            interval = 6
        else:
            interval = max(1, round(previous_interval * ease_factor))

    current = review_date or date.today()
    return ReviewResult(interval, new_repetitions, round(new_ease, 4),
                        current + timedelta(days=interval))
