"""Simple Spaced Repetition Engine."""

def calculate_next_interval(current_interval: int, rating: str) -> int:
    """Calculate the next review interval in days."""
    intervals = {
        "again": 1,
        "hard": max(1, current_interval * 2),
        "good": max(1, current_interval * 3),
        "easy": max(1, current_interval * 5),
    }
    return intervals.get(rating.lower(), current_interval)