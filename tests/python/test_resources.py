from concurrent.futures import ThreadPoolExecutor

import pytest

from geometrize_py.resources import WorkBudget


def test_worker_and_memory_limits_are_independent_and_release_restores_capacity() -> None:
    budget = WorkBudget(4, 100)
    first = budget.try_reserve(3, 20)
    assert first is not None
    assert budget.try_reserve(2, 10) is None
    assert budget.try_reserve(1, 81) is None
    second = budget.try_reserve(1, 80)
    assert second is not None
    assert budget.usage() == (4, 100)
    budget.release(first)
    budget.release(second)
    assert budget.usage() == (0, 0)
    with pytest.raises(ValueError, match="released"):
        budget.release(first)


def test_resize_is_atomic_and_failed_resize_preserves_lease() -> None:
    budget = WorkBudget(2, 100)
    lease = budget.try_reserve(1, 20)
    assert lease is not None
    assert budget.try_resize(lease, 101) is None
    assert budget.usage() == (1, 20)
    updated = budget.try_resize(lease, 80)
    assert updated is not None
    assert budget.usage() == (1, 80)
    with pytest.raises(ValueError):
        budget.release(lease)
    budget.release(updated)


def test_concurrent_reservations_never_exceed_global_budget() -> None:
    budget = WorkBudget(4, 100)
    with ThreadPoolExecutor(max_workers=16) as pool:
        leases = list(pool.map(lambda _: budget.try_reserve(1, 25), range(16)))
    admitted = [lease for lease in leases if lease is not None]
    assert len(admitted) == 4
    assert budget.usage() == (4, 100)
    for lease in admitted:
        budget.release(lease)
    assert budget.usage() == (0, 0)
