from liberty.parser import parse_liberty, EscapedString
from collections import defaultdict
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Union, Type, TypeVar, Generic
from logging import debug
from math import  prod
from bisect import bisect_left, bisect_right
from itertools import product

# Generate missing SI-Units
from Workarounds.MissingSiUnitsFix import _

from PySpice.Unit.SiUnits import Second, Farad
from PySpice.Unit.Unit import UnitValue
from PySpice.Unit import u_ns, u_fF, u_s

from Logic.Logic import EvaluateEquation, NormalizeEquation


@dataclass
class TransitionTimingPoints:
	inputTransitions: List[Second]
	outputDelays: List[Second]

	def GetDimensions(self):
		return [self.inputTransitions]

	def GetValue(self, indices: List[int]):
		return self.outputDelays[indices[0]]

@dataclass
class TransitionAndCapacitanceTimingPoints:
	inputTransitions: List[Second]
	loadCapacitances: List[Farad]
	outputDelays: List[List[Second]]

	def GetDimensions(self):
		return [self.inputTransitions, self.loadCapacitances]

	def GetValue(self, indices: List[int]):
		return self.outputDelays[indices[0]][indices[1]]

TTimingPoints = TypeVar("TTimingPoints")

@dataclass
class TimingTable(Generic[TTimingPoints]):
	condition: str
	inputRise: TTimingPoints
	inputFall: TTimingPoints

TransitionTimingTable = TimingTable[TransitionTimingPoints]
TransitionAndCapacitanceTimingTable = TimingTable[TransitionAndCapacitanceTimingPoints]

@dataclass
class PinTimingInfo:
	source: str
	destination: str
	propagationDelay: List[TransitionAndCapacitanceTimingTable]
	transitionDelay: List[TransitionAndCapacitanceTimingTable]
	transitionConstraint: List[TransitionTimingTable]

TimingTables = Union[TransitionTimingTable, TransitionAndCapacitanceTimingTable]
TimingPoints = Union[TransitionTimingPoints, TransitionAndCapacitanceTimingPoints]
CellTimingInfo = Dict[str, List[PinTimingInfo]]

@dataclass
class PortTimingInfo:
	propagationDelay: UnitValue = field(default_factory=lambda: 0@u_s)
	transitionDelay: UnitValue = field(default_factory=lambda: 0@u_s)
	transitionConstraint: UnitValue = field(default_factory=lambda: 0@u_s)


