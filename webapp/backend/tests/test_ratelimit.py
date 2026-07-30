from app.ratelimit import SlidingWindowLimiter


def test_allows_up_to_max_then_blocks():
    now = [0.0]
    lim = SlidingWindowLimiter(3, 3600, clock=lambda: now[0])
    assert lim.allow("a")
    assert lim.allow("a")
    assert lim.allow("a")
    assert not lim.allow("a")


def test_keys_are_independent():
    now = [0.0]
    lim = SlidingWindowLimiter(1, 3600, clock=lambda: now[0])
    assert lim.allow("a")
    assert lim.allow("b")
    assert not lim.allow("a")


def test_window_slides():
    now = [0.0]
    lim = SlidingWindowLimiter(2, 100, clock=lambda: now[0])
    assert lim.allow("a")
    now[0] = 50.0
    assert lim.allow("a")
    assert not lim.allow("a")
    now[0] = 101.0  # first event has aged out
    assert lim.allow("a")
    assert not lim.allow("a")
