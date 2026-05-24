from liberty.parser import parse_liberty, EscapedString
from collections import defaultdict
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Any, Set
from logging import debug
from math import  prod
from bisect import bisect_left, bisect_right
from itertools import product, chain
from collections.abc import Callable

# Generate missing SI-Units
from Workarounds.MissingSiUnitsFix import _

from PySpice.Unit.SiUnits import Second, Farad
from PySpice.Unit.Unit import UnitValue
from PySpice.Unit import u_s, u_ps, u_ns, u_pF, u_fF

from Logic.Logic import EvaluateEquation, NormalizeEquation
from Logic.Pattern import GetPatternTransitions


@dataclass
class LookupTemplate:
	name: str
	variables: List[str]
	units: List[Any]
	indices: List[List[Any]]

@dataclass
class LookupTable:
	variables: List[str]
	units: List[Any]
	indices: List[List[Any]]
	resultUnit: Any
	resultValues: Any

@dataclass
class TimingArc:
	arcCategory: str
	arcType: str
	srcPin: str
	dstPin: str
	srcTransition: str
	dstTransition: str
	condition: str
	lookupTable: LookupTable

CellTimingInfo = Dict[str, List[TimingArc]]


@dataclass
class LookupInfo:
	minValue: UnitValue
	maxValue: UnitValue

@dataclass
class TimingInfo:
	# Delay and transition times for current timeframe
	cellDelay: UnitValue = field(default_factory=lambda: 0@u_s)
	transitionDelay: UnitValue = field(default_factory=lambda: 0@u_s)
	propagationDelay: UnitValue = field(default_factory=lambda: 0@u_s)

	# Setup and hold constraints from previous timeframes:
	# setup = { 1: 1@u_s, 3: 3@u_s }  # distance from the end of timeframe to start of current timeframe.
	#   - 1 second setup arc (timeframe 1 -> current timeframe): at least 1s distance from tf 1 to current
	#   - 3 seconds setup arc (timeframe 3 -> current timeframe): at least 3s distance from tf 1 to current
	# hold = { 1: 1@u_s, 3: 3@u_s }
	#   - 1 second hold arc (timeframe 1 -> current timeframe): at least 1s distance from tf 1 to current
	#   - 3 seconds hold arc (timeframe 3 -> current timeframe): at least 3s distance from tf 3 to current
	setupConstraints: Dict[int, UnitValue] = field(default_factory=lambda: defaultdict(lambda: 0@u_s))
	holdConstraints: Dict[int, UnitValue] = field(default_factory=lambda: defaultdict(lambda: 0@u_s))


ARCS_DELAY      = ['cell_rise', 'cell_fall', 'rise_transition', 'fall_transition', 'rise_propagation', 'fall_propagation']
ARCS_CONSTRAINT = ['rise_constraint', 'fall_constraint']

ARCS_SETUP_CONSTRAINT  = ['setup_rising', 'setup_falling', 'non_seq_setup_rising', 'non_seq_setup_falling']
ARCS_HOLD_CONSTRAINT   = ['hold_rising',  'hold_falling',  'non_seq_hold_rising',  'non_seq_hold_falling']

ARCS_SETUP_CONSTRAINT += ['removal_rising', 'removal_falling', 'recovery_rising', 'recovery_falling']
ARCS_HOLD_CONSTRAINT  += ['minimum_period', 'min_pulse_width']                          # All pin
ARCS_HOLD_CONSTRAINT  += ['min_period', 'min_pulse_width_low', 'min_pulse_width_high']  # Clock pins

UNIT_TIME = {
	'1ps': 1@u_ps,
	'10ps': 10@u_ps,
	'100ps': 100@u_ps,
	'1ns': 1@u_ns,
}
UNIT_LOAD_CAPACITY = {
	'ff': 1@u_fF,
	'pf': 1@u_pF,
}


def _SplitList(values, chunkSize):
	for i in range(0, len(values), chunkSize):
		yield values[i:i+chunkSize]

def _GetValue(name):
	return name.value if isinstance(name, EscapedString) else name

def _GetRow(row):
	return [float(value) for value in _GetValue(row).split(',')]

def _GetUnit(library, name, units, defaultValue):
	if name not in library:
		return defaultValue

	definition = library[name]
	if isinstance(definition, str) or isinstance(definition, EscapedString):
		return units[_GetValue(definition)]
	else:
		return float(_GetValue(definition[0])) * units[_GetValue(definition[1])]


