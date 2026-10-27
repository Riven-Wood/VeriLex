from __future__ import annotations

import base64
import concurrent.futures
import hashlib
import math
from collections.abc import Mapping, Sequence

import pytest

from verilex_runtime.mcp.errors import MCPDomainError
from verilex_runtime.mcp.models import MCPDiscoveredTool
from verilex_runtime.mcp.receipts import TestReceiptStore, ToolDecision


def _tools() -> tuple[MCPDiscoveredTool, ...]:
    return (
        MCPDiscoveredTool(
            tool_name="read_case",
            description="Read a case",
            input_schema={"type": "object"},
        ),
        MCPDiscoveredTool(
            tool_name="search_cases",
            description="Search cases",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
        ),
    )


def _decisions() -> tuple[ToolDecision, ...]:
    return (
        ToolDecision(
            tool_name="read_case",
            enabled=True,
            expected_description_hash=(
                "40550dee8c8acedc29f3b807db4f36156082ddeada65388d5819c3b4cb9cc747"
            ),
            expected_schema_hash=(
                "a2c799262a3ce3c19ef5cdd983bf3d12b43ab3c426227091b909dcb7054738c0"
            ),
        ),
        ToolDecision(
            tool_name="search_cases",
            enabled=False,
            expected_description_hash=(
                "22c2a4061665d165ca1ac59267361ba389d728a028dfb07cd577d78c21253ab4"
            ),
            expected_schema_hash=(
                "094ec29d007cce150c65abf0756d79ad5b62a1acfdb6e0841f69f1377ef41761"
            ),
        ),
    )


def _store(clock: list[float]) -> TestReceiptStore:
    return TestReceiptStore(
        clock=lambda: clock[0],
        hmac_key=b"k" * 32,
    )


def _issue(store: TestReceiptStore, *, ttl_seconds: int = 600) -> str:
    return store.issue(
        b'{"transport":"stdio"}',
        {
            "env": {"TOKEN": "top-secret"},
            "headers": {"Authorization": "Bearer second-secret"},
        },
        _tools(),
        ("mcp_shell_network_egress",),
        ttl_seconds=ttl_seconds,
    )


def test_receipt_uses_256_bits_and_never_retains_reversible_secret_values():
    clock = [100.0]
    store = _store(clock)

    receipt = _issue(store)

    padding = "=" * (-len(receipt) % 4)
    assert len(base64.urlsafe_b64decode(receipt + padding)) == 32
    assert "top-secret" not in repr(store)
    assert "second-secret" not in repr(store)


def test_receipt_is_one_time_and_bound_to_config_secret_and_tool_snapshot():
    clock = [100.0]
    store = _store(clock)
    receipt = _issue(store)

    verified = store.consume(
        receipt,
        b'{"transport":"stdio"}',
        {
            "env": {"TOKEN": "top-secret"},
            "headers": {"Authorization": "Bearer second-secret"},
        },
        _decisions(),
    )
    assert verified.tools == _tools()
    assert verified.risk_codes == ("mcp_shell_network_egress",)
    verified.commit_consumption()

    with pytest.raises(MCPDomainError) as replayed:
        store.consume(
            receipt,
            b'{"transport":"stdio"}',
            {"env": {"TOKEN": "top-secret"}, "headers": {"Authorization": "Bearer second-secret"}},
            _decisions(),
        )
    assert replayed.value.code == "mcp_test_receipt_invalid"


@pytest.mark.parametrize(
    ("config", "secrets"),
    (
        (b'{"transport":"http"}', {"env": {"TOKEN": "top-secret"}, "headers": {"Authorization": "Bearer second-secret"}}),
        (b'{"transport":"stdio"}', {"env": {"TOKEN": "changed"}, "headers": {"Authorization": "Bearer second-secret"}}),
        (b'{"transport":"stdio"}', {"env": {"TOKEN": "top-secret"}, "headers": {"Authorization": "Bearer changed"}}),
    ),
)
def test_receipt_rejects_changed_config_or_secret_values(config, secrets):
    store = _store([100.0])
    receipt = _issue(store)

    with pytest.raises(MCPDomainError) as captured:
        store.consume(receipt, config, secrets, _decisions())

    assert captured.value.code == "mcp_test_receipt_invalid"
    assert store.is_available(receipt) is True


