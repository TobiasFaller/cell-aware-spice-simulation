import argparse
import pathlib
import json
import gzip
from enum import StrEnum
from logging import info, debug

from Logging.Logging import ParseOptions, ParseEnum, ParseEnumList
from Simulation.SimulationData import SimulationCategory
from Udfm.Udfm import Udfm
from Udfm.UdfmExport import ExportFormat, ExportUdfm


class DebugOptions(StrEnum):
	pass


def Main() -> None:
	parser = argparse.ArgumentParser(
		prog='FaultDataToUdfm',
		description='Convert the fault data to the UDFM format',
		add_help=True
	)
	parser.add_argument('--input', dest='input_path', type=str, required=True, help='Path to fault data file')
	parser.add_argument('--input-compressed', dest='input_compress', action='store_true', default=False, help='Assume input is compressed')
	parser.add_argument('--output', dest='output_path', type=str, required=True, help='Output fault model in UDFM format')
	parser.add_argument('--output-compress', dest='output_compress', action='store_true', default=False, help='Enable compression for output format')
	parser.add_argument('--format', dest='output_format', type=str, default='simple-compact', help='Export format for the UDFM (use -h udfms for supported formats)')
	parser.add_argument('--library', dest='library_name', type=str, default='Unknown library', help='Name of the library for the extended UDFM format')
	parser.add_argument('-d', '--debug', dest='debug', type=str, nargs='*', default=[], help='Enable debug options (use -h debug for supported options)')
	parser.add_argument('-v', '--verbose', dest='verbose', action='store_true', default=False, help='Enable verbose logging')
	options = ParseOptions(parser, [
		('format', ExportFormat),
		('debug', DebugOptions),
	])

	info('------------------------ PARSING OPTIONS -----------------------')
	outputFormat = ParseEnum('format', ExportFormat, options.output_format)
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
	udfm = Udfm()
	for categoryName, category in sourceData.categories.items():
		for cellName, cell in category.cells.items():
			for faultName, fault in cell.faults.items():
				for run in fault.runs:
					length = len(next(iter(run.inputs.values())))
					if length == 1:
						testType = 'static'
					elif length == 2:
						testType = 'transition'
					elif length > 2:
						testType = 'dynamic'
					udfm.AddPattern(cellName, faultName, categoryName, testType, run.inputs, run.outputs)

	info(f'------------------------ EXPORTING UDFM -----------------------')
	open_func = gzip.open if options.output_compress else open

	info(f'Writing UDFM format to {options.output_path}')
	with open_func(options.output_path, 'wt', encoding='utf-8') as stream:
		ExportUdfm(stream, udfm, outputFormat, options.library_name)

	info(f'------------------------ FINISHED -----------------------')


if __name__ == '__main__':
	Main()