def ImportLibertyTiming(path: Path, cellFilter: Callable[[str], bool] = None) -> Dict[str, List[TimingArc]]:
	def _GetTemplate(group) -> LookupTemplate:
		template = LookupTemplate(_GetValue(group.args[0]), [], [], [])
		i = 1
		while f'variable_{i}' in group:
			variable = _GetValue(group[f'variable_{i}'])
			index = _GetRow(group[f'index_{i}'][0])
			unit = {
				# cell_rise, cell_fall, rise_transition, fall_transition
				'input_net_transition': timeUnit,
				'total_output_net_capacitance': capacitiveLoadUnit,
				# rise_constraint, fall_constraint
				'constrained_pin_transition': timeUnit,
				'related_pin_transition': timeUnit,
				'related_out_total_output_net_capacitance': capacitiveLoadUnit,
			}.get(variable)
			template.variables += [variable]
			template.units += [unit]
			template.indices += [index]
			i += 1
		return template

	def _GetLookupTable(group):
		templateName = _GetValue(group.args[0]) if len(group.args) > 0 else None
		if templateName is not None:
			template = lookupTemplates.get(templateName)
		else:
			template = _GetTemplate(group)

		indices = []  # Indices from template can be overwritten
		for i, index in enumerate(template.indices):
			if f'index_{i+1}' in group:
				indices += [_GetRow(group[f'index_{i+1}'][0])]
			else:
				indices += [index]

		# Reshape the list into a multi-dimensional structure:
		# index_1 (outermost rows)
		#   |  +------------------------------------+
		#   |  | index_2 ----> (2nd outermost rows) |
		#   |  | +--+ +--+ +--+ +--+ +--+ +--+ +--+ |
		#   |  | +i3+ +i3+ +i3+ +i3+ +i3+ +i3+ +i3+ |
		#   |  | +--+ +--+ +--+ +--+ +--+ +--+ +--+ |
		#   |  +------------------------------------+
		#   |  | index_2 -------------------------> |
		#   |  | +--+ +--+ +--+ +--+ +--+ +--+ +--+ |
		#   |  | +i3+ +i3+ +i3+ +i3+ +i3+ +i3+ +i3+ |
		#   |  | +--+ +--+ +--+ +--+ +--+ +--+ +--+ |
		#   V  +------------------------------------+
		values = list(chain(*[_GetRow(row) for row in group['values']]))
		for index in reversed(indices[1:]):
			values = list(_SplitList(values, len(index)))

		if len(indices) == 0:
			values = values[0]

		return LookupTable(
			variables=template.variables,
			units=template.units,
			indices=indices,
			resultUnit=timeUnit,
			resultValues=values
		)

	with open(path, 'rt', encoding='utf-8') as file:
		library = parse_liberty(file.read())

	timeUnit           = _GetUnit(library, 'time_unit',            UNIT_TIME,          1@u_ns)
	capacitiveLoadUnit = _GetUnit(library, 'capacitive_load_unit', UNIT_LOAD_CAPACITY, None)

	lookupTemplates: Dict[str, LookupTemplate] = {
		'scalar': LookupTemplate('scalar', [], [], [])
	}
	for template in library.get_groups('lu_table_template'):
		templateName = _GetValue(template.args[0])
		lookupTemplates[templateName] = _GetTemplate(template)

	cells: Dict[str, CellTimingInfo] = {}
	for cell in library.get_groups('cell'):
		cellName = _GetValue(cell.args[0])
		if cellFilter is not None and not cellFilter(cellName):
			continue

		#debug(f'  Reading timing arcs for cell {cellName:<3}:')

		pinToPinArcs: Dict[Tuple[str, str], List[TimingArc]] = defaultdict(list)
		def _AddTimingArc(srcPin: str, srcTransition: str, dstPin: str, dstTransition, condition: str, arcCategory: str, arcType: str, lookupTable: LookupTable):
			#direction = '<--' if arcType in ['rise_constraint', 'fall_constraint'] else '-->'
			#debug(f'\x1b[31m\x1b[1m    {srcPin:<3} {srcTransition:<7} {direction} {dstPin:<3} {dstTransition:<7} ({arcCategory}, {arcType}, cond: {condition})\x1b[0m')
			pinToPinArcs[(srcPin, dstPin)].append(TimingArc(arcCategory, arcType, srcPin, dstPin, srcTransition, dstTransition, condition, lookupTable))

		def _AddNoTimingArc(srcPin: str, srcTransition: str, dstPin: str, dstTransition, condition: str, arcCategory: str, arcType: str):
			#debug(f'\x1b[34m  {srcPin:<3} {srcTransition:<7}     {dstPin:<3} {dstTransition:<7} ({arcCategory}, {arcType}, cond: {condition})\x1b[0m')
			pass

		for pin in cell.get_groups('pin'):
			targetPin = _GetValue(pin.args[0])

			for timingType, timingArcs in [
				('min_period',           [('rise_constraint', 'rising' , 'rising' ), ('fall_constraint', 'falling', 'falling')]),
				('min_pulse_width_low',  [('fall_constraint', 'falling', 'rising' )]),
	 			('min_pulse_width_high', [('rise_constraint', 'rising' , 'falling')]),
			]:
				if timingType in pin:
					timeValue = float(_GetValue(pin[timingType]))
					lookupTable = LookupTable(variables=[], units=[], indices=[], resultUnit=timeUnit, resultValues=timeValue)
					for arcType, srcTransition, dstTransition in timingArcs:
						_AddTimingArc(targetPin, srcTransition, targetPin, dstTransition, '1', timingType, arcType, lookupTable)

			for timingType, timingAttributes in [
				('minimum_period', [
					('constraint',      [('rise_constraint', 'rising', 'rising' ), ('fall_constraint', 'falling', 'falling')])
				]),
				('min_pulse_width', [
					('constraint_low',  [('fall_constraint', 'falling', 'rising' )]),
					('constraint_high', [('rise_constraint', 'rising' , 'falling')])
				])
			]:
				for timing in pin.get_groups(timingType):
					for timingAttibute, timingArcs in timingAttributes:
						if timingAttibute in timing:
							timeValue = float(_GetValue(timing[timingAttibute]))
							lookupTable = LookupTable(variables=[], units=[], indices=[], resultUnit=timeUnit, resultValues=timeValue)
							condition = NormalizeEquation(_GetValue(timing['when'] if 'when' in timing else '1'))
							for arcType, srcTransition, dstTransition in timingArcs:
								_AddTimingArc(targetPin, srcTransition, targetPin, dstTransition, condition, timingType, arcType, lookupTable)

			for timing in pin.get_groups('timing'):
				relatedPin = _GetValue(timing['related_pin'] if 'related_pin' in timing else targetPin)
				condition = NormalizeEquation(_GetValue(timing['when'] if 'when' in timing else '1'))

				timingSense = _GetValue(timing['timing_sense'] if 'timing_sense' in timing else 'non_unate')
				timingType = _GetValue(timing['timing_type'] if 'timing_type' in timing else 'combinational')

				# https://people.eecs.berkeley.edu/~alanmi/publications/other/liberty07_03.pdf
				# https://media.c3d2.de/mgoblin_media/media_entries/659/Liberty_User_Guides_and_Reference_Manual_Suite_Version_2017.06.pdf
				# https://users.cs.utah.edu/~elb/cadbook/color-figs/Chapter8-Char.pdf
				# Reference 7-26: timing_type Simple Attribute
				(implemented, _, relPos, relNeg, relNon, _, tarPos, tarNeg, tarNon), allowedArcs = {
					# ----------------------------------------------------------
					# Combinational timing arcs (related pin --> target pin)
					# ----------------------------------------------------------

					# - The signal propagation timing arc is used is some libraries instead of combinational.
					# - The combinational timing arc is sensitive to both edges.
					'signal_propagation':       ('y BBB +-*', ARCS_DELAY),
					'combinational':            ('y BBB +-*', ARCS_DELAY),
					# - Rise / fall referring to output (target pin) signal.
					'combinational_rise':       ('y RFB RRR', ARCS_DELAY),
					'combinational_fall':       ('y FRB FFF', ARCS_DELAY),
					'three_state_disable':      ('y RFB BBB', ARCS_DELAY),
					'three_state_enable':       ('y RFB BBB', ARCS_DELAY),
					'three_state_disable_rise': ('y RFB RRR', ARCS_DELAY),
					'three_state_disable_fall': ('y RFB FFF', ARCS_DELAY),
					'three_state_enable_rise':  ('y RFB RRR', ARCS_DELAY),
					'three_state_enable_fall':  ('y RFB FFF', ARCS_DELAY),
					# - Rise / fall referring to clock (related pin) signal.
					# - Rise / fall unateness is the internal state (function) vs output (target pin) (IQ -?> Q).
					'rising_edge':              ('y RRR BBB', ARCS_DELAY),
					'falling_edge':             ('y FFF BBB', ARCS_DELAY),
					# - Rise / fall referring to clock (related pin) signal.
					'non_seq_setup_rising':     ('y RRR RFB', ARCS_CONSTRAINT),
					'non_seq_setup_falling':    ('y FFF FRB', ARCS_CONSTRAINT),
					'non_seq_hold_rising':      ('y RRR RFB', ARCS_CONSTRAINT),
					'non_seq_hold_falling':     ('y FFF FRB', ARCS_CONSTRAINT),

					# ----------------------------------------------------------
					# Sequential timing arcs    (related pin --> target pin)
					# ----------------------------------------------------------

					# - Preset / clear are similar to combinational timing arcs (sensitive to both edges).
					# - Preset / clear referring to output (target pin) signal.
					'preset':                   ('y BBB +-*', ARCS_DELAY),
					'clear':                    ('y BBB +-*', ARCS_DELAY),
					# - Rise / fall referring to clock (related pin) signal.
					'setup_rising':             ('y RRR RFB', ARCS_CONSTRAINT),
					'setup_falling':            ('y FFF FRB', ARCS_CONSTRAINT),
					'hold_rising':              ('y RRR RFB', ARCS_CONSTRAINT),
					'hold_falling':             ('y FFF FRB', ARCS_CONSTRAINT),
					'recovery_rising':          ('y RRR RFB', ARCS_CONSTRAINT),
					'recovery_falling':         ('y FFF FRB', ARCS_CONSTRAINT),
					'removal_rising':           ('y RRR RFB', ARCS_CONSTRAINT),
					'removal_falling':          ('y FFF FRB', ARCS_CONSTRAINT),
					# - Pulse is constrained via transition -> opposite transition.
					# - Period is constrained via transition -> equal transition.
					'min_pulse_width':          ('y BBB ---', ARCS_CONSTRAINT),
					'minimum_period':           ('y BBB +++', ARCS_CONSTRAINT),

					# ----------------------------------------------------------
					# Unimplemented timing arcs
					# ----------------------------------------------------------

					'skew_rising':              ('i ___ ___', None),
					'skew_falling':             ('i ___ ___', None),
					'max_clock_tree_path':      ('i ___ ___', None),
					'min_clock_tree_path':      ('i ___ ___', None),
					'nochange_high_high':       ('e ___ ___', None),
					'nochange_high_low':        ('e ___ ___', None),
					'nochange_low_high':        ('e ___ ___', None),
					'nochange_low_low':         ('e ___ ___', None),
				}.get(timingType)
				if implemented == 'y':
					pass
				elif implemented == 'i':
					continue  # Ignore not implemented as not relevant for cell simulation
				else:
					raise ValueError(f'Timing type {timingType} is not implemented and will lead to a wrong simulation')

				srcSensitivity, dstSensitivity = {
					'positive_unate': (relPos, tarPos),
					'negative_unate': (relNeg, tarNeg),
					'non_unate':      (relNon, tarNon),
				}.get(timingSense)

				arcTransitions = {
					('R', 'R'): [('rising',  'rising' )],
					('R', 'F'): [('rising',  'falling')],
					('R', 'B'): [('rising',  'rising' ), ('rising',  'falling')],
					('F', 'R'): [('falling', 'rising' )],
					('F', 'F'): [('falling', 'falling')],
					('F', 'B'): [('falling', 'rising' ), ('falling', 'falling')],
					('B', 'R'): [('rising',  'rising' ), ('falling', 'rising' )],
					('B', 'F'): [('rising',  'falling'), ('falling', 'falling')],
					('B', '+'): [('rising',  'rising' ), ('falling', 'falling')],
					('B', '-'): [('rising',  'falling'), ('falling', 'rising' )],
					('B', '*'): [
						('rising',  'rising' ), ('rising',  'falling'),
						('falling', 'rising' ), ('falling', 'falling')
					],
					('B', 'B'): [
						('rising',  'rising' ), ('rising',  'falling'),
						('falling', 'rising' ), ('falling', 'falling')
					],
				}.get((srcSensitivity, dstSensitivity))

				# Pre-load the required timing arc tables to save memory:
				# If a timing arc is sensitive to both related transitions
				# it would otherwise be duplicated and read twice.
				# Example: B -> * (rising and falling table used twice)
				lookupTables = {}
				for arcType in allowedArcs:
					for group in timing.get_groups(arcType):
						lookupTables[arcType] = _GetLookupTable(group)

				for relatedTransition, targetTransition in arcTransitions:
					arcTypes = {
						'rising':  [arc for arc in allowedArcs if 'rise' in arc],
						'falling': [arc for arc in allowedArcs if 'fall' in arc],
					}.get(targetTransition)
					for arcType in arcTypes:
						if arcType in lookupTables:
							_AddTimingArc(relatedPin, relatedTransition, targetPin, targetTransition, condition, timingType, arcType, lookupTables[arcType])
						else:
							_AddNoTimingArc(relatedPin, relatedTransition, targetPin, targetTransition, condition, timingType, arcType)

		cells[cellName] = defaultdict(list)
		for (source, _), timingArcs in pinToPinArcs.items():
			cells[cellName][source].extend(timingArcs)

	return cells

