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

class DebugOptions(StrEnum):
	pass

def _ReadSimulationData(path) -> SimulationCategory:
	open_func = gzip.open if options.input_compress else open

	info(f'Loading simulation data from {path}')
	with open_func(path, 'rt', encoding='utf-8') as stream:
		return path, SimulationCategory.FromDict(json.load(stream))


def _ReadSimulationStatus(path) -> Dict[str, Dict[str, List[str]]]:
	open_func = gzip.open if options.input_compress else open

	info(f'Loading simulation status from {path}')
	with open_func(path, 'rt', encoding='utf-8') as stream:
		return path, json.load(stream)


if __name__ == '__main__':
	parser = argparse.ArgumentParser(
		prog='UtilityMergeSimulationData',
		description='Merges multiple simulations into combined data files',
		add_help=True
	)
	parser.add_argument('--input-data', dest='input_data_path', type=str, required=True, action='append', default=[], help='Path to simulation data file')
	parser.add_argument('--input-status', dest='input_status_path', type=str, required=False, action='append', default=[], help='Path to simulation status file')
	parser.add_argument('--input-compressed', dest='input_compress', action='store_true', default=False, help='Assume input is compressed')
	parser.add_argument('--output', dest='output_path', type=str, required=True, help='Output path for simulation data and status file')
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

	info('------------------------ LOADING SIMULATION DATA -----------------------')
	simulationData = SimulationCategory()

	open_func = gzip.open if options.input_compress else open
	with multiprocessing.Pool(processes=(None if options.processes == 0 else options.processes)) as pool:
		sourceData: Iterable[Tuple[str, SimulationCategory]] = pool.imap_unordered(_ReadSimulationData, options.input_data_path)
		for path, source in sourceData:
			info(f'Processing simulation data from {path}')
			for categoryName, category in source.categories.items():
				for cellName, cell in category.cells.items():
					for faultName, fault in cell.faults.items():
						for run in fault.runs:
							simulationData.AddRun(run.jobId, categoryName, cellName, faultName,
								run.inputType, run.inputs, run.clocks, run.outputs)

	info('------------------------ LOADING SIMULATION STATUS -----------------------')
	simulationStatus: Dict[str, Dict[str, List[str]]] = defaultdict(lambda: defaultdict(list))

	open_func = gzip.open if options.input_compress else open
	with multiprocessing.Pool(processes=(None if options.processes == 0 else options.processes)) as pool:
		sourceStatus: Iterable[Tuple[str, Dict[str, Dict[str, List[str]]]]] = pool.imap_unordered(_ReadSimulationStatus, options.input_status_path)
		for path, source in sourceStatus:
			info(f'Processing simulation status from {path}')
			for cellName, cell in source.items():
				for faultName, fault in cell.items():
					simulationStatus[cellName][faultName] += fault

	info('------------------------ EXPORTING SIMULATION DATA -----------------------')
	open_func = gzip.open if options.output_compress else open
	indent = 4 if (not options.output_compress) and options.verbose else None

	writePath = outputPath / f'simulation.data.json{".gz" if options.output_compress else ""}'
	info(f'Exporting simulation data to {writePath}')
	with open_func(writePath, 'wt', encoding='utf-8') as stream:
		json.dump(simulationData.ToDict(), stream, indent=indent)

	writePath = outputPath / f'simulation.status.json{".gz" if options.output_compress else ""}'
	info(f'Exporting simulation status to {writePath}')
	with open_func(writePath, 'wt', encoding='utf-8') as stream:
		json.dump(simulationStatus, stream, indent=indent)
