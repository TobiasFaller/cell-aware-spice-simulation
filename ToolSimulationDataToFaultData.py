import argparse
import json
import re
import pathlib
import gzip
from enum import StrEnum
from typing import Dict, Union, List, Tuple, Set
from logging import info, debug, error
from itertools import chain
from collections import defaultdict

from Logging.Logging import ParseOptions, ParseEnumList
from Spice.SpiceFaultInjector import FaultType
from Simulation.SimulationData import SimulationData,  SimulationCategory, \
	SimulationFault, SimulationRun
from Simulation.ClassificationData import ClassificationData, ClassificationCategory, \
	ClassificationStatus


class DebugOptions(StrEnum):
	ExportDifferences = 'export-differences'
	ExportTrimmedSimulations = 'export-trimmed-simulations'
	FinishAfterDifferences = 'finish-after-differences'
	FinishAfterTrimmedSimulations = 'finish-after-trimmed-simulations'


Assignment = Set[Tuple[str, str]]
LookupKey = Tuple[Assignment, Assignment]
Pattern = Tuple[Assignment, Assignment, Assignment]


REGEX_DIFFERENCE = re.compile('D|X')
REGEX_PROPAGATION = re.compile('D')


def Main():
	parser = argparse.ArgumentParser(
		prog='SimulationDataToFaultData',
		description='Evaluates the simulation data and generates fault models',
		add_help=True
	)
	parser.add_argument('--cells', dest='cell_filter', type=str, default='.*', help='Filter for cells types')
	parser.add_argument('--faults', dest='fault_filter', type=str, nargs='*', default=[str(e) for e in FaultType if e != FaultType.FaultFree], help='Filter for fault types (use -h faults for supported types)'),
	parser.add_argument('--input', dest='input_path', type=str, required=True, help='Path to simulation data file')
	parser.add_argument('--input-compressed', dest='input_compress', action='store_true', default=False, help='Assume input is compressed')
	parser.add_argument('--output', dest='output_path', type=str, required=True, help='Output directory for generated files')
	parser.add_argument('--output-compress', dest='output_compress', action='store_true', default=False, help='Enable compression for output format')
	parser.add_argument('-d', '--debug', dest='debug', type=str, nargs='*', default=[], help='Enable debug options (use -h debug for supported options)')
	parser.add_argument('-v', '--verbose', dest='verbose', action='store_true', default=False, help='Enable verbose logging')
	options = ParseOptions(parser, [
		('faults', [e for e in FaultType if e != FaultType.FaultFree]),
		('debug', DebugOptions),
	])

	info('------------------------ PARSING OPTIONS -----------------------')
	filterFaults = [str(f) for f in [
		FaultType.FaultFree,
		*ParseEnumList('faults', FaultType, options.fault_filter)
	]]
	debugOptions = ParseEnumList('debug', DebugOptions, options.debug)
	for debugOption in debugOptions:
		debug(f'Enabling debug option {debugOption.value}')

	outputPath = pathlib.Path(options.output_path)
	outputPath.mkdir(parents=True, exist_ok=True)

	info(f'------------------------ IMPORTING SIMULATIONS -----------------------')
	open_func = gzip.open if options.input_compress else open
	info(f'Loading simulation data from {options.input_path}')
	with open_func(options.input_path, 'rt', encoding='utf-8') as stream:
		sourceData: SimulationCategory = SimulationCategory.FromDict(json.load(stream))

	# Use the liberty model as reference as it uses X values for when cell state is undetermined
	goldenModel = 'liberty-model'
	faultfreeModels = ['liberty-model', 'fault-free']

	filteredData = SimulationCategory()
	for faultCategory, category in sourceData.categories.items():
		# Required data for difference computation
		if faultCategory in faultfreeModels:
			filteredData.categories[faultCategory] = category
			continue

		if faultCategory not in filterFaults:
			info(f'Skipping category "{faultCategory}" in sources ...')
			continue

		for cellName, cell in category.cells.items():
			if not re.match(options.cell_filter, cellName):
				info(f'Skipping cell "{cellName}" in sources ...')
				continue

			filteredData.categories[faultCategory].cells[cellName] = cell

	sourceData = None

	info(f'------------------------ LENGTH-CHECKING SIMULATIONS -----------------------')
	failed = []
	succeeded = 0
	cells = 0
	for categoryName, category in filteredData.categories.items():
		for cellName, cell in category.cells.items():
			for faultName, fault in cell.faults.items():
				valid = True
				for run in fault.runs:
					length = _GetLength(run)
					for values in run.inputs.values():
						valid &= len(values) == length
					for values in run.clocks.values():
						valid &= len(values) == length
					for values in run.outputs.values():
						valid &= len(values) == length

				if valid:
					succeeded += 1
				else:
					error(f'Category {categoryName} cell {cellName} fault {faultName} failed pre-check')
					failed.append((categoryName, cellName, faultName))
			cells += 1

	info(f'Length-checked {len(failed) + succeeded} faults of {cells} cells')
	info(f'  Succeeded: {succeeded}')
	info(f'  Failed: {len(failed)}')
	for categoryName, cellName, faultName in failed:
		info(f'    - {categoryName}/{cellName}/{faultName}')
		for run in filteredData.categories[categoryName].cells[cellName].faults[faultName].runs:
			inputs = ', '.join(f'{inputName}={inputValue}' for inputName, inputValue in run.inputs.items())
			clocks = ', '.join(f'{clockName}={clockValue}' for clockName, clockValue in run.clocks.items())
			outputs = ', '.join(f'{outputName}={outputValue}' for outputName, outputValue in run.outputs.items())
			info(f'        {inputs}, {clocks}, {outputs}')

	if len(failed) > 0:
		error('Some cells failed length check. Exiting!')
		exit(1)

	info(f'------------------------ COMPUTING DIFFERENCES -----------------------')
	# Split data into golden model and faulty models
	goldenData = filteredData.categories[goldenModel]
	faultyData = SimulationCategory({
		category: filteredData.categories[category]
		for category in filteredData.categories.keys()
		if category not in faultfreeModels
	})
	filteredData = None

	maskedData, differenceData = _ComputeDifferences(goldenData, faultyData)
	faultyData = None

	if DebugOptions.ExportDifferences in debugOptions:
		writePath = outputPath / f'1-masked.data.json'
		info(f'Exporting masked data to {writePath}')
		with open(writePath, 'wt', encoding='utf-8') as stream:
			json.dump(maskedData.ToDict(), stream, indent=4)

		writePath = outputPath / f'1-differences.data.json'
		info(f'Exporting difference data to {writePath}')
		with open(writePath, 'wt', encoding='utf-8') as stream:
			json.dump(differenceData.ToDict(), stream, indent=4)

	if DebugOptions.FinishAfterDifferences in debugOptions:
		return

	info(f'------------------------ CHECKING SIMULATIONS -----------------------')
	failed: List[Tuple[str, str, str, List[Pattern]]] = []
	succeeded = 0
	cells = 0
	for categoryName, category in maskedData.categories.items():
		for cellName, cell in category.cells.items():
			for faultName, fault in cell.faults.items():
				prefixes: Dict[LookupKey, Tuple[Assignment, int]] = {}
				valid = True
				failing: Set[Pattern] = set()
				for runIndex, run in enumerate(fault.runs):
					for i in range(0, _GetLength(run)):
						inputs = { key: value[0:i+1] for key, value in run.inputs.items() }
						clocks = { key: value[0:i+1] for key, value in run.clocks.items() }
						outputs = { key: value[0:i+1] for key, value in run.outputs.items() }
						for input in inputs.values():
							valid &= (len(input) == i + 1)
						for clock in clocks.values():
							valid &= (len(clock) == i + 1)
						for output in outputs.values():
							valid &= (len(output) == i + 1)

						prefixInputs: LookupKey = (_ToAssignment(inputs), _ToAssignment(clocks))
						prefixOutputs: Assignment = _ToAssignment(outputs)
						if prefixInputs in prefixes:
							valid &= (prefixes[prefixInputs][0] == prefixOutputs)
							if prefixes[prefixInputs][0] != prefixOutputs:
								currentRun = fault.runs[runIndex]
								otherRun = fault.runs[prefixes[prefixInputs][1]]
								failing.add((_ToAssignment(currentRun.inputs), _ToAssignment(currentRun.clocks), _ToAssignment(currentRun.outputs)))
								failing.add((_ToAssignment(otherRun.inputs), _ToAssignment(otherRun.clocks), _ToAssignment(otherRun.outputs)))
						else:
							prefixes[prefixInputs] = (prefixOutputs, runIndex)

				if valid:
					succeeded += 1
				else:
					error(f'Category {categoryName} cell {cellName} fault {faultName} failed prefix validation')
					failed.append((categoryName, cellName, faultName, failing))
			cells += 1

	info(f'Validated {len(failed) + succeeded} faults of {cells} cells')
	info(f'  Succeeded: {succeeded}')
	info(f'  Failed: {len(failed)}')
	for categoryName, cellName, faultName, failing in failed:
		info(f'    - {categoryName}/{cellName}/{faultName}')
		for inputs, clocks, outputs in failing:
			inputsText = ', '.join(f'{inputName}={inputValue}' for inputName, inputValue in inputs)
			clocksText = ', '.join(f'{clockName}={clockValue}' for clockName, clockValue in clocks)
			outputsText = ', '.join(f'{outputName}={outputValue}' for outputName, outputValue in outputs)
			info(f'        {inputsText}, {clocksText}, {outputsText}')

	if len(failed) > 0:
		error('Some cells failed prefix validation. Exiting!')
		exit(1)

	info(f'------------------------ TRIMMING SIMULATIONS -----------------------')
	trimmedData = _TrimSimulations(maskedData, goldenData, differenceData)
	maskedData = None

	if DebugOptions.ExportTrimmedSimulations in debugOptions:
		writePath = outputPath / f'2-trimmed-simulations.data.json'
		info(f'Exporting trimmed simulation data to {writePath}')
		with open(writePath, 'wt', encoding='utf-8') as stream:
			json.dump(trimmedData.ToDict(), stream, indent=4)

	if DebugOptions.FinishAfterTrimmedSimulations in debugOptions:
		return

	info(f'------------------------ EVALUATING FAULT STATUSES -----------------------')
	finalData, finalStatus = _ReduceAndEvaluateFaults(trimmedData, differenceData)
	trimmedData = None

	info(f'------------------------ EXPORTING FAULT DATA -----------------------')

	open_func = gzip.open if options.output_compress else open
	indent = 4 if (not options.output_compress) and options.verbose else None

	writePath = outputPath / f'fault.data.json{".gz" if options.output_compress else ""}'
	info(f'Exporting fault data to {writePath}')
	with open_func(writePath, 'wt', encoding='utf-8') as stream:
		json.dump(finalData.ToDict(), stream, indent=indent)

	writePath = outputPath / f'fault.status.json{".gz" if options.output_compress else ""}'
	info(f'Exporting fault statuses to {writePath}')
	with open_func(writePath, 'wt', encoding='utf-8') as stream:
		json.dump(finalStatus.ToDict(), stream, indent=indent)

	info(f'------------------------ FINISHED -----------------------')


