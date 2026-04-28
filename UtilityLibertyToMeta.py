import argparse
import json
import itertools
import pathlib
from enum import StrEnum
from typing import List, Iterable
from logging import info, debug

from Liberty.CellImporter import ImportLibertyCells
from Logging.Logging import ParseOptions, ParseEnumList
from Logic.Logic import NormalizeEquation

class DebugOptions(StrEnum):
	pass


def _ExtractPinsFromEquation(equation: str, states: List[str]) -> Iterable[str]:
	equation = NormalizeEquation(equation)
	for name in equation.split(' '):
		name = name.strip()
		if name != '' and name not in states and name not in ['0', '1']:
			yield name

if __name__ == '__main__':

	parser = argparse.ArgumentParser(
		prog='UtilityLibertyToMeta',
		description='Exports meta data in JSON format about the liberty library',
		add_help=True
	)
	parser.add_argument('--library', dest='library', type=str, required=True, help='Configuration file for the cell library')
	parser.add_argument('--library-path', dest='library_path', type=str, required=True, help='Root directory of the cell library')
	parser.add_argument('--output', dest='output_path', type=str, required=True, help='Output path for generated meta JSON')
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

	info('------------------------ LOADING CELL INFO -----------------------')
	with open(options.library, 'rt', encoding='utf-8') as file:
		library = json.load(file)

	libraryPath = pathlib.Path(options.library_path)
	libertyPath = libraryPath / library['paths']['liberty']
	libertyData = ImportLibertyCells(libertyPath)

	info('------------------------ EXPORTING META DATA -----------------------')
	meta = {}
	for cell_name, cell in libertyData.items():
		inputs = cell.inputs
		outputs = cell.outputs

		clocks = cell.clocks
		clocks += cell.attributes['ff_clock'] if 'ff_clock' in cell.attributes else []
		clocks += cell.attributes['latch_enable'] if 'latch_enable' in cell.attributes else []

		sets = cell.attributes['ff_set'] if 'ff_set' in cell.attributes else []
		sets += cell.attributes['latch_set'] if 'latch_set' in cell.attributes else []

		resets = cell.attributes['ff_reset'] if 'ff_reset' in cell.attributes else []
		resets += cell.attributes['latch_reset'] if 'latch_reset' in cell.attributes else []

		states = cell.attributes['ff_state_internal'] if 'ff_state_internal' in cell.attributes else []
		states += cell.attributes['ff_state_positive'] if 'ff_state_positive' in cell.attributes else []
		states += cell.attributes['ff_state_negative'] if 'ff_state_negative' in cell.attributes else []
		states += cell.attributes['latch_state_positive'] if 'latch_state_positive' in cell.attributes else []
		states += cell.attributes['latch_state_negative'] if 'latch_state_negative' in cell.attributes else []

		clocks = list(set(itertools.chain(*[_ExtractPinsFromEquation(equation, states) for equation in clocks])))
		resets = list(set(itertools.chain(*[_ExtractPinsFromEquation(equation, states) for equation in resets])))
		sets = list(set(itertools.chain(*[_ExtractPinsFromEquation(equation, states) for equation in sets])))
		inputs = list(set(inputs) - set(clocks) -  set(sets) - set(resets))

		combinational = cell.attributes['combinational'] if 'combinational' in cell.attributes else False
		sequential = cell.attributes['sequential'] if 'sequential' in cell.attributes else False
		latch = cell.attributes['latch'] if 'latch' in cell.attributes else False
		flipflop = cell.attributes['flipflop'] if 'flipflop' in cell.attributes else False

		meta[cell_name] = {
			'inputs': inputs,
			'outputs': outputs,
			**({'clocks': clocks} if len(clocks) > 0 else {}),
			**({'sets': sets} if len(sets) > 0 else {}),
			**({'resets': resets} if len(resets) > 0 else {}),
			**({'combinational': True} if combinational else {}),
			**({'sequential': True} if sequential else {}),
			**({'latch': True} if latch else {}),
			**({'flipflop': True} if flipflop else {}),
		}

	with open(options.output_path, 'wt', encoding='utf-8') as stream:
		json.dump(meta, stream, indent=4)