def GetLookupConstraints(cellTiming: CellTimingInfo) -> Dict[str, LookupInfo]:
	result: Dict[str, LookupInfo] = {}

	for pinToPinTimingInfos in cellTiming.values():
		for timingArc in pinToPinTimingInfos:
			lookupTable = timingArc.lookupTable
			for variable, index, unit in zip(lookupTable.variables, lookupTable.indices, lookupTable.units):
				if variable not in result:
					result[variable] = LookupInfo(
						minValue=0 * unit,
						maxValue=float('inf') * unit
					)

				result[variable].minValue = max(result[variable].minValue, index[0] * unit)
				result[variable].maxValue = min(result[variable].maxValue, index[-1] * unit)

	return result

def GetTimingConstraints(cellName: str, cellTiming: CellTimingInfo, inputTransitionTime: UnitValue, loadCapacitance: UnitValue, timeframes: Dict[str, str]) -> List[TimingInfo]:
	constraints = {
		# cell_rise, cell_fall, rise_transition, fall_transition
		'input_net_transition': inputTransitionTime,
		'total_output_net_capacitance': loadCapacitance,
		# rise_constraint, fall_constraint
		'constrained_pin_transition': inputTransitionTime,
		'related_pin_transition': inputTransitionTime,
		'related_out_total_output_net_capacitance': loadCapacitance,
	}

	# Setup delay handling: The setup delay is accounted for in the timeframe the clock pin transitions.
	# Stores sender (data pin, edge) <- receiver (clock pin, edge) received information.
	# The receiver "pulls" and deactivates each of the previous pin transitions that match a setup constraint.
	# Sent (Data Pin):      { (src pin, src edge): (timeframe, set()) }                        (resets all acks)
	# Received (Clock Pin): { (src pin, src edge): (timeframe, { (dst pin, dst edge) }) }      (sets  received flag)
	scheduleSetupDelay: Dict[Tuple[str, str], Tuple[int, Set[Tuple[str, str]]]] = {}

	# Hold delay handling: The hold delay is accounted for in the timeframe the data pin transitions.
	# Stores receiver (pin, edge) <- delay information from sender (pin, edge)
	# The sender "pushes" the delay onto the receiver, which then applies the delays on next transition.
	# Sent (Clock Pin):     { (dst pin, dst edge): [(timeframe, arc, delay), ...] }
	# Received (Data Pin):  { }
	scheduleHoldDelay: Dict[Tuple[str, str], List[Tuple[int, TimingArc, UnitValue]]] = defaultdict(list)

	result: List[TimingInfo] = []
	for timeframe in range(0, len(timeframes)):
		timing = TimingInfo()
		result.append(timing)

		previousTimeframe = timeframes[timeframe - 1] if timeframe > 0 else { port: 'X' for port in timeframes[0].keys() }
		currentTimeframe = timeframes[timeframe]
		transitions = GetPatternTransitions(previousTimeframe, currentTimeframe)

		# Setup delay handling: Reset all ack flags as these are new transitions
		for srcPin, srcTransition in transitions.items():
			scheduleSetupDelay[(srcPin, srcTransition)] = (timeframe, set())

		# Hold delay handling: Apply all hold times for the transitions
		for dstPin, dstTransition in transitions.items():
			if (dstPin, dstTransition) in scheduleHoldDelay:
				for start, timingArc, delay in scheduleHoldDelay[(dstPin, dstTransition)]:
					timing.holdConstraints[start] = max(timing.holdConstraints[start], delay)
				del scheduleHoldDelay[(dstPin, dstTransition)]

		# Delay setting acknowledge flags as there might be multiple constraint arcs
		# for the same transition that need to be handled in this timeframe.
		acknowledgedSetupDelay: Dict[Tuple[str, str], Set[Tuple[str, str]]] = defaultdict(set)
		for srcPin, srcTransition in transitions.items():
			for timingArc in cellTiming[srcPin]:
				if srcTransition != timingArc.srcTransition:
					continue
				if EvaluateEquation(timingArc.condition, currentTimeframe) != '1':
					continue

				delay = _InterpolateDelay(cellName, srcPin, timingArc.lookupTable, **constraints, interpolation='cubic')
				if timingArc.arcType in ['cell_rise', 'cell_fall']:
					timing.cellDelay = max(timing.cellDelay, delay)
				elif timingArc.arcType in ['rise_transition', 'fall_transition']:
					timing.transitionDelay = max(timing.transitionDelay, delay)
				elif timingArc.arcType in ['rise_propagation', 'fall_propagation']:
					timing.propagationDelay = max(timing.propagationDelay, delay)
				elif timingArc.arcType in ['rise_constraint', 'fall_constraint']:
					dstPin, dstTransition = timingArc.dstPin, timingArc.dstTransition
					if timingArc.arcCategory in ARCS_SETUP_CONSTRAINT:
						if (dstPin, dstTransition) not in scheduleSetupDelay:
							continue

						startTimeframe, acknowledged = scheduleSetupDelay[(dstPin, dstTransition)]
						if (srcPin, srcTransition) in acknowledged:
							continue

						timing.setupConstraints[startTimeframe] = max(timing.setupConstraints[startTimeframe], delay)
						acknowledgedSetupDelay[(dstPin, dstTransition)].add((srcPin, srcTransition))

					if timingArc.arcCategory in ARCS_HOLD_CONSTRAINT:
						scheduleHoldDelay[(dstPin, dstTransition)].append((timeframe, timingArc, delay))
						continue

				#debug(f'    {srcPin} {srcTransition} ({previousTimeframe[srcPin]} -> {currentTimeframe[srcPin]})'
				#	f' -> {timingArc.dstPin} {timingArc.dstTransition} ({timingArc.arcCategory}, {timingArc.arcType}, cond: {timingArc.condition}):'
				#	f' {loadCapacitance} cap, {inputTransitionTime} trans => {delay} delay')

		for (dstPin, dstTransition), acknowledged in acknowledgedSetupDelay.items():
			startTimeframe, acknowledged = scheduleSetupDelay[(dstPin, dstTransition)]
			acknowledged.update(acknowledged)

	return result