def _ComputeDifferences(golden: SimulationData, faulty: SimulationCategory) -> Tuple[SimulationCategory, SimulationCategory]:
	def _ToDiff(golden: str, faulty: str) -> str:
		return ''.join([{
			'00': '_', '01': 'D', '0X': 'X',
			'10': 'D', '11': '_', '1X': 'X',
			'X0': '_', 'X1': '_', 'XX': '_',
		}.get(g + f) for g, f in zip(golden, faulty)])

	def _ToMasked(golden: str, faulty: str) -> str:
		return ''.join([{
			'00': '0', '01': '1', '0X': 'X',
			'10': '0', '11': '1', '1X': 'X',
			'X0': 'X', 'X1': 'X', 'XX': 'X',
		}.get(g + f) for g, f in zip(golden, faulty)])

	def _ApplyUnknowns(run: SimulationRun, unknowns: Dict[LookupKey, Dict[str, str]]) -> Assignment:
		outputs = { name: '' for name in run.outputs.keys() }
		for i in range(0, _GetLength(run)):
			inputs = _ExtractPrefix(run.inputs, i + 1)
			clocks = _ExtractPrefix(run.clocks, i + 1)
			prefix = _ToLookupKey(inputs, clocks)
			for output, original in run.outputs.items():
				outputs[output] += unknowns.get(prefix, {}).get(output, original[i])
		return outputs

	resultMasked = SimulationCategory()
	resultDifferences = SimulationCategory()
	for cellName, goldenCell in golden.cells.items():
		# Assume only one fault in golden model exists: Take the first fault
		goldenRuns = next(iter(goldenCell.faults.values())).runs
		goldenLookup = { _ToLookupKey(run.inputs, run.clocks): run.outputs for run in goldenRuns }

		for categoryName, faultyCategory in faulty.categories.items():
			for faultName, fault in faultyCategory.cells[cellName].faults.items():
				# There can be simulations resulting in an unknown output, but not in others.
				# First identify all unknowns and then apply them to the patterns as additional mask.
				unknownMasks: Dict[LookupKey, Dict[str, str]] = defaultdict(dict)
				for run in fault.runs:
					length = _GetLength(run)
					for i in range(0, length):
						trimmedInputs = _ExtractPrefix(run.inputs, i + 1)
						trimmedClocks = _ExtractPrefix(run.clocks, i + 1)
						extractedOutputs = _ExtractIndex(run.outputs, i)
						unknownOutputs = { name: value for name, value in extractedOutputs.items() if value == 'X' }
						unknownMasks[_ToLookupKey(trimmedInputs, trimmedClocks)].update(unknownOutputs)

				maskedRuns: List[SimulationRun] = []
				differenceRuns: List[SimulationRun] = []
				for faultyRun in fault.runs:
					try:
						goldenOutputs = goldenLookup[_ToLookupKey(faultyRun.inputs, faultyRun.clocks)]
						faultyOutputs = _ApplyUnknowns(faultyRun, unknownMasks)

						outputs = faultyRun.outputs.keys()
						masked = { name: _ToMasked(goldenOutputs[name], faultyOutputs[name]) for name in outputs }
						maskedRuns.append(SimulationRun(
							jobId=faultyRun.jobId,
							inputType=faultyRun.inputType,
							inputs=faultyRun.inputs,
							clocks=faultyRun.clocks,
							outputs=masked
						))
						differences = { name: _ToDiff(goldenOutputs[name], faultyOutputs[name]) for name in outputs }
						differenceRuns.append(SimulationRun(
							jobId=faultyRun.jobId,
							inputType=faultyRun.inputType,
							inputs=faultyRun.inputs,
							clocks=faultyRun.clocks,
							outputs=differences
						))
					except KeyError as error:
						raise ValueError(f'No corresponding simulation for inputs {faultyRun.inputs}' \
					   		f' and clocks {faultyRun.clocks} was found') from error

				resultMasked.SetFault(categoryName, cellName, faultName, SimulationFault(maskedRuns))
				resultDifferences.SetFault(categoryName, cellName, faultName, SimulationFault(differenceRuns))

	return resultMasked, resultDifferences