@pytest.mark.parametrize(
    "decisions",
    (
        _decisions()[:1],
        (*_decisions(), _decisions()[0]),
        (*_decisions(), ToolDecision("extra", True, "0" * 64, "1" * 64)),
        (
            ToolDecision(
                "read_case",
                True,
                "0" * 64,
                _decisions()[0].expected_schema_hash,
            ),
            _decisions()[1],
        ),
        (
            ToolDecision(
                "read_case",
                True,
                _decisions()[0].expected_description_hash,
                "0" * 64,
            ),
            _decisions()[1],
        ),
    ),
)
def test_receipt_requires_one_exact_decision_for_every_tested_tool(decisions):
    store = _store([100.0])
    receipt = _issue(store)

    with pytest.raises(MCPDomainError) as captured:
        store.consume(
            receipt,
            b'{"transport":"stdio"}',
            {"env": {"TOKEN": "top-secret"}, "headers": {"Authorization": "Bearer second-secret"}},
            decisions,
        )

    assert captured.value.code == "mcp_test_receipt_invalid"
    assert store.is_available(receipt) is True


def test_receipt_expires_at_the_exact_ttl_boundary():
    clock = [100.0]
    store = _store(clock)
    receipt = _issue(store, ttl_seconds=10)
    clock[0] = 110.0

    with pytest.raises(MCPDomainError) as captured:
        store.consume(
            receipt,
            b'{"transport":"stdio"}',
            {"env": {"TOKEN": "top-secret"}, "headers": {"Authorization": "Bearer second-secret"}},
            _decisions(),
        )

    assert captured.value.code == "mcp_test_receipt_expired"
    assert store.is_available(receipt) is False


def test_concurrent_consumers_have_exactly_one_winner():
    store = _store([100.0])
    receipt = _issue(store)

    def consume_once():
        try:
            return store.consume(
                receipt,
                b'{"transport":"stdio"}',
                {"env": {"TOKEN": "top-secret"}, "headers": {"Authorization": "Bearer second-secret"}},
                _decisions(),
            )
        except MCPDomainError as error:
            return error.code

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(lambda _index: consume_once(), range(8)))

    winners = [value for value in outcomes if not isinstance(value, str)]
    assert len(winners) == 1
    assert outcomes.count("mcp_test_receipt_invalid") == 7
    winners[0].rollback_consumption()
    assert store.is_available(receipt) is True


def test_rollback_after_expiry_does_not_revive_receipt():
    clock = [100.0]
    store = _store(clock)
    receipt = _issue(store, ttl_seconds=10)
    verified = store.consume(
        receipt,
        b'{"transport":"stdio"}',
        {"env": {"TOKEN": "top-secret"}, "headers": {"Authorization": "Bearer second-secret"}},
        _decisions(),
    )

    clock[0] = 110.0
    verified.rollback_consumption()

    assert store.is_available(receipt) is False
    with pytest.raises(MCPDomainError) as captured:
        store.consume(
            receipt,
            b'{"transport":"stdio"}',
            {"env": {"TOKEN": "top-secret"}, "headers": {"Authorization": "Bearer second-secret"}},
            _decisions(),
        )
    assert captured.value.code == "mcp_test_receipt_invalid"


def test_receipt_hash_uses_unambiguous_domain_separated_secret_framing():
    store = _store([100.0])
    receipt = store.issue(
        b"config",
        {"env": {"a": "bc"}, "headers": {}},
        _tools(),
        (),
    )

    with pytest.raises(MCPDomainError) as captured:
        store.consume(
            receipt,
            b"config",
            {"env": {"ab": "c"}, "headers": {}},
            _decisions(),
        )

    assert captured.value.code == "mcp_test_receipt_invalid"
    assert hashlib.sha256(b"a" + b"bc").digest() == hashlib.sha256(b"ab" + b"c").digest()


