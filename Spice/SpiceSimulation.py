from bisect import bisect_left, bisect_right
from logging import warning
from dataclasses import dataclass
from typing import Dict, Any, List, Union, Tuple
from pathlib import Path

import matplotlib.pyplot as pyplot
import numpy
import json
import logging

from PySpice.Unit import u_s, u_V
from PySpice.Unit.Unit import UnitValue
from PySpice.Spice.NgSpice.Shared import NgSpiceCommandError
from PySpice.Spice.Netlist import Circuit
from PySpice.Probe.WaveForm import OperatingPoint, TransientAnalysis

from Spice.SpiceModelInfo import SpiceModelInfo
from Spice.SpiceParameters import SimulationParameters, SimulationType, MeasurementParameters
from Workarounds.NgSpiceSharedSimulatorFix import FixNgSpiceSharedSimulator

# https://matplotlib.org/stable/gallery/lines_bars_and_markers/linestyles.html
STYLE_LIMIT = (0, (1, 2))
STYLE_MEASURE = (0, (1, 2))
STYLE_TRANSITION = (0, (1, 2))
STYLE_INPUTS = (0, (3, 1))
STYLE_CLOCKS = (0, (2, 1))
STYLE_OUTPUTS = 'solid'

COLOR_TRANSITION='gray'
COLOR_MEASURE='red'
COLOR_LIMIT='gray'

logging.getLogger('PySpice.Spice.NgSpice.Shared').setLevel(logging.ERROR)
logging.getLogger('PySpice.Spice.NgSpice.Server').setLevel(logging.ERROR)
logging.getLogger('PySpice.Spice.NgSpice.Simulation').setLevel(logging.WARNING)


class SpiceException(Exception):
	pass

class MeasureException(Exception):
	pass

@dataclass
class SpiceResult:
	timeValues: List[float]
	voltageValues: Dict[str, List[float]]
	currentValues: Dict[str, List[float]]

@dataclass
class SpiceMeasurement:
	timeValues: List[float]
	inputValues: Dict[str, str]
	clockValues: Dict[str, str]
	outputValues: Dict[str, str]
	internalValues: Dict[str, str]


def SpiceSimulate(modelInfo: SpiceModelInfo, testbench: Circuit, simulationParams: SimulationParameters) -> SpiceResult:
	""" This function creates the complete circuit and runs the simulation based on the SIMULATION_TYPE set in Settings.py """
	try:
		simulator = testbench.simulator(simulator='ngspice-shared', temperature=25, nominal_temperature=25)
		# For GMIN parameter explanation see:
		# https://help.simetrix.co.uk/8.3/simetrix/simulator_reference/topics/analogdevicereference_mosfetgminimplementation.htm
		simulator.options(GMIN=simulationParams.SIMULATION_GMIN)
		simulator = FixNgSpiceSharedSimulator(simulator)
	except NgSpiceCommandError:
		warning("First simulation option failed. Trying the other option with ngspice-subprocess")

		try:
			simulator = testbench.simulator(simulator="ngspice-subprocess", temperature=25, nominal_temperature=25)
			simulator.options(gmin=simulationParams.SIMULATION_GMIN)
			simulator = FixNgSpiceSharedSimulator(simulator)
		except NgSpiceCommandError as exception:
			raise SpiceException(f"Simulation failed: Failed to run simulation for this circuit: {modelInfo.name}") \
				from exception

	# Run the simulation
	try:
		if simulationParams.SIMULATION_TYPE == SimulationType.STATIC_OPERATING_POINT:
			result: OperatingPoint = simulator.operating_point()
		elif simulationParams.SIMULATION_TYPE == SimulationType.TRANSITION:
			result: TransientAnalysis = simulator.transient(
				step_time=simulationParams.SIMULATION_STEP,
				end_time=simulationParams.SIMULATION_TIME
			)
		else:
			raise ValueError(f"Unknown simulation type {simulationParams.SIMULATION_TYPE}")
	except NgSpiceCommandError as exception:
		raise SpiceException(f"Simulation failed: Failed to run simulation for this circuit: {modelInfo.name}") \
			from exception

	# Make sure to remove the embedded simulation object as
	# it makes pickling the SpiceResult impossible.
	result._simulation = None

	if isinstance(result, OperatingPoint):
		return SpiceResult(timeValues=[0@u_s], voltageValues=result.nodes, currentValues=result.branches)
	elif isinstance(result, TransientAnalysis):
		return SpiceResult(timeValues=result.time, voltageValues=result.nodes, currentValues=result.branches)