def _TrimSimulations(faulty: SimulationCategory, golden: SimulationData, differences: SimulationCategory) -> SimulationCategory:
	result = SimulationCategory()
	for categoryName, category in faulty.categories.items():
		debug(f'Category {categoryName}')
		for cellName, cell in category.cells.items():
			# Create a prefix lookup of the golden simulations for minimization
			goldenRuns = next(iter(golden.cells[cellName].faults.values())).runs
			goldenLookup = {}
			for run in goldenRuns:
				length = _GetLength(run)
				for i in range(1, length):
					inputs = _ExtractPrefix(run.inputs, i)
					clocks = _ExtractPrefix(run.clocks, i)
					outputs = _ExtractPrefix(run.outputs, i)
					goldenLookup[_ToLookupKey(inputs, clocks)] = outputs

			# Trim, minimize and filter all the runs for each fault
			for faultName, fault in cell.faults.items():
				faultDifferences = differences.GetFault(categoryName, cellName, faultName).runs
				trimmedRuns = _TrimRuns(fault.runs, faultDifferences)
				minimizedRuns = _MinimizeRuns(trimmedRuns, goldenLookup)
				finalRuns = _RemovePrefixRuns(minimizedRuns)
				result.SetFault(categoryName, cellName, faultName, SimulationFault(finalRuns))

			countOldFaults = len([ fault for fault in faulty.GetCell(categoryName, cellName).faults.values() if len(fault.runs) > 0 ])
			countNewFaults = len([ fault for fault in result.GetCell(categoryName, cellName).faults.values() if len(fault.runs) > 0 ])
			countOldRuns = sum([ len(fault.runs) for fault in faulty.GetCell(categoryName, cellName).faults.values() ])
			countNewRuns = sum([ len(fault.runs) for fault in result.GetCell(categoryName, cellName).faults.values() ])

			if countNewFaults > 0:
				debug(f'    Filtered cell {cellName}: {countOldFaults} faults / {countOldRuns} runs ->' \
		  			f' {countNewFaults} faults ({100.0*countNewFaults/countOldFaults:.2f} %) remaining /' \
					f' {countNewRuns} runs ({100.0*countNewRuns/countOldRuns:.2f} %) remaining')
			else:
				debug(f'    Filtered cell {cellName}: {countOldFaults} faults / {countOldRuns} runs -> '\
		  			f'all faults untestable')
	return result


