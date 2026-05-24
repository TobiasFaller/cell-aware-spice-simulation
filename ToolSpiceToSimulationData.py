import multiprocessing
import argparse
import json
import json5
import re
import tqdm
import gzip
from pathlib import Path
from logging import debug, info, warning, error
from dataclasses import dataclass
from typing import List, Dict, Callable, Any, Union
from collections import defaultdict
from enum import StrEnum
from bisect import bisect_left, bisect_right

from PySpice.Unit import u_s, u_Ω, u_F, u_V, u_S
from PySpice.Unit.Unit import UnitValue

from Logging.Logging import ParseOptions, ParseEnumList
from Liberty.TimingImporter import CellTimingInfo, LookupInfo, ImportLibertyTiming, GetTimingConstraints, GetLookupConstraints
from Liberty.CellImporter import ImportLibertyCells
from Logic.Pattern import ConvertPatternsToFilteredSequence, ExpandClocks, CompactClocks
from Logic.Logic import EvaluateEquations
from Spice.SpiceFaultInjector import FaultType, FaultInjection, PlanFaultInjections, InjectFault
from Spice.SpiceModelInfo import SpiceModelInfo
from Spice.SpiceParameters import SimulationType, SimulationParameters, TestbenchParameters, MeasurementParameters, FaultParameters
from Spice.SpiceSimulation import SpiceException, SpiceResult, MeasureException, SpiceMeasurement, SpiceSimulate, SpiceMeasure, SpiceSimulationExport, SpiceSimulationPlot, SpiceMeasurementPlot, SpiceMeasurementExport
from Spice.SpiceTestbenchGenerator import SpiceTestbenchGenerator
from Spice.SpiceParser import ParseSpiceCell
from Simulation.SimulationInput import GetStaticBinaryPatterns, GetDynamicTransitionPatterns
from Simulation.SimulationData import SimulationCategory
from Simulation.SimulationCheck import CheckFaultFreeSimulation
from Workarounds.PySpiceRecursionFix import _


class DebugOptions(StrEnum):
	ExportPlan = 'export-plan'
	ExportTestbench = 'export-testbench'
	ExportSimulation = 'export-simulation'
	ExportMeasurement = 'export-measurement'
	PlotSimulation = 'plot-simulation'
	PlotMeasurement = 'plot-measurement'
	FinishAfterPlan = 'finish-after-plan'
	FinishAfterTestbench = 'finish-after-testbench'
	FinishAfterSimulation = 'finish-after-simulation'
	FinishAfterMeasurement = 'finish-after-measurement'

class InputType(StrEnum):
	STATIC_INPUTS = 'static'
	TRANSITION_INPUTS = 'transition'

	def __str__(self) -> str:
		return self.value


@dataclass
class SimulationJob:
	cellInfo: SpiceModelInfo
	cellTiming: CellTimingInfo
	faultCategory: str
	faultInjection: Union[FaultInjection, str]
	inputType: InputType
	inputSequence: Dict[str, str]
	clockSequence: Dict[str, str]
	jobId: int = -1
	jobCount: int = -1

	def __repr__(self) -> str:
		inputs = ' '.join(port + "=" + sequence for port, sequence in self.inputSequence.items())
		clocks = ' '.join(port + "=" + sequence for port, sequence in self.clockSequence.items())
		return f'{self.cellInfo.name} {self.faultInjection} {inputs} {clocks}'.strip()


@dataclass
class SimulationResult:
	jobId: int
	spiceResult: Union[SpiceResult, None] = None
	spiceException: Union[SpiceException, None] = None
	spiceCompleted: bool = False
	measureResult: Union[SpiceMeasurement, None] = None
	measureException: Union[MeasureException, None] = None


