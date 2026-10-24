# Analog (ADC) filament sensor
#
# Turns a Klipper temperature_sensor reading into a filament_switch_sensor, so an
# analog filament sensor (for example a hall sensor read through adc_temperature)
# can feed the ACE driver and Klipper's runout logic without a digital pin.
#
# Copyright (C) 2026 XateEsix
#
# Builds on Klipper's RunoutHelper (Copyright (C) 2019 Eric Callahan).
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging

from . import filament_switch_sensor


class AdcFilamentSensor:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.name = config.get_name().split()[-1]
        self.source_name = config.get('sensor')
        self.threshold = config.getfloat('threshold')
        self.hysteresis = config.getfloat('hysteresis', 1., minval=0.)
        self.present_when = config.getchoice(
            'present_when', {'below': 'below', 'above': 'above'}, 'below')
        self.sample_time = config.getfloat('sample_time', .25, above=0.)
        self.debounce_count = config.getint('debounce_count', 2, minval=1)
        self.runout_helper = filament_switch_sensor.RunoutHelper(config)
        self.get_status = self.runout_helper.get_status
        self.source = None
        self.value = None
        self._pending = 0
        self.printer.register_event_handler("klippy:connect",
                                            self._handle_connect)
        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        # The ACE driver looks sensors up as "filament_switch_sensor <name>".
        self.printer.add_object("filament_switch_sensor " + self.name, self)
        self.timer = self.reactor.register_timer(self._sample)

    def _handle_connect(self):
        self.source = self.printer.lookup_object(
            "temperature_sensor " + self.source_name)

    def _handle_ready(self):
        self.reactor.update_timer(self.timer, self.reactor.monotonic() + 1.)

    def _classify(self, value, present):
        """Return True for filament present. Inside the band keep `present`."""
        low = self.threshold - self.hysteresis / 2.
        high = self.threshold + self.hysteresis / 2.
        if value < low:
            return self.present_when == 'below'
        if value > high:
            return self.present_when == 'above'
        return present

    def _sample(self, eventtime):
        try:
            self.value = self.source.get_status(eventtime)['temperature']
        except Exception:
            logging.exception("filament_adc_sensor %s: read failed", self.name)
            return eventtime + self.sample_time
        helper = self.runout_helper
        if self.value is None:
            return eventtime + self.sample_time
        proposed = self._classify(self.value, helper.filament_present)
        if proposed == helper.filament_present:
            self._pending = 0
        else:
            self._pending += 1
            if self._pending >= self.debounce_count:
                self._pending = 0
                helper.note_filament_present(eventtime, proposed)
        return eventtime + self.sample_time

    def get_adc_value(self):
        return self.value


def load_config_prefix(config):
    return AdcFilamentSensor(config)