def _TrimRuns(runs: List[SimulationRun], differences: List[SimulationRun]) -> List[SimulationRun]:
	""" Trims simulation runs such that either the first difference, or the first don't care difference is reached.
	Empty runs are removed after trimming.

	Example results from multiple faults:
	- I=0,  O=0  (DIFF=_)  -> I=,   O=  (empty and removed)
	- I=1,  O=X  (DIFF=X)  -> I=1,  O=X
	- I=0,  O=1  (DIFF=D)  -> I=0,  O=1
	- I=01, O=01 (DIFF=__) -> I=,   O=  (empty and removed)
	- I=01, O=X1 (DIFF=X_) -> I=0,  O=X
	- I=01, O=11 (DIFF=D_) -> I=0,  O=1
	- I=10, O=10 (DIFF=__) -> I=,   O=  (empty and removed)
	- I=10, O=1X (DIFF=_X) -> I=10, O=1X
	- I=10, O=11 (DIFF=_D) -> I=10, O=11
	- I=10, O=0X (DIFF=DX) -> I=1,  O=0
	- I=10, O=XX (DIFF=XX) -> I=1,  O=X
	- I=10, O=11 (DIFF=XD) -> I=10, O=11

	>>> _TrimRuns([ \
	        SimulationRun(jobId=1, inputType='static', inputs={'I': '0X'}, clocks={'C': 'PP'}, outputs={'O': 'X1'}), \
	        SimulationRun(jobId=2, inputType='static', inputs={'I': '1X'}, clocks={'C': 'PP'}, outputs={'O': 'X1'}), \
	        SimulationRun(jobId=3, inputType='transition', inputs={'I': '01X'}, clocks={'C': 'PPP'}, outputs={'O': 'X11'}), \
	        SimulationRun(jobId=4, inputType='transition', inputs={'I': '10X'}, clocks={'C': 'PPP'}, outputs={'O': 'X11'}), \
	    ], [ \
	        SimulationRun(jobId=1, inputType='static', inputs={'I': '0X'}, clocks={'C': 'PP'}, outputs={'O': '_D'}), \
	        SimulationRun(jobId=2, inputType='static', inputs={'I': '1X'}, clocks={'C': 'PP'}, outputs={'O': '__'}), \
	        SimulationRun(jobId=3, inputType='transition', inputs={'I': '01X'}, clocks={'C': 'PPP'}, outputs={'O': '_D_'}), \
	        SimulationRun(jobId=4, inputType='transition', inputs={'I': '10X'}, clocks={'C': 'PPP'}, outputs={'O': '__D'}), \
	    ])
	... # doctest: +NORMALIZE_WHITESPACE
	[job: 1, static inputs: I=0X, clocks: C=PP, outputs: O=X1, \
	job: 3, transition inputs: I=01, clocks: C=PP, outputs: O=X1, \
	job: 4, transition inputs: I=10X, clocks: C=PPP, outputs: O=X11]
	>>> _TrimRuns([ \
	        SimulationRun(jobId=1, inputType='static', inputs={'I': '0X'}, clocks={'C': 'PP'}, outputs={'O': 'X1'}), \
	        SimulationRun(jobId=2, inputType='static', inputs={'I': '1X'}, clocks={'C': 'PP'}, outputs={'O': 'XX'}), \
	        SimulationRun(jobId=3, inputType='transition', inputs={'I': '01X'}, clocks={'C': 'PPP'}, outputs={'O': 'X1X'}), \
	        SimulationRun(jobId=4, inputType='transition', inputs={'I': '10X'}, clocks={'C': 'PPP'}, outputs={'O': 'XX1'}), \
	    ], [ \
	        SimulationRun(jobId=1, inputType='static', inputs={'I': '0X'}, clocks={'C': 'PP'}, outputs={'O': '_D'}), \
	        SimulationRun(jobId=2, inputType='static', inputs={'I': '1X'}, clocks={'C': 'PP'}, outputs={'O': '_X'}), \
	        SimulationRun(jobId=3, inputType='transition', inputs={'I': '01X'}, clocks={'C': 'PPP'}, outputs={'O': '_DX'}), \
	        SimulationRun(jobId=4, inputType='transition', inputs={'I': '10X'}, clocks={'C': 'PPP'}, outputs={'O': '_XD'}), \
	    ])
	... # doctest: +NORMALIZE_WHITESPACE
	[job: 1, static inputs: I=0X, clocks: C=PP, outputs: O=X1, \
	job: 2, static inputs: I=1X, clocks: C=PP, outputs: O=XX, \
	job: 3, transition inputs: I=01, clocks: C=PP, outputs: O=X1, \
	job: 4, transition inputs: I=10X, clocks: C=PPP, outputs: O=XX1]
	"""
	faultyLookup = {}
	trimmedRuns: List[SimulationRun] = []
	for runFaulty, runDifferences in zip(runs, differences):
		inputLength = len(list(runFaulty.inputs.values())[0])
		noMatch = inputLength + 1
		newLength = noMatch
		for outputName in runFaulty.outputs.keys():
			for match in REGEX_PROPAGATION.finditer(runDifferences.outputs[outputName]):
				newLength = min(newLength, match.end())
				break
		if newLength == noMatch:
			# Found no fault propagation => find general difference
			newLength = noMatch
			for outputName in runFaulty.outputs.keys():
				for match in REGEX_DIFFERENCE.finditer(runDifferences.outputs[outputName]):
					newLength = min(newLength, match.end())
					break
		if newLength == noMatch:
			# Found neither propagation nor difference => remove
			continue

		trimmedInputs = _ExtractPrefix(runFaulty.inputs, newLength)
		trimmedClocks = _ExtractPrefix(runFaulty.clocks, newLength)
		trimmedOutputs = _ExtractPrefix(runFaulty.outputs, newLength)
		for port, values in trimmedInputs.items():
			assert len(values) == newLength, f"Input {port} with \"{values}\" doesn't have expected length of {newLength}"
		for port, values in trimmedClocks.items():
			assert len(values) == newLength, f"Clock {port} with \"{values}\" doesn't have expected length of {newLength}"
		for port, values in trimmedOutputs.items():
			assert len(values) == newLength, f"Output {port} with \"{values}\" doesn't have expected length of {newLength}"

		if _ToLookupKey(trimmedInputs, trimmedClocks) in faultyLookup:
			assert trimmedOutputs == faultyLookup[_ToLookupKey(trimmedInputs, trimmedClocks)]
			continue  # Sequence is already in the final set of runs

		faultyLookup[_ToLookupKey(trimmedInputs, trimmedClocks)] = trimmedOutputs
		trimmedRuns.append(SimulationRun(
			jobId=runFaulty.jobId,
			inputType=runFaulty.inputType,
			inputs=trimmedInputs,
			clocks=trimmedClocks,
			outputs=trimmedOutputs
		))

	return trimmedRuns


