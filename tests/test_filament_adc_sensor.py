import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import Mock


class FakeRunoutHelper:
    def __init__(self, config):
        self.filament_present = False
        self.sensor_enabled = True
        self.events = []

    def note_filament_present(self, eventtime, present):
        self.filament_present = present
        self.events.append((eventtime, present))

    def get_status(self, eventtime):
        return {"filament_detected": self.filament_present, "enabled": True}


def _load(**options):
    package = types.ModuleType("fakeextras")
    package.__path__ = []
    helper_module = types.ModuleType("fakeextras.filament_switch_sensor")
    helper_module.RunoutHelper = FakeRunoutHelper
    package.filament_switch_sensor = helper_module
    sys.modules["fakeextras"] = package
    sys.modules["fakeextras.filament_switch_sensor"] = helper_module
    path = Path(__file__).resolve().parents[1] / "extras" / "filament_adc_sensor.py"
    spec = importlib.util.spec_from_file_location("fakeextras.filament_adc_sensor", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    values = {"sensor": "filament_adc1", "threshold": 15., "hysteresis": 2.,
              "present_when": "below", "sample_time": .25, "debounce_count": 2}
    values.update(options)
    config = Mock()
    config.get_name.return_value = "filament_adc_sensor filament_runout_nozzle"
    config.get.side_effect = lambda key, default=None: values.get(key, default)
    config.getfloat.side_effect = lambda key, default=None, **kw: values.get(key, default)
    config.getint.side_effect = lambda key, default=None, **kw: values.get(key, default)
    config.getchoice.side_effect = lambda key, choices, default=None: values.get(key, default)
    printer = Mock()
    config.get_printer.return_value = printer
    sensor = module.AdcFilamentSensor(config)
    source = Mock()
    sensor.source = source
    return sensor, source, printer


def _feed(sensor, source, readings):
    for n, value in enumerate(readings):
        source.get_status.return_value = {"temperature": value}
        sensor._sample(float(n))


def test_registers_alias_the_ace_driver_looks_up():
    _sensor, _source, printer = _load()
    printer.add_object.assert_called_once()
    assert printer.add_object.call_args[0][0] == "filament_switch_sensor filament_runout_nozzle"


def test_detects_filament_after_debounce():
    sensor, source, _ = _load()
    _feed(sensor, source, [2.6])
    assert sensor.runout_helper.filament_present is False
    _feed(sensor, source, [2.6, 2.6])
    assert sensor.runout_helper.filament_present is True


def test_single_noisy_sample_is_ignored():
    sensor, source, _ = _load()
    _feed(sensor, source, [2.6, 2.6, 28.0, 2.6, 2.6])
    assert sensor.runout_helper.filament_present is True
    assert len(sensor.runout_helper.events) == 1


def test_runout_after_consecutive_high_samples():
    sensor, source, _ = _load()
    _feed(sensor, source, [2.6, 2.6, 28.0, 28.0])
    assert sensor.runout_helper.filament_present is False


def test_values_inside_the_band_keep_the_state():
    sensor, source, _ = _load()
    _feed(sensor, source, [2.6, 2.6, 15.5, 15.5, 14.5, 14.5])
    assert sensor.runout_helper.filament_present is True


def test_present_when_above_inverts_the_meaning():
    sensor, source, _ = _load(present_when="above")
    _feed(sensor, source, [28.0, 28.0])
    assert sensor.runout_helper.filament_present is True
    _feed(sensor, source, [2.6, 2.6])
    assert sensor.runout_helper.filament_present is False


def test_missing_reading_does_nothing():
    sensor, source, _ = _load()
    _feed(sensor, source, [None, None, None])
    assert sensor.runout_helper.events == []