def test_receipt_and_repository_share_the_empty_description_hash():
    store = _store([100.0])
    tool = MCPDiscoveredTool("empty_description", "", {"type": "object"})
    decision = ToolDecision(
        "empty_description",
        True,
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "a2c799262a3ce3c19ef5cdd983bf3d12b43ab3c426227091b909dcb7054738c0",
    )
    receipt = store.issue(
        b"config",
        {"env": {}, "headers": {}},
        (tool,),
        (),
    )

    verified = store.consume(
        receipt,
        b"config",
        {"env": {}, "headers": {}},
        (decision,),
    )

    assert verified.tools == (tool,)


def test_verified_tool_schema_mutation_cannot_change_a_retry_snapshot():
    store = _store([100.0])
    receipt = _issue(store)
    first = store.consume(
        receipt,
        b'{"transport":"stdio"}',
        {"env": {"TOKEN": "top-secret"}, "headers": {"Authorization": "Bearer second-secret"}},
        _decisions(),
    )
    first.tools[0].input_schema["title"] = "mutated"
    first.rollback_consumption()

    second = store.consume(
        receipt,
        b'{"transport":"stdio"}',
        {"env": {"TOKEN": "top-secret"}, "headers": {"Authorization": "Bearer second-secret"}},
        _decisions(),
    )

    assert second.tools[0].input_schema == {"type": "object"}


def test_verified_decisions_are_frozen_at_consume_time():
    store = _store([100.0])
    receipt = _issue(store)
    submitted = list(_decisions())

    verified = store.consume(
        receipt,
        b'{"transport":"stdio"}',
        {"env": {"TOKEN": "top-secret"}, "headers": {"Authorization": "Bearer second-secret"}},
        submitted,
    )
    submitted.clear()

    assert verified.decisions == _decisions()


def test_receipt_store_enforces_a_global_snapshot_byte_budget():
    store = TestReceiptStore(
        clock=lambda: 100.0,
        hmac_key=b"k" * 32,
        max_snapshot_bytes=6_000,
    )
    large = MCPDiscoveredTool(
        "large",
        "x" * 4_000,
        {"type": "object", "description": "y" * 1_000},
    )
    store.issue(
        b"config",
        {"env": {}, "headers": {}},
        (large,),
        (),
    )

    with pytest.raises(MCPDomainError) as captured:
        store.issue(
            b"config-2",
            {"env": {}, "headers": {}},
            (large,),
            (),
        )

    assert captured.value.code == "mcp_test_receipt_invalid"


def _snapshot_cost() -> int:
    store = TestReceiptStore(
        clock=lambda: 100.0,
        hmac_key=b"k" * 32,
        max_snapshot_bytes=1_000_000,
    )
    _issue(store)
    return store._snapshot_bytes


def test_snapshot_byte_budget_accepts_the_exact_boundary_and_rejects_one_less():
    cost = _snapshot_cost()
    exact = TestReceiptStore(
        clock=lambda: 100.0,
        hmac_key=b"k" * 32,
        max_snapshot_bytes=cost,
    )
    _issue(exact)
    too_small = TestReceiptStore(
        clock=lambda: 100.0,
        hmac_key=b"k" * 32,
        max_snapshot_bytes=cost - 1,
    )

    with pytest.raises(MCPDomainError) as captured:
        _issue(too_small)

    assert captured.value.code == "mcp_test_receipt_invalid"


def test_commit_releases_snapshot_budget_for_the_next_receipt():
    cost = _snapshot_cost()
    store = TestReceiptStore(
        clock=lambda: 100.0,
        hmac_key=b"k" * 32,
        max_snapshot_bytes=cost,
    )
    receipt = _issue(store)
    verified = store.consume(
        receipt,
        b'{"transport":"stdio"}',
        {
            "env": {"TOKEN": "top-secret"},
            "headers": {"Authorization": "Bearer second-secret"},
        },
        _decisions(),
    )

    verified.commit_consumption()

    assert _issue(store)