def _MinimizeRuns(runs: List[SimulationRun], goldenLookup: Dict[LookupKey, Dict[str, str]]):
	""" Removes simulation runs that are subsumed by other simulation runs that are shorter or more generic.

	Example results from multiple faults:
	- I=1,  O=0  (DIFF=D)  subsumes         I=01,  O=00  (DIFF=_D ) using lookup 1
	- I=1,  O=0  (DIFF=D)  does not subsume I=01,  O=X0  (DIFF=XD )
	- I=01, O=00 (DIFF=_D) subsumes         I=001, O=0X0 (DIFF=_XD) using lookup 1
	- I=0X, O=01 (DIFF=_D) subsumes         I=100, O=101 (DIFF=__D) using lookup 2

	With good lookup:
	1) I=0, O=0
	2) I=1, O=1

	>>> lookup = { \
		    _ToLookupKey({'I': '0'}, {'C': 'P'}): {'O': 'X'}, \
		    _ToLookupKey({'I': '1'}, {'C': 'P'}): {'O': 'X'}, \
		    _ToLookupKey({'I': '0X'}, {'C': 'PP'}): {'O': 'X0'}, \
		    _ToLookupKey({'I': '1X'}, {'C': 'PP'}): {'O': 'X1'}, \
		    _ToLookupKey({'I': '01'}, {'C': 'PP'}): {'O': 'X0'}, \
		    _ToLookupKey({'I': '10'}, {'C': 'PP'}): {'O': 'X1'}, \
		    _ToLookupKey({'I': '01X'}, {'C': 'PPP'}): {'O': 'X01'}, \
		    _ToLookupKey({'I': '10X'}, {'C': 'PPP'}): {'O': 'X10'}, \
		}
	>>> _MinimizeRuns([ \
	        SimulationRun(jobId=1, inputType='static', inputs={'I': '0X'}, clocks={'C': 'PP'}, outputs={'O': 'X1'}), \
	        SimulationRun(jobId=3, inputType='transition', inputs={'I': '01'}, clocks={'C': 'PP'}, outputs={'O': 'X1'}), \
	        SimulationRun(jobId=4, inputType='transition', inputs={'I': '10X'}, clocks={'C': 'PPP'}, outputs={'O': 'X11'}), \
	    ], lookup)
	... # doctest: +NORMALIZE_WHITESPACE
	... # Stuck-At fault should ideally be reduced to only a single static test
	[job: 1, static inputs: I=0X, clocks: C=PP, outputs: O=X1]
	>>> _MinimizeRuns([ \
	        SimulationRun(jobId=1, inputType='static', inputs={'I': '0X'}, clocks={'C': 'PP'}, outputs={'O': 'X1'}), \
	        SimulationRun(jobId=2, inputType='static', inputs={'I': '1X'}, clocks={'C': 'PP'}, outputs={'O': 'XX'}), \
	        SimulationRun(jobId=3, inputType='transition', inputs={'I': '01'}, clocks={'C': 'PP'}, outputs={'O': 'X1'}), \
	        SimulationRun(jobId=4, inputType='transition', inputs={'I': '10X'}, clocks={'C': 'PPP'}, outputs={'O': 'XX1'}), \
	    ], lookup)
	... # doctest: +NORMALIZE_WHITESPACE
	[job: 1, static inputs: I=0X, clocks: C=PP, outputs: O=X1, \
	job: 2, static inputs: I=1X, clocks: C=PP, outputs: O=XX]
	"""
	def _EqualUnderDontCares(current: Dict[str, str], other: Dict[str, str]):
		""" Checks for equality while allowing the other inputs to have don't cares. """
		# Check for conflicting values in inputs.
		for key in current.keys():
			if any([{
				'00': 0, '01': 1, '0X': 0,
				'10': 1, '11': 0, '1X': 0,
				'X0': 1, 'X1': 1, 'XX': 0,
			}.get(g + f) for g, f in zip(current[key], other[key])]):
				return False
		return True

	# Trim prefix of runs which are equivalent another faulty run prefixed with golden-like cell behaviour.
	# The iteration is in ascending length to find all prefixes.
	#
	# minimizedRuns stores the length of the runs for algorithm speedup.
	minimizedRuns: List[Tuple[int, SimulationRun]] = []
	for run in sorted(runs, key=lambda run: _GetLength(run)):
		length = _GetLength(run)
		redundant = False
		for otherLength, otherRun in minimizedRuns:
			if otherLength > length:
				continue

			if otherLength < length:
				# Check if there is a prefix that could be prepended to create this run.
				prefixInputs = _ExtractPrefix(run.inputs, length - otherLength)
				prefixClocks = _ExtractPrefix(run.clocks, length - otherLength)
				prefixOutputs = _ExtractPrefix(run.outputs, length - otherLength)
				prefix = _ToLookupKey(prefixInputs, prefixClocks)
				if prefix not in goldenLookup and prefixOutputs != goldenLookup[prefix]:
					continue

			# Check if this run is a combination of the prefix and the other run.
			suffixInputs = _ExtractSuffix(run.inputs, otherLength)
			suffixClocks = _ExtractSuffix(run.clocks, otherLength)
			suffixOutputs = _ExtractSuffix(run.outputs, otherLength)

			# The propagation has to be treated differently such that no propagations are removed
			# by runs that don't have a propagation but unknown outputs instead.
			suffixPrePropagation = _ExtractPrefix(suffixOutputs, otherLength - 1)
			suffixPropagation = _ExtractSuffix(suffixOutputs, 1)
			otherPrePropagation = _ExtractPrefix(otherRun.outputs, otherLength - 1)
			otherPropagation = _ExtractSuffix(otherRun.outputs, 1)
			if _EqualUnderDontCares(suffixInputs, otherRun.inputs) \
					and _EqualUnderDontCares(suffixClocks, otherRun.clocks) \
					and _EqualUnderDontCares(suffixPrePropagation, otherPrePropagation) \
					and suffixPropagation == otherPropagation:
				redundant = True
				break

		if not redundant:
			minimizedRuns.append((length, run))

	return list([run for _, run in minimizedRuns])


