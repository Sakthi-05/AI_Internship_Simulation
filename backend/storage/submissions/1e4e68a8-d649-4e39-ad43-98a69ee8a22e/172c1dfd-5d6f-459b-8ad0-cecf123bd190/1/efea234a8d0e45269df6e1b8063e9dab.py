from spaced_repetition import calculate_next_interval

def test_again():
    assert calculate_next_interval(3, "again") == 1

def test_hard():
    assert calculate_next_interval(3, "hard") == 6

def test_good():
    assert calculate_next_interval(3, "good") == 9

def test_easy():
    assert calculate_next_interval(3, "easy") == 15