def SpiceMeasure(modelInfo: SpiceModelInfo, spiceResult: SpiceResult, measurementParams: MeasurementParameters) -> SpiceMeasurement:
	def _ToLogic(value):
		return _GetLogicValue(
			value,
			measurementParams.MEASUREMENT_LOW_BOUNDARY,
			measurementParams.MEASUREMENT_HIGH_BOUNDARY
		)

	def _ExtractValues(ports: Dict[str, str]):
		result = dict.fromkeys(ports.keys(), "")
		for port, portName in ports.items():
			for time in measurementParams.MEASUREMENT_TIMES:
				if time > spiceResult.timeValues[-1]:
					# Simulation was aborted prematurely; Assume instability as cause
					result[port] += 'X'
					continue

				# Search for closest point as we might not have an exact match
				index_left = min(bisect_left(spiceResult.timeValues, time), len(spiceResult.timeValues) - 1)
				index_right = min(bisect_right(spiceResult.timeValues, time), len(spiceResult.timeValues) - 1)
				if abs(spiceResult.timeValues[index_left] - time) < abs(spiceResult.timeValues[index_right] - time):
					index = index_left
				else:
					index = index_right
				result[port] += _ToLogic(spiceResult.voltageValues[portName][index])

		return result

	try:
		# Using the testbench ports as they represent how the cell is seen from the outside.
		timeValues = [ float(value) for value in measurementParams.MEASUREMENT_TIMES ]
		inputValues = _ExtractValues({ port: f"in_{port.lower()}" for port in modelInfo.inputs })
		clockValues = _ExtractValues({ port: f"in_{port.lower()}" for port in modelInfo.clocks })
		outputValues = _ExtractValues({ port: f"out_{port.lower()}" for port in modelInfo.outputs })
		internalValues = _ExtractValues({ port: f"x{modelInfo.name.lower()}.{port.lower()}" for port in modelInfo.internal })
		return SpiceMeasurement(timeValues, inputValues, clockValues, outputValues, internalValues)
	except ValueError as exception:
		raise MeasureException("Logic value couldn't be converted to measurement") \
			from exception


# -------------------------------------------------------------------------------------
# Debug output
# -------------------------------------------------------------------------------------

def SpiceSimulationPlot(modelInfo: SpiceModelInfo, spiceResult: SpiceResult, measurementParams: MeasurementParameters, path: Path) -> None:
	""" Plots input and output voltages for debugging. """
	_, ax = pyplot.subplots(figsize=(20, 10))
	ax.set_title(modelInfo.name)
	ax.set_xlabel('Time [s]')
	ax.set_ylabel('Voltage [V]')
	ax.grid()

	times = spiceResult.timeValues
	voltages = spiceResult.voltageValues
	if len(times) == 1:
		# Add pseudo data point to make plotting possible
		times = [times[0], times[0] + 0.001@u_s]
		voltages = { port: [values[0], values[0]] for port, values in voltages.items() }

	x_low = 0.0
	x_high = float(max([
		max(times),
		max(measurementParams.TRANSITION_START_TIMES),
		max(measurementParams.TRANSITION_END_TIMES),
		max(measurementParams.MEASUREMENT_TIMES)
	]))
	x_margins = 0.0

	y_low = float(measurementParams.SUPPLY_VOLTAGE_LOW)
	y_high = float(measurementParams.SUPPLY_VOLTAGE_HIGH)
	y_margins = 0.05

	ax.set_xlim(x_low - (x_high - x_low) * x_margins, x_high + (x_high - x_low) * x_margins)
	ax.set_ylim(y_low - (y_high - y_low) * y_margins, y_high + (y_high - y_low) * y_margins)

	ax.axhline(float(measurementParams.MEASUREMENT_LOW_BOUNDARY), color=COLOR_LIMIT, linestyle=STYLE_LIMIT, label='LOW', clip_on=False)
	ax.axhline(float(measurementParams.MEASUREMENT_HIGH_BOUNDARY), color=COLOR_LIMIT, linestyle=STYLE_LIMIT, label='HIGH', clip_on=False)

	for index, transitionPoint in enumerate(measurementParams.TRANSITION_START_TIMES):
		ax.axvline(float(transitionPoint), color=COLOR_TRANSITION, linestyle=STYLE_TRANSITION, label=f'Transition {index + 1}', clip_on=False)
	for index, transitionPoint in enumerate(measurementParams.TRANSITION_END_TIMES):
		ax.axvline(float(transitionPoint), color=COLOR_TRANSITION, linestyle=STYLE_TRANSITION, clip_on=False)

	for index, measurePoint in enumerate(measurementParams.MEASUREMENT_TIMES):
		ax.axvline(float(measurePoint), color=COLOR_MEASURE, linestyle=STYLE_MEASURE, label=f'Measurement {index + 1}', clip_on=False)

	handles = []
	for port in modelInfo.inputs:
		handle, = ax.plot(numpy.array(times), numpy.array(voltages[f"IN_{port}".lower()]), linestyle=STYLE_INPUTS, label=port)
		handles.append(handle)

	for port in modelInfo.clocks:
		handle, = ax.plot(numpy.array(times), numpy.array(voltages[f"IN_{port}".lower()]), linestyle=STYLE_CLOCKS, label=port)
		handles.append(handle)

	for port in modelInfo.outputs:
		handle, = ax.plot(numpy.array(times), numpy.array(voltages[f"OUT_{port}".lower()]), linestyle=STYLE_OUTPUTS, label=port)
		handles.append(handle)

	ax.legend(handles=handles, loc=(.05,.1))

	if path is None:
		pyplot.show()
	else:
		pyplot.savefig(path)

	pyplot.close()


