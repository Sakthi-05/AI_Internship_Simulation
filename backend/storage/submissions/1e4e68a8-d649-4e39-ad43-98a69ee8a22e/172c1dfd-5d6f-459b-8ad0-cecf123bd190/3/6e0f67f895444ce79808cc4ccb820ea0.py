from datetime import date
import pytest
from spaced_repetition import calculate_next_review

REVIEW_DATE = date(2026, 10, 7)

def test_first_successful_review_is_one_day():
    r = calculate_next_review(5, 0, 2.5, 0, REVIEW_DATE)
    assert r.interval_days == 1 and r.repetitions == 1
    assert r.next_review == date(2026, 10, 8)

def test_second_successful_review_is_six_days():
    r = calculate_next_review(4, 1, 2.5, 1, REVIEW_DATE)
    assert r.interval_days == 6 and r.repetitions == 2
    assert r.next_review == date(2026, 10, 13)

def test_later_successful_review_expands_interval():
    r = calculate_next_review(5, 2, 2.5, 6, REVIEW_DATE)
    assert r.interval_days == 15 and r.repetitions == 3
    assert r.next_review == date(2026, 10, 22)

def test_failed_review_resets_repetitions_and_interval():
    r = calculate_next_review(2, 4, 2.5, 30, REVIEW_DATE)
    assert r.interval_days == 1 and r.repetitions == 0
    assert r.next_review == date(2026, 10, 8)

def test_ease_factor_has_minimum_floor():
    r = calculate_next_review(0, 5, 1.3, 20, REVIEW_DATE)
    assert r.ease_factor == 1.3

def test_invalid_quality_is_rejected():
    with pytest.raises(ValueError): calculate_next_review(6)

def test_non_integer_quality_is_rejected():
    with pytest.raises(TypeError): calculate_next_review(4.5)