def ImportLibertyTiming(path: Path) -> Dict[str, CellTimingInfo]:
	def _GetValue(name):
		return name.value if isinstance(name, EscapedString) else name

	def _GetRow(row):
		return [float(value) for value in _GetValue(row).split(',')]

	def _GetTimingPoints(timing, name, point_type: Type):
		groups = timing.get_groups(name)
		if len(groups) == 0:
			return []

		if point_type == TransitionAndCapacitanceTimingPoints:
			inputTransitions = _GetRow(groups[0]['index_1'][0])
			loadCapacitances = _GetRow(groups[0]['index_2'][0])
			return TransitionAndCapacitanceTimingPoints(
				inputTransitions=[delay@u_ns for delay in inputTransitions],
				loadCapacitances=[capacitance@u_fF for capacitance in loadCapacitances],
				outputDelays=[
					[ delay@u_ns for delay in _GetRow(groups[0]['values'][row]) ]
					for row in range(len(inputTransitions))
				]
			)

		elif point_type == TransitionTimingPoints:
			inputTransitions = _GetRow(groups[0]['index_1'][0])
			outputDelays = _GetRow(groups[0]['values'][0])
			return TransitionTimingPoints(
				inputTransitions=[delay@u_ns for delay in inputTransitions],
				outputDelays=[delay@u_ns for delay in outputDelays]
			)


	with open(path, 'rt', encoding='utf-8') as file:
		library = parse_liberty(file.read())

	cells: Dict[str, CellTimingInfo] = {}
	for cell in library.get_groups('cell'):
		cellName = _GetValue(cell.args[0])

		propagationTimings: Dict[str, List[TransitionAndCapacitanceTimingTable]] = defaultdict(list)
		transitionTimings: Dict[str, List[TransitionAndCapacitanceTimingTable]] = defaultdict(list)
		transitionConstraints: Dict[str, List[TransitionTimingTable]] = defaultdict(list)
		for pin in cell.get_groups('pin'):
			pinDestName = _GetValue(pin.args[0])

			for timing in pin.get_groups('timing'):
				# Some libraries don't specify 'signal_propagation' if they provide timing for pin-to-pin delays
				# => We assume 'signal_propagation' in this case and only add entries to the propagation tables
				#    in case the correct groups exist anyways.
				timingType = _GetValue(timing['timing_type'] if 'timing_type' in timing else 'signal_propagation')
				pinSourceName = _GetValue(timing['related_pin'] if 'related_pin' in timing else pinDestName)
				condition = _GetValue(timing['when'] if 'when' in timing else '1')
				condition = NormalizeEquation(condition)
				if timingType not in [
					'signal_propagation',
					'rising_edge', 'falling_edge', 'hold_rising', 'hold_falling', 'setup_rising', 'setup_falling',
					'min_pulse_width',
				]:
					continue

				debug(f'    Reading timing {cellName}: {pinSourceName} -> {pinDestName} ({timingType})')

				# For timing_type: signal_propagation
				if timing.get_groups('cell_rise'):
					propagationTimings[(pinSourceName, pinDestName)] += [
						TransitionAndCapacitanceTimingTable(
							condition=condition,
							inputRise=_GetTimingPoints(timing, 'cell_rise', TransitionAndCapacitanceTimingPoints),
							inputFall=_GetTimingPoints(timing, 'cell_fall', TransitionAndCapacitanceTimingPoints),
						)]

				# For timing_type: rising_edge, falling_edge, hold_rising, hold_falling, setup_rising and setup_falling
				if timing.get_groups('rise_transition'):
					transitionTimings[(pinSourceName, pinDestName)] += [
						TransitionAndCapacitanceTimingTable(
							condition=condition,
							inputRise=_GetTimingPoints(timing, 'rise_transition', TransitionAndCapacitanceTimingPoints),
							inputFall=_GetTimingPoints(timing, 'fall_transition', TransitionAndCapacitanceTimingPoints),
						)]

				# For timing_type: min_pulse_width
				if timing.get_groups('rise_constraint'):
					transitionConstraints[(pinSourceName, pinDestName)] += [
						TransitionTimingTable(
							condition=condition,
							inputRise=_GetTimingPoints(timing, 'rise_constraint', TransitionTimingPoints),
							inputFall=_GetTimingPoints(timing, 'fall_constraint', TransitionTimingPoints),
						)]

		pinPairs = list(sorted(set([*propagationTimings.keys(), *transitionTimings.keys(), *transitionConstraints.keys()])))
		cells[cellName] = defaultdict(list)
		for source, destination in pinPairs:
			cells[cellName][source].append(
				PinTimingInfo(
					source=source,
					destination=destination,
					propagationDelay=propagationTimings.get((source, destination), []),
					transitionDelay=transitionTimings.get((source, destination), []),
					transitionConstraint=transitionConstraints.get((source, destination), []),
				)
			)

	return cells