def Main() -> None:
	parser = argparse.ArgumentParser(
		prog='SpiceToSimulationData',
		description='Simulates defects on cells of the library via SPICE',
		add_help=True
	)
	parser.add_argument('--library', dest='library', type=str, required=True, help='Configuration file for the cell library')
	parser.add_argument('--library-path', dest='library_path', type=str, required=True, help='Root directory of the cell library')
	parser.add_argument('--cells', dest='cell_filter', type=str, default='.*', help='Filter for simulated cells')
	parser.add_argument('--faults', dest='inject_faults', type=str, nargs='*', default=[str(e) for e in FaultType if e != FaultType.FaultFree], help='Injected faults for the simulation (use -h faults for supported types)'),
	parser.add_argument('--inputs', dest='input_types', type=str, nargs='+', default=[str(e) for e in InputType], help='Fault simulation type (use -h simulation for supported types)')
	parser.add_argument('--output', dest='output_path', type=str, required=True, help='Output directory for generated files')
	parser.add_argument('--output-compress', dest='output_compress', action='store_true', default=False, help='Enable compression for output format')
	parser.add_argument('--processes', dest='processes', type=int, default=0, help='Number of parallel fault simulations (0 for auto, 1 is default)')
	parser.add_argument('-d', '--debug', dest='debug', type=str, nargs='*', default=[], help='Enable debug options (use -h debug for supported options)')
	parser.add_argument('-v', '--verbose', dest='verbose', action='store_true', default=False, help='Enable verbose logging')
	options = ParseOptions(parser, [
		('faults', [e for e in FaultType if e != FaultType.FaultFree]),
		('inputs', InputType),
		('debug', DebugOptions),
	])

	info('------------------------ PARSING OPTIONS -----------------------')
	global parallelism
	global injectFaults
	global inputTypes
	global debugOptions
	parallelism = options.processes
	injectFaults = [
		FaultType.FaultFree,
		*ParseEnumList('faults', FaultType, options.inject_faults)
	]
	inputTypes = ParseEnumList('inputs', InputType, options.input_types)
	debugOptions = ParseEnumList('debug', DebugOptions, options.debug)
	for debugOption in debugOptions:
		debug(f'Enabling debug option {debugOption.value}')

	global outputPath
	outputPath = Path(options.output_path)
	outputPath.mkdir(parents=True, exist_ok=True)

	info('------------------------ LOADING CONFIGURATION -----------------------')
	global library
	with open(options.library, 'rt', encoding='utf-8') as file:
		library = json5.load(file)

	global libraryPath
	global libertyPath
	global spicePath
	global includePaths
	libraryPath = Path(options.library_path)
	libertyPath = libraryPath / library['paths']['liberty']
	spicePath = libraryPath / library['paths']['spice']
	includePaths = [libraryPath / path for path in library['paths']['includes']]

	info('------------------------ LOADING CELLS -----------------------')
	cellFilter = lambda name: re.match(options.cell_filter, name)
	cells = ImportLibertyCells(libertyPath, cellFilter)
	cellInfos: Dict[str, SpiceModelInfo] = {}
	for cellInfo in cells.values():
		path = spicePath.with_name(spicePath.name.replace('{cell}', cellInfo.name))
		cellInfo.cell = ParseSpiceCell(path, cellInfo.name)
		cellInfos[cellInfo.name] = cellInfo

	for cellName, cell in cells.items():
		def _GetClock(clock, pol):
			return {
				None: f'?{clock}',
				'positive': f'+{clock}',
				'negative': f'-{clock}',
			}.get(pol)

		inputs = f'Inputs {", ".join(cell.inputs)}' if len(cell.inputs) > 0 else ''
		outputs = f'Outputs {", ".join(cell.outputs)}' if len(cell.outputs) > 0 else ''
		clocks = f'Clocks {", ".join([_GetClock(clock, pol) for clock, pol in zip(cell.clocks, cell.clockPolarities)])}' if len(cell.clocks) > 0 else ''
		# supplies = f'Supplies {", ".join(cell.supplies)}' if len(cell.supplies) > 0 else ''
		# grounds = f'Grounds {", ".join(cell.grounds)}' if len(cell.grounds) > 0 else ''
		equations = f'Equations {", ".join(["{}={}".format(output, equation) for output, equation in cell.equations.items()])}' if len(cell.equations) > 0 else ''
		debug(f'Loaded cell {cellName} ({", ".join([element for element in (inputs, outputs, clocks, equations) if element])})')

	info('------------------------ LOADING TIMING TABLES -----------------------')
	cellFilter = lambda name: name in cellInfos
	cellTimings: Dict[str, CellTimingInfo] = ImportLibertyTiming(libertyPath, cellFilter)

	info('------------------------ PLANNING SIMULATIONS -----------------------')
	simulationData = SimulationCategory()
	simulationsPlanned: Dict[str, Dict[Union[FaultType, str], List[SimulationJob]]] = {}
	for cellInfo in cellInfos.values():
		if len(cellInfo.clocks) > 1:
			raise ValueError(f'Cell {cellInfo.name} contains more than one clock: {", ".join(cellInfo.clocks)}')

		cellTiming = cellTimings.get(cellInfo.name, None)
		if cellTiming is None:
			raise ValueError(f'No timing information is available for cell {cell}')

		def _CheckLimits(value: UnitValue, constraints: LookupInfo, name: str):
			if value < constraints.minValue:
				raise ValueError(f'Parameter {name} for cell {cellInfo.name} with {value} is below minimum {constraints.minValue}')
			elif value > constraints.maxValue:
				raise ValueError(f'Parameter {name} for cell {cellInfo.name} with {value} is above maximum {constraints.maxValue}')

		# Adjust input transition and load capacitance if required as not all gates have a range
		# where the default input transition and load capacitance falls into.
		lookupConstraints = GetLookupConstraints(cellTiming)
		testbenchParams = GetTestbenchOverrides(library.get('testbench', {})) \
			| GetTestbenchOverrides(library.get('overrides', {}).get(cellInfo.name, {}))
		if 'total_output_net_capacitance' in lookupConstraints:
			_CheckLimits(testbenchParams['LOAD_CAPACITANCE'], lookupConstraints['total_output_net_capacitance'], 'load capacitance')
		if 'input_net_transition' in lookupConstraints:
			_CheckLimits(testbenchParams['INPUT_TRANSITION_TIME'], lookupConstraints['input_net_transition'], 'input transition')

		cellSimulations: Dict[Union[FaultType, str], List[SimulationJob]] = defaultdict(list)
		for inputType in inputTypes:
			if inputType == InputType.STATIC_INPUTS:
				inputSequences = GetStaticBinaryPatterns(cellInfo.inputs)
			elif inputType == InputType.TRANSITION_INPUTS:
				inputSequences = GetDynamicTransitionPatterns(cellInfo.inputs)

			for inputSequence in inputSequences:
				if len(cellInfo.clocks) > 0:
					# Extend all the inputs with another timeframe such that the output can be .
					# The value of the inputs is keps as in the time before to have a deterministic behaviour.
					inputLength = max([0] + [len(value) for value in inputSequence.values()])
					inputSequence = { input: f'{value}{value[-1]}' for input, value in inputSequence.items() }
					clockSequence = { clock: f'{"P" * inputLength}P' for clock in cellInfo.clocks }
				else:
					clockSequence = {}

				faults = [('liberty-model', 'liberty-model')]
				for faultCategory in injectFaults:
					for faultInjection in PlanFaultInjections(cellInfo, faultCategory):
						faults.append((faultCategory, faultInjection))

				for faultCategory, faultInjection in faults:
					cellSimulations[faultCategory].append(
						SimulationJob(
							cellInfo=cellInfo,
							cellTiming=cellTiming,
							faultCategory=faultCategory,
							faultInjection=faultInjection,
							inputType=inputType,
							inputSequence=inputSequence,
							clockSequence=clockSequence,
						)
					)

		simulationsPlanned[cellInfo.name] = cellSimulations

	info('------------------------ PLANNED SIMULATIONS -----------------------')
	for cell, cellSimulations in simulationsPlanned.items():
		info(f'Cell {cell}: {sum(len(l) for l in cellSimulations.values())} simulations')
		for fault, simulations in cellSimulations.items():
			info(f'    {fault}: {len(simulations)} simulations')
	info('')

	# Flatten the simulation list and assign job ids
	simulationsPlannedFlat: List[SimulationJob] = []
	for cellSimulations in simulationsPlanned.values():
		for faultInjections in cellSimulations.values():
			simulationsPlannedFlat += faultInjections
	for index, simulation in enumerate(simulationsPlannedFlat):
		simulation.jobId = index
		simulation.jobCount = len(simulationsPlannedFlat)
	simulationsPlanned = None

	if DebugOptions.ExportPlan in debugOptions:
		writePath = outputPath / f'plan.json'
		debug(f'Exporting plan to {writePath}')
		with open(writePath, 'wt', encoding='utf-8') as file:
			def _GetInjectionParams(faultInjection: Union[FaultInjection, str]):
				return {} if isinstance(faultInjection, str) else faultInjection.faultParameters

			json.dump([{
				'job': simulation.jobId,
				'cell': simulation.cellInfo.name,
				'fault-type': simulation.faultCategory,
				'fault-params': _GetInjectionParams(simulation.faultInjection),
				'input-type': simulation.inputType,
				'inputs': simulation.inputSequence,
				'clocks': simulation.clockSequence,
			} for simulation in simulationsPlannedFlat], file, indent=4)

	if DebugOptions.FinishAfterPlan in debugOptions:
		return

	info(f'------------------------ RUNNING {len(simulationsPlannedFlat)} SIMULATIONS -----------------------')
	simulationResults = ProcessFaultSimulations(RunFaultSimulationJob, simulationsPlannedFlat)

	# Sort the results afterwards to have the faults align with the original order
	simulationResults.sort(key=lambda item: item.jobId)

	if DebugOptions.FinishAfterTestbench in debugOptions \
		or DebugOptions.FinishAfterSimulation in debugOptions \
		or DebugOptions.FinishAfterMeasurement in debugOptions:
		return

	info(f'------------------------ EVALUATION SIMULATIONS -----------------------')
	simulationsFailed = 0
	simulationsAborted = 0
	simulationStatus: Dict[str, Dict[str, List[str]]] = defaultdict(lambda: defaultdict(list))
	for params, result in zip(simulationsPlannedFlat, simulationResults):
		cellName = params.cellInfo.name
		faultName = str(params.faultInjection)
		faultCategory = str(params.faultCategory)

		if result.spiceException is not None or result.measureException is not None:
			if result.spiceException is not None:
				simulationStatus[cellName][faultName] += [f'job {result.jobId} spice error: {result.spiceException}']
				simulationsFailed += 1
			elif result.measureException is not None:
				simulationStatus[cellName][faultName] += [f'job {result.jobId} measure error: {result.measureException}']
				simulationsFailed += 1
			continue

		# Cell inputs should always be a defined value (except the last timeframe if a clock is present).
		undefinedInput = False
		for port, value in result.measureResult.inputValues.items():
			checkValue = value[:-1] if len(params.cellInfo.clocks) > 0 else value
			if 'X' in checkValue:
				undefinedInput = True
				break
		if undefinedInput:
			exception = ValueError(f'Invalid input to cell {cellName} {port}={value}')
			simulationStatus[cellName][faultName] += [f'job {result.jobId} input error: {exception}']
			simulationsFailed += 1
			continue

		# Clock inputs can only be undefined if the clock has no polarity (is unused)
		# or in the last timeframe where no clock pulse is required.
		undefinedClock = False
		for port, value in result.measureResult.clockValues.items():
			checkValue = value[:-1] if len(params.cellInfo.clocks) > 0 else value
			if 'X' in checkValue and params.cellInfo.clockPolarities[port] is not None:
				undefinedClock = True
				break
		if undefinedClock:
			exception = ValueError(f'Invalid clock to cell {cellName} {port}={value}')
			simulationStatus[cellName][faultName] += [f'job {result.jobId} input error: {exception}']
			simulationsFailed += 1
			continue

		if result.spiceCompleted:
			simulationStatus[cellName][faultName] += [f'job {result.jobId} simulated']
		else:
			simulationStatus[cellName][faultName] += [f'job {result.jobId} simulation warning: aborted prematurely, assuming instability']
			simulationsAborted += 1

		simulationData.AddRun(
			jobId=result.jobId,
			category=faultCategory,
			cellName=cellName,
			faultName=faultName,
			inputType=params.inputType,
			inputs=result.measureResult.inputValues,
			clocks=result.measureResult.clockValues,
			outputs=result.measureResult.outputValues,
		)

	info('------------------------ VALIDATING FAULT-FREE SIMULATION -----------------------')
	checkResults = CheckFaultFreeSimulation(simulationData.categories['fault-free'], cellInfos)
	for cellName, checkResult in checkResults.items():
		if checkResult.validated and len(checkResult.violations) == 0:
			info(f'Fault-free simulation result of {cellName} correct')
			simulationStatus[cellName]['fault-free'] += ['validated']
			continue

		if len(checkResult.violations) == 0:
			warning(f'Fault-free simulation result of {cellName} could not be fully validated')
			simulationStatus[cellName]['fault-free'] += ['validation error: not validated (maybe equations missing)']
			continue

		for violation in checkResult.violations:
			inputs = ', '.join(f'{inputName}={inputValue}' for inputName, inputValue in violation.inputs.items())
			clocks = ', '.join(f'{clockName}={clockValue}' for clockName, clockValue in violation.clocks.items())
			outputs = ', '.join(f'{outputName}={outputValue}' for outputName, outputValue in violation.outputs.items())
			expected = ', '.join(f'{outputName}={outputValue}' for outputName, outputValue in violation.expected.items())
			violationText = f'job {violation.jobId} inputs {inputs}, clocks {clocks}, got {outputs} but expected {expected}'
			simulationStatus[cellName]['fault-free'] += [f'validation error: {violationText}']
			simulationsFailed += 1
			error(f'The {cellName} has a wrong logic outputs for {violationText}')

	info('------------------------ EXPORTING RESULTS -----------------------')

	open_func = gzip.open if options.output_compress else open
	indent = 4 if (not options.output_compress) and options.verbose else None

	writePath = outputPath / f'simulation.data.json{".gz" if options.output_compress else ""}'
	info(f'Exporting simulation data to {writePath}')
	with open_func(writePath, 'wt', encoding='utf-8') as stream:
		json.dump(simulationData.ToDict(), stream, indent=indent)

	writePath = outputPath / f'simulation.status.json{".gz" if options.output_compress else ""}'
	info(f'Exporting simulation statuses to {writePath}')
	with open_func(writePath, 'wt', encoding='utf-8') as stream:
		json.dump(simulationStatus, stream, indent=indent)

	info(f'------------------------ FINISHED -----------------------')
	if simulationsFailed == 0:
		print('All simulations passed validation')
	else:
		print(f'{simulationsFailed} simulations failed validation')
		exit(1)


