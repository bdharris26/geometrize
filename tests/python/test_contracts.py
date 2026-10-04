from copy import deepcopy

from geometrize_py.contracts import app_contract


def test_contract_mutations_do_not_change_later_responses() -> None:
    expected = deepcopy(app_contract())
    contract = app_contract()

    contract["defaults"]["shape_types"].clear()
    contract["presets"]["quick"]["options"]["steps"] = 1
    contract["presets"]["balanced"]["label"] = "Edited label"
    contract["presets"].clear()
    contract["focus"]["defaults"]["radius"] = 0.8
    contract["focus"]["limits"]["strength"]["max"] = 0.5

    assert app_contract() == expected


def test_focus_contract_is_optional_and_normalized() -> None:
    contract = app_contract()
    assert contract["defaults"]["focus"] is None
    assert contract["focus"] == {
        "defaults": {"radius": 0.2, "strength": 0.75},
        "limits": {"x": {"min": 0, "max": 1}, "y": {"min": 0, "max": 1},
                   "radius": {"min": 0.01, "max": 1}, "strength": {"min": 0, "max": 1}},
    }
