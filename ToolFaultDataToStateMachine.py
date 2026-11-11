import argparse
import json
import gzip
import pathlib

from typing import List, Dict, Any, Union
from enum import StrEnum
from dataclasses import dataclass, field
from dataclasses_json import dataclass_json, config
from logging import info, debug
from collections import defaultdict
from itertools import chain

from Logging.Logging import ParseOptions, ParseEnumList
from Simulation.SimulationData import SimulationCategory, SimulationRun


class DebugOptions(StrEnum):
	pass


def AlwaysExclude(value):
	return True


@dataclass_json
@dataclass
class Transition:
	toState: int = field(metadata=config(field_name='to-state'))
	inputs: Dict[str, str]
	clocks: Dict[str, str]
	outputs: Dict[str, str]
	propagations: List[str]
	unknowns: List[str]
	meta: Dict[str, str] = field(metadata=config(exclude=AlwaysExclude), default_factory=lambda: { 'runs': set() })


@dataclass_json
@dataclass
class State:
	transitions: List[Transition] = field(default_factory=lambda: [])
	meta: Dict[str, Any] = field(metadata=config(exclude=AlwaysExclude), default_factory=lambda: { 'runs': set() })


def Main() -> None:
	parser = argparse.ArgumentParser(
		prog='FaultDataToStateMachine',
		description='Convert the fault data to the state machine fault model format',
		add_help=True
	)
	parser.add_argument('--input', dest='input_path', type=str, required=True, help='Path to fault data file')
	parser.add_argument('--input-compressed', dest='input_compress', action='store_true', default=False, help='Assume input is compressed')
	parser.add_argument('--output', dest='output_path', type=str, required=True, help='Output fault model in JSON format')
	parser.add_argument('--output-compress', dest='output_compress', action='store_true', default=False, help='Enable compression for output format')
	parser.add_argument('-d', '--debug', dest='debug', type=str, nargs='*', default=[], help='Enable debug options (use -h debug for supported options)')
	parser.add_argument('-v', '--verbose', dest='verbose', action='store_true', default=False, help='Enable verbose logging')
	options = ParseOptions(parser, [
		('debug', DebugOptions),
	])

	info('------------------------ PARSING OPTIONS -----------------------')
	debugOptions = ParseEnumList('debug', DebugOptions, options.debug)
	for debugOption in debugOptions:
		debug(f'Enabling debug option {debugOption.value}')

	outputPath = pathlib.Path(options.output_path).parent
	outputPath.mkdir(parents=True, exist_ok=True)

	info(f'------------------------ IMPORTING FAULT DATA -----------------------')
	open_func = gzip.open if options.input_compress else open
	info(f'Loading fault data from {options.input_path}')
	with open_func(options.input_path, 'rt', encoding='utf-8') as stream:
		sourceData: SimulationCategory = SimulationCategory.FromDict(json.load(stream))

	info(f'------------------------ CONVERTING FAULT DATA -----------------------')
	output = defaultdict(dict)
	for categoryName, category in sourceData.categories.items():
		for cellName, cell in category.cells.items():
			for faultName, fault in cell.faults.items():
				states = _CreateStateMachine(cellName, faultName, fault.runs)
				output[cellName][faultName] = [state.to_dict() for state in states]

	info(f'------------------------ EXPORTING STATE MACHINE FORMAT -----------------------')
	open_func = gzip.open if options.output_compress else open
	indent = 4 if (not options.output_compress) and options.verbose else None

	info(f'Writing state machine format to {options.output_path}')
	with open_func(options.output_path, 'wt', encoding='utf-8') as stream:
		json.dump(output, stream, indent=indent)

	info(f'------------------------ FINISHED -----------------------')


def _CreateStateMachine(cellName: str, faultName: str, runs: List[SimulationRun]) -> List[State]:
	states: List[State] = [ State() ]  # First state (epsilon).

	def _GetOrCreateTransition(jobIndex: int, stateFrom: int, stateTo: Union[int, None], inputs: Dict[str, str], clocks: Dict[str, str], outputs: Dict[str, str]) -> Transition:
		for transition in states[stateFrom].transitions:
			if transition.inputs == inputs:
				if transition.outputs != outputs:
					textRuns = ', '.join(f'{index}' for index in (set([ jobIndex ]) | transition.meta['runs']))
					textInputs = ', '.join([ f'{input}={value}' for input, value in inputs.items() ])
					textClocks = ', '.join([ f'{clock}={value}' for clock, value in clocks.items() ])
					textOutputs = ', '.join([ f'{output}={value}' for output, value in outputs.items() ])
					textTransInputs = ', '.join([ f'{input}={value}' for input, value in transition.inputs.items() ])
					textTransClocks = ', '.join([ f'{clock}={value}' for clock, value in transition.clocks.items() ])
					textTransOutputs = ', '.join([ f'{output}={value}' for output, value in transition.outputs.items() ])
					raise ValueError(f'The simulation contains fault {faultName} for cell {cellName} where two or more runs'
		   				f' ({textRuns}) with same prefix but differing outputs:'
						f' {stateFrom}: {textInputs}, {textClocks} -> {textOutputs} vs {textTransInputs}, {textTransClocks} -> {textTransOutputs}')
				return transition

		if stateTo is None:
			stateTo = len(states)
			states.append(State())

		propagations = []
		unknowns = []
		for outputName, output in outputs.items():
			if output in ['*', 'x']:
				unknowns.append(outputName)
			if output in ['^', 'v']:
				propagations.append(outputName)

		transition = Transition(stateTo, inputs, clocks, outputs, propagations, unknowns)
		states[stateFrom].transitions.append(transition)
		return transition

	def _GetLength(run: SimulationRun) -> int:
		return max(chain(*[
			[len(input) for input in run.inputs.values()],
			[len(clock) for clock in run.clocks.values()],
			[len(output) for output in run.outputs.values()],
		]))

	# First encode the longer runs, then smaller ones.
	for run in sorted(runs, key=_GetLength, reverse=True):
		currentState = 0
		length = _GetLength(run)
		for timeframe in range(length):
			last = (timeframe + 1) == length
			inputs = { input: value[timeframe] for input, value in run.inputs.items() }
			clocks = { clock: value[timeframe] for clock, value in run.clocks.items() }
			outputs = { output: value[timeframe] for output, value in run.outputs.items() }

			transition = _GetOrCreateTransition(run.jobId, currentState, 0 if last else None, inputs, clocks, outputs)
			transition.meta['runs'].add(run.jobId)
			states[currentState].meta['runs'].add(run.jobId)
			currentState = transition.toState

		states[currentState].meta['runs'].add(run.jobId)

	for state in states:
		for transition in state.transitions:
			for outputName, output in transition.outputs.items():
				transition.outputs[outputName] = output \
					.replace('x', 'X') \
					.replace('*', 'X') \
					.replace('v', '0') \
					.replace('^', '1')

	return states


if __name__ == '__main__':
	Main()