def ProcessFaultSimulations(function: Callable, jobs: List[SimulationJob]) -> List[SimulationResult]:
	def _split_list(jobs, chunkSize):
		for i in range(0, len(jobs), chunkSize):
			yield jobs[i:i+chunkSize]

	result = []
	if parallelism != 1:
		# A number of 0 is mapped to None (scale according to number of processors available)
		processes=None if parallelism == 0 else parallelism

		# Close pools after some time to free up resources like growing stream buffers
		for sublist in _split_list(jobs, chunkSize=100000):
			info('(Re)creating pool and (re)allocating workers')
			with multiprocessing.Pool(processes, maxtasksperchild=1, initializer=InitGlobals, initargs=(GetGlobals(), )) as pool:
				with tqdm.tqdm(total=len(jobs), initial=len(result)) as progressBar:
					for item in pool.imap_unordered(function, sublist, chunksize=100 if len(jobs) > 10000 else 1):
						progressBar.update(1)
						result += [item]
	else:
		with tqdm.tqdm(total=len(jobs)) as progressBar:
			for job in jobs:
				result += [function(job)]
				progressBar.update(1)

	return result


def RunFaultSimulationJob(job: SimulationJob) -> SimulationResult:
	debug(f'Running job {job.jobId:06d}/{job.jobCount:06d}: {job}')

	if job.faultInjection == 'liberty-model':
		# We assume all clocks have the same polarity and are clocking every timeframe
		clockPolarities = dict(zip(job.cellInfo.clocks, job.cellInfo.clockPolarities))
		times, inputs, clocks = ExpandClocks(job.inputSequence, job.clockSequence, clockPolarities)

		# Evaluate the equations to simulate the liberty equation model
		equationResults = EvaluateEquations(job.cellInfo.equations, { **inputs, **clocks })
		outputs = { output: equationResults[output] for output in job.cellInfo.outputs }
		times, inputs, clocks, outputs = CompactClocks(inputs, clocks, clockPolarities, outputs)

		inputLength = max([0] + [len(value) for value in inputs.values()])
		if len(times) != inputLength:
			return SimulationResult(
				jobId=job.jobId,
				spiceCompleted=True,
				measureException=MeasureException(
					ValueError(f'Simulation length of {len(times)} is different than input length {inputLength}'))
			)

		return SimulationResult(
			jobId=job.jobId,
			spiceCompleted=True,
			measureResult=SpiceMeasurement(
				timeValues=range(max([0] + [len(values) for values in job.inputSequence.values()])),
				inputValues=inputs,
				clockValues=clocks,
				outputValues=outputs,
				internalValues={}
			)
		)

	faultParams = FaultParameters(
		**(GetFaultOverrides(library.get('fault', {}))
	 		| GetFaultOverrides(library.get('overrides', {}).get(job.cellInfo.name, {})))
	)
	faultyCell = InjectFault(job.cellInfo, job.faultInjection, faultParams)

	testbenchParams = TestbenchParameters(
		**(GetTestbenchOverrides(library.get('testbench', {}))
	 		| GetTestbenchOverrides(library.get('overrides', {}).get(job.cellInfo.name, {}))),
		TRANSISTOR_MODELS=includePaths,
		CELL_MODEL=faultyCell
	)
	simulationParams, measurementParams = GenerateSimulationParameters(job, testbenchParams)

	testbenchGenerator = SpiceTestbenchGenerator(job.cellInfo, testbenchParams, simulationParams)

	if DebugOptions.ExportTestbench in debugOptions:
		writePath = outputPath / 'jobs' / f'{job.jobId}-{job.cellInfo.name}-{job.faultInjection.path}.spi'
		writePath.parent.mkdir(parents=True, exist_ok=True)
		debug(f'Exporting testbench to {writePath}')
		testbenchGenerator.ExportTestbench(writePath, simulationParams)

	if DebugOptions.FinishAfterTestbench in debugOptions:
		return SimulationResult(job.jobId)

	try:
		simulationResult = SpiceSimulate(job.cellInfo, testbenchGenerator.circuit, simulationParams)

		# If simulation was aborted prematurely we assume that instability is the reason.
		timeTarget, timeStep = simulationParams.SIMULATION_TIME, simulationParams.SIMULATION_STEP
		timeReached = max([0@u_s] + simulationResult.timeValues)
		simulationCompleted = (timeReached + timeStep) >= timeTarget
	except SpiceException as exception:
		warning(f'Job {job.jobId} failed: {exception}')
		return SimulationResult(job.jobId, spiceException=exception)

	if DebugOptions.ExportSimulation in debugOptions:
		writePath = outputPath / 'jobs' / f'{job.jobId}-{job.cellInfo.name}-{job.faultInjection.path}.voltages.json'
		writePath.parent.mkdir(parents=True, exist_ok=True)
		debug(f'Exporting SPICE result to {writePath}')
		SpiceSimulationExport(job.cellInfo, simulationResult, writePath)

	if DebugOptions.PlotSimulation in debugOptions:
		writePath = outputPath / 'jobs' / f'{job.jobId}-{job.cellInfo.name}-{job.faultInjection.path}.voltages.png'
		writePath.parent.mkdir(parents=True, exist_ok=True)
		debug(f'Plotting SPICE result to {writePath}')
		SpiceSimulationPlot(job.cellInfo, simulationResult, measurementParams, writePath)

	if DebugOptions.FinishAfterSimulation in debugOptions:
		return SimulationResult(job.jobId, spiceCompleted=simulationCompleted)

	try:
		measurementResult = SpiceMeasure(job.cellInfo, simulationResult, measurementParams)

		# In case the simulation has not completed we still need complete inputs for extraction.
		# => Patch up the inputs such that the result can still be processed for generating a fault model.
		if not simulationCompleted:
			def _ExtractValues(ports: List[str], source: Dict[str, str]):
				result = dict.fromkeys(ports, "")
				for port in ports:
					for time in measurementParams.MEASUREMENT_TIMES:
						# Search for closest point as we might not have an exact match
						index_left = min(bisect_left(simulationParams.INPUT_STEPS, time), len(simulationParams.INPUT_STEPS) - 1)
						index_right = min(bisect_right(simulationParams.INPUT_STEPS, time), len(simulationParams.INPUT_STEPS) - 1)
						if abs(simulationParams.INPUT_STEPS[index_left] - time) < abs(simulationParams.INPUT_STEPS[index_right] - time):
							index = index_left
						else:
							index = index_right
						result[port] += source[port][index]
				return result

			measurementResult.inputValues = _ExtractValues(job.cellInfo.inputs, simulationParams.INPUT_VALUES)
			measurementResult.clockValues = _ExtractValues(job.cellInfo.clocks, simulationParams.INPUT_CLOCKS)
			for inputValue in measurementResult.inputValues.values():
				assert len(measurementResult.timeValues) == len(inputValue)
			for clockValue in measurementResult.clockValues.values():
				assert len(measurementResult.timeValues) == len(clockValue)
			for outputValue in measurementResult.outputValues.values():
				assert len(measurementResult.timeValues) == len(outputValue)

		measurementResult = ProcessMeasurement(job, measurementResult)
	except MeasureException as exception:
		return SimulationResult(job.jobId, measureException=exception, spiceCompleted=simulationCompleted)
	except ValueError as exception:
		return SimulationResult(job.jobId, measureException=exception, spiceCompleted=simulationCompleted)

	if DebugOptions.ExportMeasurement in debugOptions:
		writePath = outputPath / 'jobs' / f'{job.jobId}-{job.cellInfo.name}-{job.faultInjection.path}.measure.json'
		writePath.parent.mkdir(parents=True, exist_ok=True)
		debug(f'Exporting measurement result to {writePath}')
		SpiceMeasurementExport(job.cellInfo, measurementResult, writePath)

	if DebugOptions.PlotMeasurement in debugOptions:
		writePath = outputPath / 'jobs' / f'{job.jobId}-{job.cellInfo.name}-{job.faultInjection.path}.measure.png'
		writePath.parent.mkdir(parents=True, exist_ok=True)
		debug(f'Plotting measurement result to {writePath}')
		SpiceMeasurementPlot(job.cellInfo, measurementResult, writePath)

	return SimulationResult(job.jobId, measureResult=measurementResult, spiceCompleted=simulationCompleted)


