from concurrent.futures import ThreadPoolExecutor

import pytest

from geometrize_py.resources import WorkBudget, scene_memory_bytes


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


def test_memory_only_lease_is_available_when_workers_are_full() -> None:
    budget = WorkBudget(1, 100)
    fitting = budget.try_reserve(1, 60)
    assert fitting is not None
    request = budget.try_reserve(0, 20)
    assert request is not None
    assert budget.usage() == (1, 80)
    enlarged = budget.try_resize(request, 40)
    assert enlarged is not None
    assert budget.usage() == (1, 100)
    assert budget.try_reserve(0, 1) is None
    with pytest.raises(ValueError):
        budget.try_resize(enlarged, float("nan"))
    assert budget.usage() == (1, 100)
    budget.release(enlarged)
    budget.release(fitting)
    assert budget.usage() == (0, 0)
    for workers, memory in ((-1, 1), (0, 0), (0, -1), (0, float("nan")), (0, 1.5), (0, True)):
        with pytest.raises(ValueError):
            budget.try_reserve(workers, memory)


def test_scene_memory_estimate_accounts_for_total_vertices() -> None:
    plain = scene_memory_bytes(100, 0)
    with_points = scene_memory_bytes(100, 100_000)
    assert with_points - plain >= 100_000 * 192
    assert with_points > 12_340_000  # measured validation peak for this geometry
    with pytest.raises(ValueError):
        scene_memory_bytes(1, -1)
    with pytest.raises(ValueError):
        scene_memory_bytes(1, float("nan"))


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
