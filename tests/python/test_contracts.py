from copy import deepcopy

from geometrize_py.contracts import app_contract


def test_contract_mutations_do_not_change_later_responses() -> None:
    expected = deepcopy(app_contract())
    contract = app_contract()

    contract["defaults"]["shape_types"].clear()
    contract["presets"]["quick"]["options"]["steps"] = 1
    contract["presets"]["balanced"]["label"] = "Edited label"
    contract["presets"].clear()

    assert app_contract() == expected
