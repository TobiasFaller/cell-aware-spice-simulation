import argparse
import json
import gzip
import pathlib
from logging import info, debug
from collections import defaultdict
from enum import StrEnum

from Logging.Logging import ParseOptions, ParseEnumList
from Simulation.SimulationData import SimulationCategory


class DebugOptions(StrEnum):
	pass


def Main() -> None:
	parser = argparse.ArgumentParser(
		prog='FaultDataToJson',
		description='Convert the fault data to the JSON format',
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
				output[cellName][faultName] = [
					{
						'inputs': run.inputs,
						'outputs': run.outputs,
					} for run in fault.runs
				]

	info(f'------------------------ EXPORTING JSON FORMAT -----------------------')
	open_func = gzip.open if options.output_compress else open
	indent = 4 if (not options.output_compress) and options.verbose else None

	info(f'Writing json format to {options.output_path}')
	with open_func(options.output_path, 'wt', encoding='utf-8') as stream:
		json.dump(output, stream, indent=indent)

	info(f'------------------------ FINISHED -----------------------')


if __name__ == '__main__':
	Main()
