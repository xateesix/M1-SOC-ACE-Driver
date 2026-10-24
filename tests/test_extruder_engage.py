"""Coordinated extruder turning while the ACE feeds to a toolhead sensor that
sits past the extruder gears (the ACE cannot push through stopped gears)."""
from unittest.mock import Mock, patch

import pytest

from ace.config import INSTANCE_MANAGERS
from ace.instance import AceInstance


class Clock:
    """Fake time.time(): every poll or move advances it by its own duration."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _instance(clock, engage=100.0, trigger_after_moves=None, can_extrude=True):
    inst = object.__new__(AceInstance)
    inst.instance_num = 1
    inst.gcode = Mock()
    inst.printer = Mock()
    INSTANCE_MANAGERS[1] = Mock()
    inst._info = {"slots": [{"index": 0, "status": "feeding"}]}
    inst._stop_feed = Mock()
    inst._disable_feed_assist = Mock()
    inst._enable_feed_assist = Mock()
    inst._change_feed_speed = Mock(return_value=True)
    inst.execute_feed_with_retries = Mock()
    inst.wait_ready = Mock()
    inst.timeout_multiplier = 2
    inst.FEED_ERROR_GRACE_S = 10**9
    inst.extruder_feeding_length = 1
    inst.extruder_engage_length = engage
    inst.extruder_engage_speed = 0.0
    inst.extruder_engage_chunk = 5.0
    inst.extruder_engage_max_length = 400.0

    state = {"moves": 0, "can_extrude": can_extrude}
    heater = Mock()
    type(heater).can_extrude = property(lambda self: state["can_extrude"])
    heater.target_temp = 230.0
    heater.min_extrude_temp = 170.0
    inst.printer.lookup_object.return_value.get_extruder.return_value.get_heater.return_value = heater

    def dwell(delay=0.1):
        clock.now += delay

    def extruder_move(length, speed, wait_for_move_end=False):
        if length == inst.extruder_engage_chunk:
            state["moves"] += 1
            clock.now += length / speed
        else:
            state["seat"] = (length, speed)

    inst.dwell = dwell
    inst._extruder_move = extruder_move
    inst._state = state

    def run_script(script):
        if script.startswith("M109"):
            state["can_extrude"] = True
            clock.now += 40.0

    inst.gcode.run_script_from_command.side_effect = run_script

    def switch(_name):
        return (
            trigger_after_moves is not None
            and state["moves"] >= trigger_after_moves
        )

    INSTANCE_MANAGERS[1].get_switch_state.side_effect = switch
    return inst


def _run(inst, clock, feed_length=2000.0, speed=50.0):
    with patch("ace.instance.time.time", clock):
        return inst._feed_to_toolhead_with_extruder_assist(
            0, feed_length=feed_length, feed_speed=speed,
            extruder_feeding_length=1, extruder_feeding_speed=5,
        )


def test_extruder_turns_after_engage_length_until_sensor_trips():
    clock = Clock()
    inst = _instance(clock, engage=100.0, trigger_after_moves=4)
    _run(inst, clock)
    assert inst._state["moves"] == 4
    inst._stop_feed.assert_called()  # the existing seat step still runs
    assert inst._state["seat"] == (1, 5)
    inst._stop_feed.assert_called_once_with(0)  # no hold: extruder was hot


def test_disabled_by_default_never_moves_the_extruder_early():
    clock = Clock()
    inst = _instance(clock, engage=0.0, trigger_after_moves=None)
    # Sensor never trips: feed times out, the 60 s assist fails, as before
    with pytest.raises(ValueError, match="not triggering"):
        _run(inst, clock, feed_length=100.0)
    assert inst._state["moves"] == 0


def test_cold_extruder_holds_feed_heats_and_resumes_remaining_length():
    clock = Clock()
    inst = _instance(clock, engage=100.0, trigger_after_moves=2, can_extrude=False)
    _run(inst, clock, feed_length=2000.0, speed=50.0)
    inst.gcode.run_script_from_command.assert_any_call("M109 S230")
    # first call is the initial feed, second the resume for what is left
    first, second = inst.execute_feed_with_retries.call_args_list[:2]
    assert first.args[1] == 2000.0
    assert 1800.0 <= second.args[1] < 1950.0
    assert inst._state["moves"] == 2


def test_gives_up_after_max_extruder_travel():
    clock = Clock()
    inst = _instance(clock, engage=100.0, trigger_after_moves=None)
    inst.extruder_engage_max_length = 20.0
    with pytest.raises(ValueError, match="not triggering"):
        _run(inst, clock, feed_length=5000.0)
    assert inst._state["moves"] == 4
