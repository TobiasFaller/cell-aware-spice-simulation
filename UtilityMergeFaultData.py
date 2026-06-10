import argparse
import json
import pathlib
import gzip
import multiprocessing
from enum import StrEnum
from typing import List, Dict, Iterable, Tuple
from logging import info, debug
from collections import defaultdict

from Logging.Logging import ParseOptions, ParseEnumList
from Simulation.SimulationData import SimulationCategory
from Simulation.ClassificationData import ClassificationCategory

class DebugOptions(StrEnum):
	pass


def _ReadFaultData(path) -> SimulationCategory:
	open_func = gzip.open if options.input_compress else open

	info(f'Loading fault data from {path}')
	with open_func(path, 'rt', encoding='utf-8') as stream:
		return path, SimulationCategory.FromDict(json.load(stream))


def _ReadFaultStatus(path) -> ClassificationCategory:
	open_func = gzip.open if options.input_compress else open

	info(f'Loading fault status from {path}')
	with open_func(path, 'rt', encoding='utf-8') as stream:
		return path, ClassificationCategory.FromDict(json.load(stream))


if __name__ == '__main__':
	parser = argparse.ArgumentParser(
		prog='UtilityMergeFaultData',
		description='Merges multiple faullt models into combined data files',
		add_help=True
	)
	parser.add_argument('--input-data', dest='input_data_path', type=str, required=True, action='append', default=[], help='Path to fault data file')
	parser.add_argument('--input-status', dest='input_status_path', type=str, required=False, action='append', default=[], help='Path to fault status file')
	parser.add_argument('--input-compressed', dest='input_compress', action='store_true', default=False, help='Assume input is compressed')
	parser.add_argument('--output', dest='output_path', type=str, required=True, help='Output path for fault data and status file')
	parser.add_argument('--output-compressed', dest='output_compress', action='store_true', default=False, help='Generate compressed output')
	parser.add_argument('--processes', dest='processes', type=int, default=0, help='Set the ')
	parser.add_argument('-d', '--debug', dest='debug', type=str, nargs='*', default=[], help='Enable debug options (use -h debug for supported options)')
	parser.add_argument('-v', '--verbose', dest='verbose', action='store_true', default=False, help='Enable verbose logging')
	options = ParseOptions(parser, [
		('debug', DebugOptions),
	])

	info('------------------------ PARSING OPTIONS -----------------------')
	debugOptions = ParseEnumList('debug', DebugOptions, options.debug)
	for debugOption in debugOptions:
		debug(f'Enabling debug option {debugOption.value}')

	outputPath = pathlib.Path(options.output_path)
	outputPath.mkdir(parents=True, exist_ok=True)

	info('------------------------ LOADING FAULT DATA -----------------------')
	faultData = SimulationCategory()

	open_func = gzip.open if options.input_compress else open
	with multiprocessing.Pool(processes=(None if options.processes == 0 else options.processes)) as pool:
		sourceData: Iterable[Tuple[str, SimulationCategory]] = pool.imap_unordered(_ReadFaultData, options.input_data_path)
		for path, source in sourceData:
			info(f'Processing fault data from {path}')
			for categoryName, category in source.categories.items():
				for cellName, cell in category.cells.items():
					for faultName, fault in cell.faults.items():
						for run in fault.runs:
							faultData.AddRun(run.jobId, categoryName, cellName, faultName,
								run.inputType, run.inputs, run.clocks, run.outputs)

	info('------------------------ LOADING FAULT STATUS -----------------------')
	faultStatus = ClassificationCategory()

	open_func = gzip.open if options.input_compress else open
	with multiprocessing.Pool(processes=(None if options.processes == 0 else options.processes)) as pool:
		sourceStatus: Iterable[Tuple[str, ClassificationCategory]] = pool.imap_unordered(_ReadFaultStatus, options.input_status_path)
		for path, source in sourceStatus:
			info(f'Processing fault status from {path}')
			for categoryName, category in source.categories.items():
				for cellName, cell in category.cells.items():
					for faultName, fault in cell.faults.items():
						faultStatus.SetFault(categoryName, cellName, faultName,
						   	fault.status, fault.equivalent)

	info('------------------------ EXPORTING FAULT DATA -----------------------')
	open_func = gzip.open if options.output_compress else open
	indent = 4 if (not options.output_compress) and options.verbose else None

	writePath = outputPath / f'fault.data.json{".gz" if options.output_compress else ""}'
	info(f'Exporting fault data to {writePath}')
	with open_func(writePath, 'wt', encoding='utf-8') as stream:
		json.dump(faultData.ToDict(), stream, indent=indent)

	writePath = outputPath / f'fault.status.json{".gz" if options.output_compress else ""}'
	info(f'Exporting fault status to {writePath}')
	with open_func(writePath, 'wt', encoding='utf-8') as stream:
		json.dump(faultStatus.ToDict(), stream, indent=indent)