def GetPortTimingInfo(cellName: str, cellTiming: CellTimingInfo, inputPort: str, inputTransition: str, inputTransitionTime: UnitValue, loadCapacitance: UnitValue, constraints: Dict[str, str]) -> Dict[str, PortTimingInfo]:
	if not inputPort in cellTiming:
		return {}

	portTimingInfos = defaultdict(lambda: PortTimingInfo())
	for timingInfo in cellTiming[inputPort]:
		propagationDelay = [table for table in timingInfo.propagationDelay if EvaluateEquation(table.condition, constraints) == '1']
		transitionDelay = [table for table in timingInfo.transitionDelay if EvaluateEquation(table.condition, constraints) == '1']
		transitionConstraint = [table for table in timingInfo.transitionConstraint if EvaluateEquation(table.condition, constraints) == '1']

		if len(propagationDelay) > 1:
			raise ValueError(f'There are multiple matching values for propagation delay for port {inputPort} of cell {cellName} with {inputTransition} transition')
		if len(transitionDelay) > 1:
			raise ValueError(f'There are multiple matching values for transition delay for port {inputPort} of cell {cellName} with {inputTransition} transition')
		if len(transitionConstraint) > 1:
			raise ValueError(f'There are multiple matching values for transition constraint for port {inputPort} of cell {cellName} with {inputTransition} transition')

		if inputTransition == 'rising':
			propagationDelay = [table.inputRise for table in propagationDelay]
			transitionDelay = [table.inputRise for table in transitionDelay]
			transitionConstraint = [table.inputRise for table in transitionConstraint]
		elif inputTransition == 'falling':
			propagationDelay = [table.inputFall for table in propagationDelay]
			transitionDelay = [table.inputFall for table in transitionDelay]
			transitionConstraint = [table.inputFall for table in transitionConstraint]
		else:
			raise ValueError(f'Invalid transition {inputTransition}')

		output = timingInfo.destination
		if len(propagationDelay) == 1:
			portTimingInfos[output].propagationDelay = _InterpolateDelay(cellName, inputPort, propagationDelay[0], inputTransitionTime, loadCapacitance)
		if len(transitionDelay) == 1:
			portTimingInfos[output].transitionDelay = _InterpolateDelay(cellName, inputPort, transitionDelay[0], inputTransitionTime, loadCapacitance)
		if len(transitionConstraint) == 1:
			portTimingInfos[output].transitionConstraint = _InterpolateDelay(cellName, inputPort, transitionConstraint[0], inputTransitionTime)

	return portTimingInfos


def _InterpolateDelay(cellName: str, portName: str, points: TimingPoints, *params: List[UnitValue], interpolation='cubic') -> UnitValue:
	""" Interpolates the timing tables with one or multiple dimensions.

	The following interpolation types are supported: 'linear', 'cubic', 'nearest-above'
	"""
	dims = points.GetDimensions()
	if len(dims) != len(params):
		raise ValueError(f'Expected {len(dims)} parameters, but got only {len(params)}')

	for index, (dimension, parameter) in enumerate(zip(dims, params)):
		if parameter < dimension[0] or parameter > dimension[-1]:
			raise ValueError(f'Input parameter {index} value {parameter} of port {portName} of cell {cellName} outside of range {dimension[0]} to {dimension[1]}')

	if interpolation in ['linear', 'cubic']:
		weightsPerDimension = []
		for dim, param in zip(dims, params):
			indexLow = bisect_right(dim, param) - 1
			indexHigh = min(bisect_left(dim, param), len(dim) - 1)
			if indexHigh == indexLow:
				t = 0.0
			else:
				t = (param - dim[indexLow]) / (dim[indexHigh] - dim[indexLow])

			if interpolation == 'linear':
				# Calculate weights using matrix:
				# https://en.wikipedia.org/wiki/Bilinear_interpolation#On_the_unit_square
				weights = [ 1.0 - t, t ]
				indices = [ indexLow, indexHigh ]

			elif interpolation == 'cubic':
				# Calculate weights using Catmull-Rom spline:
				# https://en.wikipedia.org/wiki/Cubic_Hermite_spline#Interpolation_on_the_unit_interval_with_matched_derivatives_at_endpoints
				w0 = 0.5 * (-t**3 + 2*t**2 - t)
				w1 = 0.5 * (3*t**3 - 5*t**2 + 2)
				w2 = 0.5 * (-3*t**3 + 4*t**2 + t)
				w3 = 0.5 * (t**3 - t**2)
				weights = [ w0, w1, w2, w3 ]
				indices = [ max(0, indexLow - 1), indexLow, indexHigh, min(indexHigh + 1, len(dim) - 1) ]

			# Combine weights for repeated indices (at boundaries)
			weightMap = defaultdict(float)
			for index, weight in zip(indices, weights):
				weightMap[index] += weight
			weightsPerDimension.append(weightMap)

		# Sum the product of all weights with retrieved values
		delaySum = 0@u_s
		indicesPerDimension = [list(weightMap.keys()) for weightMap in weightsPerDimension]
		for indices in product(*indicesPerDimension):
			weights = [dimensionWeights[index] for index, dimensionWeights in zip(indices, weightsPerDimension)]
			delaySum += points.GetValue(indices) * prod(weights)
		return delaySum

	elif interpolation == 'nearest-above':
		return points.GetValue([min(bisect_right(dim, param), len(dim) - 1) for dim, param in zip(dims, params)])

	else:
		raise ValueError('Invalid interpolation type')