def SpiceSimulationExport(modelInfo: SpiceModelInfo, spiceResult: SpiceResult, path: Path) -> None:
	""" Exports input and output voltages to JSON for debugging. """
	def _items(dictionary: Dict[Any, Any]) -> Tuple[Any, Any]:
		return sorted(dictionary.items(), key=lambda item: item[0])

	jsonData = {
		'time': _GetUnitValues(spiceResult.timeValues),
		'voltages': dict((name, _GetUnitValues(values)) for name, values in _items(spiceResult.voltageValues)),
		'currents': dict((name, _GetUnitValues(values)) for name, values in _items(spiceResult.currentValues)),
	}
	with open(path, 'wt', encoding='utf-8') as file:
		json.dump(jsonData, file, indent=4)


def SpiceMeasurementPlot(modelInfo: SpiceModelInfo, measureResult: SpiceMeasurement, path: Path) -> None:
	""" Plots input and output values for debugging. """
	_, ax = pyplot.subplots(figsize=(20, 10))
	ax.set_title(modelInfo.name)
	ax.set_xlabel('Time [s]')
	ax.set_ylabel('Value [Logic]')
	ax.grid()

	def _TranslateLogic(values: str) -> List[float]:
		return [{
			'0': 0.0,
			'X': 0.5,
			'1': 1.0
		}.get(value, 0.5) for value in values]

	timeValues = measureResult.timeValues
	inputValues = measureResult.inputValues
	clockValues = measureResult.clockValues
	outputValues = measureResult.outputValues
	if len(timeValues) == 1:
		# Add pseudo data point to make plotting possible
		timeValues = [timeValues[0], timeValues[0] + 0.001@u_s]
		inputValues = { port: [value[0], value[0]] for port, value in inputValues.items() }
		clockValues = { port: [value[0], value[0]] for port, value in clockValues.items() }
		outputValues = { port: [value[0], value[0]] for port, value in outputValues.items() }

	x_low = 0.0
	x_high = float(max(timeValues))
	x_margins = 0.0

	y_low = 0.0
	y_high = 1.0
	y_margins = 0.05

	ax.set_xlim(x_low - (x_high - x_low) * x_margins, x_high + (x_high - x_low) * x_margins)
	ax.set_ylim(y_low - (y_high - y_low) * y_margins, y_high + (y_high - y_low) * y_margins)

	ax.axhline(0.0, color=COLOR_LIMIT, linestyle=STYLE_LIMIT, label='LOW', clip_on=False)
	ax.axhline(1.0, color=COLOR_LIMIT, linestyle=STYLE_LIMIT, label='HIGH', clip_on=False)

	for index, measurePoint in enumerate(timeValues):
		ax.axvline(float(measurePoint), color='red', linestyle=STYLE_MEASURE, label=f'Measurement {index + 1}', clip_on=False)

	handles = []
	for port in inputValues.keys():
		handle, = ax.plot(numpy.array(timeValues), numpy.array(_TranslateLogic(inputValues[port])), linestyle=STYLE_INPUTS, label=port)
		handles.append(handle)

	for port in clockValues.keys():
		handle, = ax.plot(numpy.array(timeValues), numpy.array(_TranslateLogic(clockValues[port])), linestyle=STYLE_CLOCKS, label=port)
		handles.append(handle)

	for port in outputValues.keys():
		handle, = ax.plot(numpy.array(timeValues), numpy.array(_TranslateLogic(outputValues[port])), linestyle=STYLE_OUTPUTS, label=port)
		handles.append(handle)

	ax.legend(handles=handles, loc=(.05,.1))

	if path is None:
		pyplot.show()
	else:
		pyplot.savefig(path)

	pyplot.close()


def SpiceMeasurementExport(modelInfo: SpiceModelInfo, measureResult: SpiceMeasurement, path: Path) -> None:
	""" Export input and output values to JSON for debugging. """
	with open(path, 'wt', encoding='utf-8') as file:
		json.dump({
			"times": measureResult.timeValues,
			"inputs": measureResult.inputValues,
			"clocks": measureResult.clockValues,
			"outputs": measureResult.outputValues
		}, file, indent=4)

# -------------------------------------------------------------------------------------
# Helper
# -------------------------------------------------------------------------------------

def _GetUnitValue(value: Union[float, str, UnitValue]) -> float:
	if isinstance(value, float):
		return value
	elif isinstance(value, UnitValue):
		return float(value)
	elif isinstance(value, str):
		return float([c for c in value if c in "0123456789."])
	else:
		raise ValueError(f"Value is neither of type UnitValue nor float nor string, but of type {type(value)}")

def _GetUnitValues(values: List[Union[float, str, UnitValue]]) -> List[float]:
	return [_GetUnitValue(value) for value in values]

def _GetLogicValue(value, low, high):
	value = _GetUnitValue(value)
	if value <= float(low):
		return '0'
	if value >= float(high):
		return '1'
	return 'X'
