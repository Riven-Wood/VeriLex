from concurrent.futures import ThreadPoolExecutor

import pytest

from verilex_runtime.tools import ToolError
from verilex_runtime.web.budget import WebTurnBudget


def test_ninth_page_is_rejected_without_consuming_budget():
    budget = WebTurnBudget(max_pages=8, max_characters=80_000)
    for _ in range(8):
        budget.reserve("turn-1", 1_000)

    with pytest.raises(ToolError) as captured:
        budget.reserve("turn-1", 1)
    assert captured.value.code == "web_turn_limit_reached"

    budget.clear("turn-1")
    budget.reserve("turn-1", 80_000)


def test_character_overflow_is_rejected_without_consuming_budget():
    budget = WebTurnBudget(max_pages=8, max_characters=80_000)
    budget.reserve("turn-1", 79_999)

    with pytest.raises(ToolError) as captured:
        budget.reserve("turn-1", 2)
    assert captured.value.code == "web_turn_limit_reached"

    budget.reserve("turn-1", 1)


def test_clear_releases_only_the_completed_turn_budget():
    budget = WebTurnBudget(max_pages=8, max_characters=80_000)
    budget.reserve("turn-1", 80_000)
    budget.reserve("turn-2", 80_000)

    budget.clear("turn-1")

    budget.reserve("turn-1", 80_000)
    with pytest.raises(ToolError) as captured:
        budget.reserve("turn-2", 1)
    assert captured.value.code == "web_turn_limit_reached"


@pytest.mark.parametrize("turn_id", [None, "", "   ", " turn-1 ", 7])
def test_turn_id_must_be_a_nonempty_string(turn_id):
    budget = WebTurnBudget()

    with pytest.raises(ToolError) as captured:
        budget.reserve(turn_id, 1)
    assert captured.value.code == "web_tool_context_invalid"


@pytest.mark.parametrize("character_count", [-1, True, 1.5, "1"])
def test_character_count_must_be_a_nonnegative_integer(character_count):
    budget = WebTurnBudget()

    with pytest.raises(ToolError) as captured:
        budget.reserve("turn-1", character_count)
    assert captured.value.code == "web_tool_context_invalid"


def test_parallel_reservations_cannot_exceed_page_limit():
    budget = WebTurnBudget(max_pages=8, max_characters=80_000)

    def reserve_once(_index):
        try:
            budget.reserve("turn-1", 1)
        except ToolError as error:
            return error.code
        return "reserved"

    with ThreadPoolExecutor(max_workers=16) as executor:
        outcomes = list(executor.map(reserve_once, range(16)))

    assert outcomes.count("reserved") == 8
    assert outcomes.count("web_turn_limit_reached") == 8