def _RemovePrefixRuns(runs: List[SimulationRun]):
	result: List[SimulationRun] = []

	# First longer sequences to detect when shorter sequences can be omitted
	for run in sorted(runs, key=_GetLength, reverse=True):
		length = _GetLength(run)
		for otherRun in result:
			inputs = _ExtractPrefix(otherRun.inputs, length)
			clocks = _ExtractPrefix(otherRun.clocks, length)
			outputs = _ExtractPrefix(otherRun.outputs, length)
			if run.inputs == inputs and run.clocks == clocks \
					and run.outputs == outputs:
				# This sequence is a prefix, does not propagate a fault
				# by construction (trimming) and therefore not required.
				break
		else:
			result.append(run)

	result.reverse()
	return result


def _ReduceAndEvaluateFaults(data: SimulationCategory, differences: SimulationCategory):
	def _Equivalent(aRuns: List[SimulationRun], bRuns: List[SimulationRun]):
		if len(aRuns) != len(bRuns):
			return False

		for a, b in zip(aRuns, bRuns):
			if a.inputs != b.inputs or a.clocks != b.clocks or a.outputs != b.outputs:
				return False

		return True

	faultData = SimulationCategory()
	faultStatus = ClassificationCategory()
	for categoryName, category in data.categories.items():
		for cellName, cell in category.cells.items():
			for faultName, fault in cell.faults.items():
				differenceRuns = differences.GetFault(categoryName, cellName, faultName).runs

				differencePropagation = False
				differenceUnknown = False
				for run in differenceRuns:
					for value in run.outputs.values():
						if next(REGEX_PROPAGATION.finditer(value), None) is not None:
							differencePropagation = True
							break
						if next(REGEX_DIFFERENCE.finditer(value), None) is not None:
							differenceUnknown = True
							continue
					if differencePropagation:
						break

				if differencePropagation:
					status = ClassificationStatus.Detected
				elif differenceUnknown:
					status = ClassificationStatus.Undefined
				else:
					# Don't add undetected faults to the final fault model
					faultStatus.SetFault(categoryName, cellName, faultName, ClassificationStatus.Undetected)
					continue

				equivalent = False
				for existingCategory in faultData.categories.values():
					for existingFaultName, existingFault in existingCategory.cells[cellName].faults.items():
						if _Equivalent(fault.runs, existingFault.runs):
							faultStatus.SetFault(categoryName, cellName, faultName,
								ClassificationStatus.EquivalentTo, existingFaultName)
							equivalent = True
							break
					if equivalent:
						break

				if not equivalent:
					faultData.SetFault(categoryName, cellName, faultName, fault)
					faultStatus.SetFault(categoryName, cellName, faultName, status)

	return faultData, faultStatus


def _ToAssignment(values: Dict[str, str]) -> Assignment:
	return frozenset(values.items())

def _ToLookupKey(inputs: Dict[str, str], clocks: Dict[str, str]) -> LookupKey:
	return (_ToAssignment(inputs), _ToAssignment(clocks))

def _ExtractPrefix(values: Dict[str, str], length: int) -> Dict[str, str]:
	return { name: value[0:length] for name, value in values.items() }

def _ExtractSuffix(values: Dict[str, str], length: int) -> Dict[str, str]:
	return { name: value[-length:] for name, value in values.items() }

def _ExtractIndex(values: Dict[str, str], index: int) -> Dict[str, str]:
	return { name: value[index] for name, value in values.items() }

def _GetLength(run: SimulationRun) -> int:
	return max(chain(
		[len(values) for values in run.inputs.values()],
		[len(values) for values in run.clocks.values()],
		[len(values) for values in run.outputs.values()]
	))

if __name__ == '__main__':
	Main()
