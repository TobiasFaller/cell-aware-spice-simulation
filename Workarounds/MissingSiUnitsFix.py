from typing import Any
from dataclasses import dataclass

from PySpice.Unit.Unit import PrefixedUnit, UnitValue, UnitValues, UnitMetaclass, UnitPrefixMetaclass
from PySpice.Unit.SiUnits import Farad, Femto, Pico
from PySpice.Unit import define_shortcut, UnitValueShorcut


@dataclass
class RescaledUnit:
	callback: Any
	factor: int

	def __call__(self, prefixed_unit, value) -> Any:
		return self.callback(prefixed_unit, value * self.factor)


def _build_unit_prefix_workaround(unit, unit_prefix, workaround_prefix):
	""" Generates missing unit prefixes inside the PySpice.Unit namespace.
		Since these units don't exist in SPICE natively they are
		emulated using other units instead.
	"""
	# Map to actual instances
	unit = UnitMetaclass._units[unit.UNIT_SUFFIX]
	unit_prefix = UnitPrefixMetaclass._prefixes[unit_prefix.POWER]
	workaround_prefix = UnitPrefixMetaclass._prefixes[workaround_prefix.POWER]

	# Create unit that does an automatic remapping for all values
	rescale_factor = pow(10, unit_prefix.power - workaround_prefix.power)
	value_ctor = RescaledUnit(UnitValue, rescale_factor)
	values_ctor = RescaledUnit(UnitValues, rescale_factor)
	prefixed_unit = PrefixedUnit(unit, workaround_prefix, value_ctor, values_ctor)
	PrefixedUnit.register(prefixed_unit)
	shortcut = UnitValueShorcut(prefixed_unit)

	define_shortcut(f'U_{unit_prefix}{unit.unit_suffix}', prefixed_unit)
	define_shortcut(f'u_{unit_prefix}{unit.unit_suffix}', shortcut)

_build_unit_prefix_workaround(Farad, Femto, Pico)


# Placeholder just to import something
_ = None


if __name__ == '__main__':
	from PySpice.Unit import u_fF
	print(repr(5 @ u_fF))
	print(repr([1, 10] @ u_fF))
