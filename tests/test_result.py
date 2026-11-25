from app.core.result import calc_result


def test_win() -> None:
    assert calc_result(3, 1) == (1, 0, 0)


def test_draw() -> None:
    assert calc_result(2, 2) == (0, 1, 0)


def test_loss() -> None:
    assert calc_result(0, 2) == (0, 0, 1)


def test_zero_zero_is_draw() -> None:
    assert calc_result(0, 0) == (0, 1, 0)
