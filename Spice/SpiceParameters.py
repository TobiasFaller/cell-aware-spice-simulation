from enum import StrEnum
from dataclasses import dataclass, field
from typing import List, Dict
from pathlib import Path

# Generate missing SI-Units
from Workarounds.MissingSiUnitsFix import _

from PySpice.Unit.SiUnits import Volt, Ohm, Second, Farad, Siemens
from PySpice.Unit import u_Ω, u_kΩ, u_GΩ
from PySpice.Spice.Netlist import SubCircuit


class SimulationType(StrEnum):
	STATIC_OPERATING_POINT = 'static'
	TRANSITION = 'transition'

	def __str__(self) -> str:
		return self.value

@dataclass
class SimulationParameters:
	SIMULATION_TYPE: SimulationType
	SIMULATION_TIME: Second
	SIMULATION_STEP: Second
	SIMULATION_GMIN: Siemens
	INPUT_STEPS: List[Second]
	INPUT_VALUES: Dict[str, str]
	INPUT_CLOCKS: Dict[str, str]

@dataclass
class TestbenchParameters:
	SUPPLY_NAME_VDD: str
	SUPPLY_NAME_VSS: str
	SUPPLY_VOLTAGE: Volt
	SUPPLY_SERIES_RESISTANCE: Ohm
	SUPPLY_PARASITIC_CAPACITANCE: Farad
	INPUT_VOLTAGE_HIGH: Volt
	INPUT_VOLTAGE_LOW: Volt
	INPUT_SERIES_RESISTANCE: Ohm
	INPUT_PARASITIC_CAPACITANCE: Farad
	OUTPUT_SERIES_RESISTANCE: Ohm
	OUTPUT_PARASITIC_CAPACITANCE: Farad
	OUTPUT_LEAKAGE_RESISTOR: Ohm

	LOAD_CAPACITANCE: Farad
	INPUT_SETUP_TIME: Second
	INPUT_TRANSITION_TIME: Second
	DELTA_MEASUREMENT: Second
	SIMULATION_STEP: Second
	SIMULATION_GMIN: Siemens

	CELL_DELAY_FACTOR: float
	TRANSITION_DELAY_FACTOR: float
	PROPAGATION_DELAY_FACTOR: float
	CONSTRAINT_FACTOR: float

	TRANSISTOR_MODELS: List[Path] = None
	CELL_MODEL: SubCircuit = None

@dataclass
class FaultParameters:
	# Values for open / bridging / transistor-leak/drive faults inserted into the spi files;
	# need to be strings and in a form recognized by SPICE
	STUCK_RESISTOR_VALUE: Ohm = field(default_factory=lambda: 1@u_Ω)
	OPEN_RESISTOR_VALUE: Ohm = field(default_factory=lambda: 1@u_GΩ)
	SHORT_RESISTOR_VALUE: Ohm = field(default_factory=lambda: 1@u_Ω)
	T_LEAK_RESISTOR_VALUE: Ohm = field(default_factory=lambda: 1@u_Ω)
	T_DRIVE_RESISTOR_VALUE: Ohm = field(default_factory=lambda: 10@u_kΩ)

@dataclass
class MeasurementParameters:
	TRANSITION_START_TIMES: List[Second]
	TRANSITION_END_TIMES: List[Second]
	MEASUREMENT_TIMES: List[Second]

	SUPPLY_VOLTAGE_HIGH: Volt
	SUPPLY_VOLTAGE_LOW: Volt

	# Voltage ranges for the output voltage of the cell
	# voltages < MEASUREMENT_LOW_BOUNDARY are interpreted as logic 0
	# voltages > MEASUREMENT_HIGH_BOUNDARY are interpreted as logic 1
	# everything in between MEASUREMENT_LOW_BOUNDARY and MEASUREMENT_HIGH_BOUNDARY are logic U (Unknown)
	MEASUREMENT_LOW_BOUNDARY: Volt
	MEASUREMENT_HIGH_BOUNDARY: Volt
