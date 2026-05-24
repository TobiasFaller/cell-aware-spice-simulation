from bisect import bisect_left, bisect_right
from logging import warning
from dataclasses import dataclass
from typing import Dict, Any, List, Union, Tuple
from pathlib import Path

import matplotlib.pyplot as pyplot
import matplotlib.axes as pyplot_axes
import matplotlib.gridspec as pyplot_gridspec
import matplotlib.patheffects as pyplot_effects
import numpy
import json
import logging
import itertools

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

INPUT_SPACE = 0.3

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
	fig = pyplot.figure(figsize=(20, 10))
	fig.suptitle(modelInfo.name)

	inputCount = len(modelInfo.inputs) + len(modelInfo.clocks)
	outerGrid = pyplot_gridspec.GridSpec(2, 1, hspace=0.05, height_ratios=[INPUT_SPACE, 1.0 - INPUT_SPACE])
	topGrid = pyplot_gridspec.GridSpecFromSubplotSpec(max(1, inputCount), 1, hspace=0.2, subplot_spec=outerGrid[0])

	axs: List[pyplot_axes.Axes] = []
	for index in range(inputCount):
		axs.append(fig.add_subplot(topGrid[index]))
	axs.append(fig.add_subplot(outerGrid[1]))

	for ax in axs[:-1]:
		ax.get_xaxis().set_visible(False)

	axs[-1].set_xlabel('Time [s]')
	axs[-1].set_ylabel('Voltage [V]')
	axs[-1].grid()

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

	for ax in axs:
		ax.set_xlim(x_low - (x_high - x_low) * x_margins, x_high + (x_high - x_low) * x_margins)
		ax.set_ylim(y_low - (y_high - y_low) * y_margins, y_high + (y_high - y_low) * y_margins)

	axs[-1].axhline(float(measurementParams.MEASUREMENT_LOW_BOUNDARY), color=COLOR_LIMIT, linestyle=STYLE_LIMIT, label='LOW', clip_on=False)
	axs[-1].axhline(float(measurementParams.MEASUREMENT_HIGH_BOUNDARY), color=COLOR_LIMIT, linestyle=STYLE_LIMIT, label='HIGH', clip_on=False)

	for index, transitionPoint in enumerate(measurementParams.TRANSITION_START_TIMES):
		axs[-1].axvline(float(transitionPoint), color=COLOR_TRANSITION, linestyle=STYLE_TRANSITION, label=f'Transition {index + 1}', clip_on=False)
	for index, transitionPoint in enumerate(measurementParams.TRANSITION_END_TIMES):
		axs[-1].axvline(float(transitionPoint), color=COLOR_TRANSITION, linestyle=STYLE_TRANSITION, clip_on=False)

	for ax in axs:
		for index, measurePoint in enumerate(measurementParams.MEASUREMENT_TIMES):
			ax.axvline(float(measurePoint), color=COLOR_MEASURE, linestyle=STYLE_MEASURE, label=f'Measurement {index + 1}', clip_on=False)

	measurementIndices = []
	for time in measurementParams.MEASUREMENT_TIMES:
		if time > spiceResult.timeValues[-1]:
			# Simulation was aborted prematurely; Assume instability as cause
			continue

		# Search for closest point as we might not have an exact match
		index_left = min(bisect_left(spiceResult.timeValues, time), len(spiceResult.timeValues) - 1)
		index_right = min(bisect_right(spiceResult.timeValues, time), len(spiceResult.timeValues) - 1)
		if abs(spiceResult.timeValues[index_left] - time) < abs(spiceResult.timeValues[index_right] - time):
			index = index_left
		else:
			index = index_right
		measurementIndices.append(index)

	index = 0
	inputHandles = []
	inputLabels = []
	inputTexts = []
	clockHandles = []
	clockLabels = []
	clockTexts = []
	for port in modelInfo.inputs:
		handle, = axs[index].plot(numpy.array(times), numpy.array(voltages[f"IN_{port}".lower()]), label=port)
		inputLabels.append(axs[index].set_ylabel(port))
		inputHandles.append(handle)

		inputTexts.append([])
		for measurementIndex in measurementIndices:
			logicValue = _GetLogicValue(
				voltages[f"IN_{port}".lower()][measurementIndex],
				measurementParams.MEASUREMENT_LOW_BOUNDARY,
				measurementParams.MEASUREMENT_HIGH_BOUNDARY
			)
			inputTexts[-1].append(axs[index].text(times[measurementIndex], (y_low + y_high) / 2.0, f'{logicValue}', fontsize=15, #backgroundcolor='white',
				horizontalalignment='center', verticalalignment='center'))

		index += 1

	for port in modelInfo.clocks:
		handle, = axs[index].plot(numpy.array(times), numpy.array(voltages[f"IN_{port}".lower()]), label=port)
		clockLabels.append(axs[index].set_ylabel(port))
		clockHandles.append(handle)

		clockTexts.append([])
		for measurementIndex in measurementIndices:
			logicValue = _GetLogicValue(
				voltages[f"IN_{port}".lower()][measurementIndex],
				measurementParams.MEASUREMENT_LOW_BOUNDARY,
				measurementParams.MEASUREMENT_HIGH_BOUNDARY
			)
			clockTexts[-1].append(axs[index].text(times[measurementIndex], (y_low + y_high) / 2.0, f'{logicValue}', fontsize=15, #backgroundcolor='white',
				horizontalalignment='center', verticalalignment='center'))

		index += 1

	handles = []
	for index, port in enumerate(modelInfo.inputs):
		handle, = axs[-1].plot(numpy.array(times), numpy.array(voltages[f"IN_{port}".lower()]), linestyle=STYLE_INPUTS, label=port)
		handles.append(handle)
		inputHandles[index].set_color(handle.get_color())
		inputLabels[index].set_color(handle.get_color())
		for textHandle in inputTexts[index]:
			textHandle.set_color(handle.get_color())

	for index, port in enumerate(modelInfo.clocks):
		handle, = axs[-1].plot(numpy.array(times), numpy.array(voltages[f"IN_{port}".lower()]), linestyle=STYLE_CLOCKS, label=port)
		handles.append(handle)
		clockHandles[index].set_color(handle.get_color())
		clockLabels[index].set_color(handle.get_color())
		for textHandle in clockTexts[index]:
			textHandle.set_color(handle.get_color())

	for index, port in enumerate(modelInfo.outputs):
		handle, = axs[-1].plot(numpy.array(times), numpy.array(voltages[f"OUT_{port}".lower()]), linestyle=STYLE_OUTPUTS, label=port)
		handles.append(handle)

		for measurementIndex in measurementIndices:
			logicValue = _GetLogicValue(
				voltages[f"OUT_{port}".lower()][measurementIndex],
				measurementParams.MEASUREMENT_LOW_BOUNDARY,
				measurementParams.MEASUREMENT_HIGH_BOUNDARY
			)
			outputCount = len(modelInfo.outputs)
			verticalPosition = y_low + ((outputCount - index) / (outputCount + 1)) * (y_low + y_high)
			axs[-1].text(times[measurementIndex], verticalPosition, f'{logicValue}', fontsize=25, color=handle.get_color(), #backgroundcolor='white',
				horizontalalignment='center', verticalalignment='center')

	axs[-1].legend(handles=handles, loc=(0.05, 0.1))

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
	fig = pyplot.figure(figsize=(20, 10))
	fig.suptitle(modelInfo.name)

	inputCount = len(modelInfo.inputs) + len(modelInfo.clocks)
	outerGrid = pyplot_gridspec.GridSpec(2, 1, hspace=0.05, height_ratios=[INPUT_SPACE, 1.0 - INPUT_SPACE])
	topGrid = pyplot_gridspec.GridSpecFromSubplotSpec(max(1, inputCount), 1, hspace=0.2, subplot_spec=outerGrid[0])

	axs: List[pyplot_axes.Axes] = []
	for index in range(inputCount):
		axs.append(fig.add_subplot(topGrid[index]))
	axs.append(fig.add_subplot(outerGrid[1]))

	for ax in axs[:-1]:
		ax.get_xaxis().set_visible(False)

	axs[-1].set_xlabel('Time [s]')
	axs[-1].set_ylabel('Value [Logic]')
	axs[-1].grid()

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
	if timeValues[0] > 0.0:
		timeValues = [0.0, *timeValues]
		inputValues = { port: f'X{value}' for port, value in inputValues.items() }
		clockValues = { port: f'X{value}' for port, value in clockValues.items() }
		outputValues = { port: f'X{value}' for port, value in outputValues.items() }

	if len(timeValues) == 1:
		# Add pseudo data point to make plotting possible
		timeValues = [timeValues[0], timeValues[0] + 1e-15]
		inputValues = { port: f'{value[0]}{value[0]}' for port, value in inputValues.items() }
		clockValues = { port: f'{value[0]}{value[0]}' for port, value in clockValues.items() }
		outputValues = { port: f'{value[0]}{value[0]}' for port, value in outputValues.items() }

	# All data is provided as point measurements, where we assume that the signals
	# are stable in between those data points. Therefore, adjust the plot such that there
	# are no continuous transitions in between the data points as the plot otherwise
	# is almost unreadable then.
	expandedTimeValues = []
	expandedInputValues = { port: ''.join([f'{value}{value}' for value in values]) for port, values in inputValues.items() }
	expandedClockValues = { port: ''.join([f'{value}{value}' for value in values]) for port, values in clockValues.items() }
	expandedOutputValues = { port: ''.join([f'{value}{value}' for value in values]) for port, values in outputValues.items() }
	for index, time in enumerate(timeValues):
		startTime = time
		if index < len(timeValues) - 1:
			endTime = time + (timeValues[index + 1] - time) * 0.9995
		else:
			endTime = time + (timeValues[-1] - timeValues[0]) / len(timeValues)
		expandedTimeValues += [startTime, endTime]

	timeValues = expandedTimeValues
	inputValues = expandedInputValues
	clockValues = expandedClockValues
	outputValues = expandedOutputValues

	x_low = float(min(timeValues))
	x_high = float(max(timeValues))
	x_margins = 0.0

	y_low = 0.0
	y_high = 1.0
	y_margins = 0.05

	for ax in axs:
		ax.set_xlim(x_low - (x_high - x_low) * x_margins, x_high + (x_high - x_low) * x_margins)
		ax.set_ylim(y_low - (y_high - y_low) * y_margins, y_high + (y_high - y_low) * y_margins)

	axs[-1].axhline(0.0, color=COLOR_LIMIT, linestyle=STYLE_LIMIT, label='LOW', clip_on=False)
	axs[-1].axhline(1.0, color=COLOR_LIMIT, linestyle=STYLE_LIMIT, label='HIGH', clip_on=False)

	for ax in axs:
		for index, measurePoint in enumerate(timeValues):
			ax.axvline(float(measurePoint), color='red', linestyle=STYLE_MEASURE, label=f'Measurement {index + 1}', clip_on=False)

	index = 0
	inputHandles = []
	inputLabels = []
	inputTexts = []
	clockHandles = []
	clockLabels = []
	clockTexts = []
	for port in inputValues.keys():
		handle, = axs[index].plot(numpy.array(timeValues), numpy.array(_TranslateLogic(inputValues[port])), label=port)
		inputLabels.append(axs[index].set_ylabel(port))
		inputHandles.append(handle)
		inputTexts.append([])
		for value, timeStart, timeEnd in zip(inputValues[port][::2], timeValues[::2], timeValues[1::2]):
			inputTexts[-1].append(axs[index].text((timeStart + timeEnd) / 2.0, 0.5, value, fontsize=15, backgroundcolor='white',
				horizontalalignment='center', verticalalignment='center'))
		index += 1

	for port in clockValues.keys():
		handle, = axs[index].plot(numpy.array(timeValues), numpy.array(_TranslateLogic(clockValues[port])), label=port)
		clockLabels.append(axs[index].set_ylabel(port))
		clockHandles.append(handle)
		clockTexts.append([])
		for value, timeStart, timeEnd in zip(clockValues[port][::2], timeValues[::2], timeValues[1::2]):
			clockTexts[-1].append(axs[index].text((timeStart + timeEnd) / 2.0, 0.5, value, fontsize=15, backgroundcolor='white',
				horizontalalignment='center', verticalalignment='center'))
		index += 1

	handles = []
	for index, port in enumerate(inputValues.keys()):
		handle, = axs[-1].plot(numpy.array(timeValues), numpy.array(_TranslateLogic(inputValues[port])), linestyle=STYLE_INPUTS, label=port)
		handles.append(handle)
		inputHandles[index].set_color(handle.get_color())
		inputLabels[index].set_color(handle.get_color())
		for textHandle in inputTexts[index]:
			textHandle.set_color(handle.get_color())

	for index, port in enumerate(clockValues.keys()):
		handle, = axs[-1].plot(numpy.array(timeValues), numpy.array(_TranslateLogic(clockValues[port])), linestyle=STYLE_CLOCKS, label=port)
		handles.append(handle)
		clockHandles[index].set_color(handle.get_color())
		clockLabels[index].set_color(handle.get_color())
		for textHandle in clockTexts[index]:
			textHandle.set_color(handle.get_color())
		for value, timeStart, timeEnd in zip(clockValues[port][::2], timeValues[::2], timeValues[1::2]):
			if value == 'P':
				axs[-1].text((timeStart + timeEnd) / 2.0, 0.5, 'P', fontsize=25, color=handle.get_color(), backgroundcolor='white',
					horizontalalignment='center', verticalalignment='center')

	for port in outputValues.keys():
		handle, = axs[-1].plot(numpy.array(timeValues), numpy.array(_TranslateLogic(outputValues[port])), linestyle=STYLE_OUTPUTS, label=port)
		handles.append(handle)

	axs[-1].legend(handles=handles, loc=(0.05, 0.1))

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