def GenerateSimulationParameters(job: SimulationJob, testbenchParams: TestbenchParameters):
	inputLength = max([0] + [len(value) for value in job.inputSequence.values()])
	if inputLength <= 1 and len(job.cellInfo.clocks) == 0:
		simulationParams = SimulationParameters(
			SIMULATION_TYPE=SimulationType.STATIC_OPERATING_POINT,
			SIMULATION_TIME=0@u_s,
			SIMULATION_STEP=testbenchParams.SIMULATION_STEP,
			SIMULATION_GMIN=testbenchParams.SIMULATION_GMIN,
			INPUT_STEPS=[0@u_s],
			INPUT_VALUES=job.inputSequence,
			INPUT_CLOCKS={},
		)
		measurementParams = MeasurementParameters(
			SUPPLY_VOLTAGE_LOW=0@u_V,
			SUPPLY_VOLTAGE_HIGH=testbenchParams.SUPPLY_VOLTAGE,
			**(GetMeasurementOverrides(library.get('measurement', {}))
	  			| GetMeasurementOverrides(library.get('overrides', {}).get(job.cellInfo.name, {}))),
			TRANSITION_START_TIMES=[0@u_s],
			TRANSITION_END_TIMES=[0@u_s],
			MEASUREMENT_TIMES=[0@u_s],
		)
	else:
		cellName = job.cellInfo.name
		cellTiming = job.cellTiming

		deltaMeasurement = testbenchParams.DELTA_MEASUREMENT
		inputSetupTime = testbenchParams.INPUT_SETUP_TIME
		inputTransitionTime = testbenchParams.INPUT_TRANSITION_TIME
		loadCapacitance = testbenchParams.LOAD_CAPACITANCE

		simTime = [0@u_s]
		simInputs = []
		simMeasurements = []
		simTransitionStarts = []
		simTransitionEnds = []
		_GetCurrentTime = lambda: simTime[0]
		_AddMeasurementMarker = lambda: simMeasurements.append(simTime[0])
		_AddTransitionStartMarker = lambda: simTransitionStarts.append(simTime[0])
		_AddTransitionEndMarker = lambda: simTransitionEnds.append(simTime[0])

		def _AddDelay(time: UnitValue) -> UnitValue:
			# Make sure to copy simTime as otherwise all instances are changed.
			# Using += would otherwise modify all instances.
			simTime[0] = _GetCurrentTime() + time

		def _AddInputs(inputs):
			if len(simInputs) == 0 or simTime[0] > simInputs[-1][0]:
				simInputs.append((simTime[0], inputs))
			elif simTime[0] == simInputs[-1][0]:
				assert inputs == simInputs[-1][1]

		def _EndInputs():
			if len(simInputs) == 0 or simTime[0] > simInputs[-1][0]:
				simInputs.append((simTime[0], simInputs[-1][1]))
			_AddDelay(1e-15@u_s)

		# We assume all clocks have the same polarity and are clocking every timeframe
		clockPolarities = dict(zip(job.cellInfo.clocks, job.cellInfo.clockPolarities))
		indices, inputs, clocks = ExpandClocks(job.inputSequence, job.clockSequence, clockPolarities)

		# Split input sequences into timeframes and merge inputs and clocks to predict
		# required delay, transition, setup and hold times.
		timeframes = [{
			**{ inputName: inputValue[index] for inputName, inputValue in inputs.items() },
			**{ clockName: clockValue[index] for clockName, clockValue in clocks.items() }
		} for index in range(len(indices)) ]

		if len(timeframes) > 0:
			_AddInputs(timeframes[0])
			_AddDelay(inputSetupTime)
			_EndInputs()

		timingConstraints = GetTimingConstraints(cellName, cellTiming, inputTransitionTime, loadCapacitance, timeframes)
		timeframeEnds = []
		for timeframe in range(len(timeframes)):
			constraints = timingConstraints[timeframe]

			delayCell = constraints.cellDelay * testbenchParams.CELL_DELAY_FACTOR
			delayTransition = constraints.transitionDelay * testbenchParams.TRANSITION_DELAY_FACTOR
			delayPropagation = constraints.propagationDelay * testbenchParams.PROPAGATION_DELAY_FACTOR
			delayStable = max(0@u_s, max(delayCell, delayPropagation) + delayTransition)

			delaySetup = 0@u_s
			for setupTimeframe, setupConstraint in constraints.setupConstraints.items():
				timeReference = _GetCurrentTime() if (setupTimeframe >= timeframe) else timeframeEnds[setupTimeframe]
				timeElapsed = _GetCurrentTime() - timeReference
				delaySetup = max(delaySetup, setupConstraint * testbenchParams.CONSTRAINT_FACTOR - timeElapsed)

			delayHold = 0@u_s
			for holdTimeframe, holdConstraint in constraints.holdConstraints.items():
				timeReference = _GetCurrentTime() if (holdTimeframe >= timeframe) else timeframeEnds[holdTimeframe]
				timeElapsed = _GetCurrentTime() - timeReference
				delayHold = max(delayHold, holdConstraint * testbenchParams.CONSTRAINT_FACTOR - timeElapsed)

			if delaySetup > 0@u_s:
				_AddDelay(delaySetup)
				_EndInputs()

			if delayHold > 0@u_s:
				_AddDelay(delayHold)
				_EndInputs()

			_AddTransitionStartMarker()
			_AddInputs(timeframes[timeframe])
			_AddDelay(inputTransitionTime)
			_AddDelay(delayStable)
			_AddTransitionEndMarker()

			_AddDelay(deltaMeasurement)
			_AddMeasurementMarker()
			_EndInputs()

			timeframeEnds.append(_GetCurrentTime())

		if len(timeframes) > 0:
			_AddInputs(timeframes[-1])
			_AddDelay(inputSetupTime)
			_EndInputs()

		inputSteps = [time for time, _ in simInputs]
		inputValues = ConvertPatternsToFilteredSequence([inputs for _, inputs in simInputs], job.cellInfo.inputs)
		inputClocks = ConvertPatternsToFilteredSequence([inputs for _, inputs in simInputs], job.cellInfo.clocks)
		simulationParams = SimulationParameters(
			SIMULATION_TYPE=SimulationType.TRANSITION,
			SIMULATION_TIME=simTime[0],
			SIMULATION_STEP=testbenchParams.SIMULATION_STEP,
			SIMULATION_GMIN=testbenchParams.SIMULATION_GMIN,
			INPUT_STEPS=inputSteps,
			INPUT_VALUES=inputValues,
			INPUT_CLOCKS=inputClocks,
		)
		measurementParams = MeasurementParameters(
			SUPPLY_VOLTAGE_LOW=0@u_V,
			SUPPLY_VOLTAGE_HIGH=testbenchParams.SUPPLY_VOLTAGE,
			**(GetMeasurementOverrides(library.get('measurement', {}))
	  			| GetMeasurementOverrides(library.get('overrides', {}).get(cellName, {}))),
			TRANSITION_START_TIMES=simTransitionStarts,
			TRANSITION_END_TIMES=simTransitionEnds,
			MEASUREMENT_TIMES=simMeasurements,
		)

	return simulationParams, measurementParams