def test_expiry_purge_releases_snapshot_budget_including_an_occupied_receipt():
    clock = [100.0]
    cost = _snapshot_cost()
    store = TestReceiptStore(
        clock=lambda: clock[0],
        hmac_key=b"k" * 32,
        max_snapshot_bytes=cost,
    )
    receipt = _issue(store, ttl_seconds=1)
    store.consume(
        receipt,
        b'{"transport":"stdio"}',
        {
            "env": {"TOKEN": "top-secret"},
            "headers": {"Authorization": "Bearer second-secret"},
        },
        _decisions(),
    )
    clock[0] = 101.0

    assert _issue(store)


def test_concurrent_issue_never_exceeds_the_global_snapshot_budget():
    cost = _snapshot_cost()
    store = TestReceiptStore(
        clock=lambda: 100.0,
        hmac_key=b"k" * 32,
        max_snapshot_bytes=cost * 3,
    )

    def issue_once():
        try:
            return _issue(store)
        except MCPDomainError as error:
            return error.code

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(lambda _index: issue_once(), range(8)))

    assert sum(value != "mcp_test_receipt_invalid" for value in outcomes) == 3
    assert outcomes.count("mcp_test_receipt_invalid") == 5
    assert store._snapshot_bytes == cost * 3


class _ExplodingSequence(Sequence):
    def __len__(self):
        return 1

    def __getitem__(self, _index):
        raise RuntimeError("sentinel sequence failure")


class _ExplodingMapping(Mapping):
    def __len__(self):
        return 1

    def __iter__(self):
        raise RuntimeError("sentinel mapping failure")

    def __getitem__(self, _key):
        raise RuntimeError("sentinel mapping failure")


def test_hostile_iterators_are_rejected_without_leaking_internal_exceptions():
    store = _store([100.0])

    for tools, secrets in (
        (_ExplodingSequence(), {"env": {}, "headers": {}}),
        (_tools(), _ExplodingMapping()),
    ):
        with pytest.raises(MCPDomainError) as captured:
            store.issue(b"config", secrets, tools, ())
        assert captured.value.code == "mcp_test_receipt_invalid"

    receipt = _issue(store)
    with pytest.raises(MCPDomainError) as captured:
        store.consume(
            receipt,
            b'{"transport":"stdio"}',
            {
                "env": {"TOKEN": "top-secret"},
                "headers": {"Authorization": "Bearer second-secret"},
            },
            _ExplodingSequence(),
        )
    assert captured.value.code == "mcp_test_receipt_invalid"
    assert store.is_available(receipt) is True


def test_random_source_failure_is_a_stable_receipt_error():
    def fail_random(_size):
        raise RuntimeError("sentinel random failure")

    store = TestReceiptStore(
        clock=lambda: 100.0,
        hmac_key=b"k" * 32,
        random_bytes=fail_random,
    )

    with pytest.raises(MCPDomainError) as captured:
        _issue(store)

    assert captured.value.code == "mcp_test_receipt_invalid"


@pytest.mark.parametrize(
    "ttl_seconds",
    (True, False, 0, -1, 601, 1.0, math.nan, math.inf, 10**100),
)
def test_receipt_rejects_invalid_ttl_values_stably(ttl_seconds):
    store = _store([100.0])

    with pytest.raises(MCPDomainError) as captured:
        _issue(store, ttl_seconds=ttl_seconds)

    assert captured.value.code == "mcp_test_receipt_invalid"


@pytest.mark.parametrize("clock_value", (True, -1, math.nan, math.inf, "100"))
def test_receipt_rejects_invalid_clock_values_stably(clock_value):
    store = TestReceiptStore(
        clock=lambda: clock_value,
        hmac_key=b"k" * 32,
    )

    with pytest.raises(MCPDomainError) as captured:
        _issue(store)

    assert captured.value.code == "mcp_test_receipt_invalid"