def _InterpolateDelay(cellName: str, portName: str, lookup: LookupTable, interpolation='cubic', **kwargs) -> UnitValue:
	""" Interpolates the timing tables with one or multiple dimensions.

	The following interpolation types are supported: 'linear', 'cubic', 'nearest-above'
	"""
	def _GetValue(indices):
		result = lookup.resultValues
		for index in indices:
			result = result[index]
		return result

	names = []
	dims = []
	units = []
	params = []
	for variable, unit, index in zip(lookup.variables, lookup.units, lookup.indices):
		if variable not in kwargs:
			raise ValueError(f'Expected parameter {variable} in parameters for lookup table')
		names += [variable]
		dims += [index]
		units += [unit]
		params += [float(kwargs[variable] / unit)]

	for index, (name, dimension, unit, parameter) in enumerate(zip(names, dims, units, params)):
		# Small deviations are allowed to account for floating point inaccuracies
		epsilon = (dimension[-1] - dimension[0]) * 0.001
		if parameter < (dimension[0] - epsilon) or parameter > (dimension[-1] + epsilon):
			raise ValueError(f'Input parameter {name} value {parameter} of port {portName} of cell {cellName} outside of range {dimension[0]} to {dimension[-1]} of {unit}')

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
		delaySum = 0.0
		indicesPerDimension = [list(weightMap.keys()) for weightMap in weightsPerDimension]
		for indices in product(*indicesPerDimension):
			weights = [dimensionWeights[index] for index, dimensionWeights in zip(indices, weightsPerDimension)]
			delaySum += _GetValue(indices) * prod(weights)

	elif interpolation == 'nearest-above':
		delaySum = _GetValue([min(bisect_right(dim, param), len(dim) - 1) for dim, param in zip(dims, params)])

	else:
		raise ValueError('Invalid interpolation type')

	return delaySum * lookup.resultUnit