def ProcessMeasurement(job: SimulationJob, measurement: SpiceMeasurement) -> SpiceMeasurement:
	if len(job.cellInfo.clocks) == 0:
		return measurement
	else:
		inputLength = max([0] + [len(value) for value in job.inputSequence.values()])
		clockPolarities = { clock: polarity for clock, polarity in zip(job.cellInfo.clocks, job.cellInfo.clockPolarities) }
		indices, inputs, clocks, outputs = CompactClocks(measurement.inputValues, measurement.clockValues, clockPolarities, measurement.outputValues)
		times = [measurement.timeValues[timeframe] for timeframe in indices]
		if len(times) != inputLength:
			raise ValueError(f'Simulation length of {len(times)} is different than input length {inputLength}')

		return SpiceMeasurement(
			timeValues=times,
			inputValues=inputs,
			clockValues=clocks,
			outputValues=outputs,
			internalValues={}
		)


def GetOverrides(params: Dict[str, Any], specification: Dict[str, Any]) -> Dict[str, Any]:
	result = {}
	for name, unit in specification.items():
		value = params.get(name.replace('_', '-').lower(), None)
		if value is None:
			continue

		if unit is None or unit == str:
			result[name] = value
		else:
			result[name] = value@unit

	return result


def GetTestbenchOverrides(params: Dict[str, Any]) -> Dict[str, Any]:
	return GetOverrides(params, {
		'SUPPLY_NAME_VDD':               str,
		'SUPPLY_NAME_VSS':               str,
		'SUPPLY_VOLTAGE':                u_V,
		'SUPPLY_SERIES_RESISTANCE':      u_Ω,
		'SUPPLY_PARASITIC_CAPACITANCE':  u_F,
		'INPUT_VOLTAGE_HIGH':            u_V,
		'INPUT_VOLTAGE_LOW':             u_V,
		'INPUT_SERIES_RESISTANCE':       u_Ω,
		'INPUT_PARASITIC_CAPACITANCE':   u_F,
		'OUTPUT_SERIES_RESISTANCE':      u_Ω,
		'OUTPUT_PARASITIC_CAPACITANCE':  u_F,
		'OUTPUT_LEAKAGE_RESISTOR':       u_Ω,
		'LOAD_CAPACITANCE':              u_F,
		'INPUT_SETUP_TIME':              u_s,
		'INPUT_TRANSITION_TIME':         u_s,
		'DELTA_MEASUREMENT':             u_s,
		'SIMULATION_STEP':               u_s,
		'SIMULATION_GMIN':               u_S,
		'CELL_DELAY_FACTOR':             None,
		'TRANSITION_DELAY_FACTOR':       None,
		'PROPAGATION_DELAY_FACTOR':      None,
		'CONSTRAINT_FACTOR':             None,
	})


