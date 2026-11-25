def calc_result(goals_for: int, goals_against: int) -> tuple[int, int, int]:
    """Return (is_win, is_draw, is_loss) from goals.

    EA FC returns wins/losses as zero in friendly matches;
    this function derives the result purely from the scoreline.
    """
    if goals_for > goals_against:
        return (1, 0, 0)
    if goals_for == goals_against:
        return (0, 1, 0)
    return (0, 0, 1)
