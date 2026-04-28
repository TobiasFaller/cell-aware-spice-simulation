from typing import Any
from dataclasses import dataclass

from PySpice.Unit.Unit import PrefixedUnit, UnitValue, UnitValues, UnitMetaclass, UnitPrefixMetaclass
from PySpice.Unit import UnitValueShorcut

UnitValue.__hash__ = lambda self: self._prefixed_unit.__hash__() << 16 | self._value.__hash__()

# Placeholder just to import something
_ = None