def test_duplicate_finalization_and_stale_verified_handles_cannot_revive_or_consume():
    store = _store([100.0])
    receipt = _issue(store)
    first = store.consume(
        receipt,
        b'{"transport":"stdio"}',
        {
            "env": {"TOKEN": "top-secret"},
            "headers": {"Authorization": "Bearer second-secret"},
        },
        _decisions(),
    )
    first.rollback_consumption()
    first.rollback_consumption()
    second = store.consume(
        receipt,
        b'{"transport":"stdio"}',
        {
            "env": {"TOKEN": "top-secret"},
            "headers": {"Authorization": "Bearer second-secret"},
        },
        _decisions(),
    )

    first.commit_consumption()
    assert store.is_available(receipt) is False
    second.commit_consumption()
    second.commit_consumption()
    second.rollback_consumption()
    assert store.is_available(receipt) is False


def _many_tools(count: int) -> tuple[MCPDiscoveredTool, ...]:
    return tuple(
        MCPDiscoveredTool(f"tool-{index}", "", {"type": "object"})
        for index in range(count)
    )


def _many_decisions(count: int) -> tuple[ToolDecision, ...]:
    return tuple(
        ToolDecision(
            f"tool-{index}",
            True,
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "a2c799262a3ce3c19ef5cdd983bf3d12b43ab3c426227091b909dcb7054738c0",
        )
        for index in range(count)
    )


def test_receipt_tool_and_decision_limits_allow_1000_and_reject_1001():
    store = _store([100.0])
    receipt = store.issue(
        b"config",
        {"env": {}, "headers": {}},
        _many_tools(1000),
        (),
    )
    verified = store.consume(
        receipt,
        b"config",
        {"env": {}, "headers": {}},
        _many_decisions(1000),
    )
    verified.rollback_consumption()

    with pytest.raises(MCPDomainError) as too_many_tools:
        store.issue(
            b"config",
            {"env": {}, "headers": {}},
            _many_tools(1001),
            (),
        )
    with pytest.raises(MCPDomainError) as too_many_decisions:
        store.consume(
            receipt,
            b"config",
            {"env": {}, "headers": {}},
            _many_decisions(1001),
        )

    assert too_many_tools.value.code == "mcp_test_receipt_invalid"
    assert too_many_decisions.value.code == "mcp_test_receipt_invalid"
    assert store.is_available(receipt) is True


def test_receipt_risk_limit_allows_64_and_rejects_65():
    store = _store([100.0])
    risks = tuple(f"risk-{index}" for index in range(64))
    receipt = store.issue(
        b"config",
        {"env": {}, "headers": {}},
        (),
        risks,
    )
    verified = store.consume(
        receipt,
        b"config",
        {"env": {}, "headers": {}},
        (),
    )
    assert verified.risk_codes == tuple(sorted(risks))
    verified.rollback_consumption()

    with pytest.raises(MCPDomainError) as captured:
        store.issue(
            b"config",
            {"env": {}, "headers": {}},
            (),
            (*risks, "risk-64"),
        )

    assert captured.value.code == "mcp_test_receipt_invalid"


def test_receipt_schema_budget_allows_2_mib_and_rejects_one_byte_more():
    store = _store([100.0])
    limit = 2 * 1024 * 1024
    overhead = len(b'{"description":"","type":"object"}')

    def sized_tool(index: int, canonical_size: int) -> MCPDiscoveredTool:
        return MCPDiscoveredTool(
            f"schema-budget-{index}",
            "",
            {
                "description": "x" * (canonical_size - overhead),
                "type": "object",
            },
        )

    exact = tuple(sized_tool(index, 65_536) for index in range(32))
    assert store.issue(
        b"config",
        {"env": {}, "headers": {}},
        exact,
        (),
    )
    over = (
        *(sized_tool(index, 65_536) for index in range(31)),
        sized_tool(31, 65_520),
        MCPDiscoveredTool("schema-budget-32", "", {"type": "object"}),
    )

    with pytest.raises(MCPDomainError) as captured:
        store.issue(
            b"config",
            {"env": {}, "headers": {}},
            over,
            (),
        )

    assert captured.value.code == "mcp_test_receipt_invalid"