def GetFaultOverrides(params: Dict[str, Any]) -> Dict[str, Any]:
	return GetOverrides(params, {
		'STUCK_RESISTOR_VALUE':   u_Ω,
		'OPEN_RESISTOR_VALUE':    u_Ω,
		'SHORT_RESISTOR_VALUE':   u_Ω,
		'T_LEAK_RESISTOR_VALUE':  u_Ω,
		'T_DRIVE_RESISTOR_VALUE': u_Ω,
	})


def GetMeasurementOverrides(params: Dict[str, Any]) -> Dict[str, Any]:
	return GetOverrides(params, {
		'MEASUREMENT_LOW_BOUNDARY':  u_V,
		'MEASUREMENT_HIGH_BOUNDARY': u_V,
	})


def InitGlobals(toSet: Dict[str, Any]) -> None:
	globals().update(toSet)


def GetGlobals() -> Dict[str, Any]:
	return {
		'outputPath': outputPath,
		'parallelism': parallelism,
		'injectFaults': injectFaults,
		'debugOptions': debugOptions,
		'library': library,
		'libraryPath': libraryPath,
		'libertyPath': libertyPath,
		'spicePath': spicePath,
		'includePaths': includePaths
	}


if __name__ == '__main__':
	# This helps reduce the memory overhead as the default ('fork')
	# will duplicate the current Python context and with that
	# result in the collected result list to be copied into all
	# worker processes.
	multiprocessing.set_start_method('spawn')
	Main()
