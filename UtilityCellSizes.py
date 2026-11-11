import json
import argparse
import pathlib
import matplotlib.pyplot as pyplot
import matplotlib.legend as plegend

from typing import Dict, Any, List
from enum import StrEnum
from logging import info, debug
from PIL import Image

from Logging.Logging import ParseOptions, ParseEnumList


STYLE='light'

CellSizes = Dict[str, Dict[str, int]]


class DebugOptions(StrEnum):
	pass


DESIGNS = {
	'light': {
		'pyplot': {
			'hatch.linewidth': 3,
			'hatch.color': '#00000055',
		},
		'groups': { 'color': 'black', 'linestyle': '--', 'linewidth': 1.5 },
		'status': {
			#'inputs':  { 'color': '#1f77b4' },
			#'outputs': { 'color': "#ffab0e" },
			#'transistors': { 'color': "#f33535" },

			'inputs':   { 'color': '#6C8EBF', 'linewidth': 1.0 },
			'outputs': { 'color': '#666666', 'linewidth': 1.0 },
			'transistors': { 'color': '#B85450', 'linewidth': 1.0 },
		}
	},

	'dark': {
		'pyplot': {
			'hatch.linewidth': 3,
			'hatch.color': '#00000099',
			'text.color': 'white',
			'savefig.edgecolor': 'white',
			'patch.edgecolor': 'white',
			'boxplot.whiskerprops.color': 'white',
			'boxplot.flierprops.markeredgecolor': 'white',
			'boxplot.capprops.color': 'white',
			'boxplot.boxprops.color': 'white',
			'axes.labelcolor': 'white',
			'axes.edgecolor': 'white',
			'ytick.color': 'white',
			'xtick.color': 'white',
			'legend.labelcolor': 'black',
		},
		'groups': { 'color': 'white', 'linestyle': '--' },
		'status': {
			#'inputs':  { 'color': '#1f77b4' },
			#'outputs': { 'color': "#ffab0e" },
			#'transistors': { 'color': "#f33535" },

			'inputs':   { 'color': '#6C8EBF', 'linewidth': 1.0 },
			'outputs': { 'color': '#666666', 'linewidth': 1.0 },
			'transistors': { 'color': '#B85450', 'linewidth': 1.0 },
		}
	}
}


def Main() -> None:
	parser = argparse.ArgumentParser(
		prog='UtilityCellSizes',
		description='Exports cell sizes',
		add_help=True
	)
	parser.add_argument('--input', dest='input_path', type=str, required=True, help='Path to cell meta data file')
	parser.add_argument('--output', dest='output_path', type=str, required=True, help='Output plot to this path')
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

	info(f'------------------------ IMPORTING FAULT STATISTICS -----------------------')
	info(f'Loading cell statistics from {options.input_path}')
	with open(options.input_path, 'rt', encoding='utf-8') as stream:
		meta = json.load(stream)

	statistics = _GetStatistics(meta)
	info(f'Plotting statistics to {options.output_path}')
	_PlotStatistics(options.output_path, statistics, STYLE)


def _PlotStatistics(path: pathlib.Path, statistics: CellSizes, style) -> None:
	pyplot.rcParams.update(DESIGNS[style]['pyplot'])

	fig, ax1 = pyplot.subplots()
	fig.set_size_inches(6.5, 4.5)
	fig.tight_layout(pad=5)
	ax2 = ax1.twinx()
	ax1.set_zorder(2)
	ax2.set_zorder(1)

	statistics = dict([(cell, stats) \
			for cell, stats in sorted(statistics.items(), key=lambda elem: (_GetOrder(elem[0]))) \
			if not _IsIgnore(cell)])

	x_offset = 0
	x_labels = []
	x_ticks = []
	x_data = []
	x_groups = []
	for index, cellName in enumerate(statistics.keys()):
		if _HasLabel(cellName):
			x_groups.append([index])
			x_labels.append(_GetCellName(cellName))
		else:
			x_groups[-1].append(index)

	min_distance = 1 if len(statistics) <= 15 else 4
	for index, indices in enumerate(x_groups):
		x_distance = len(indices) if len(indices) > min_distance else min_distance
		x_ticks.append(x_offset + x_distance / 2.0)
		x_delta = x_distance / len(indices)
		for i in range(len(indices)):
			is_first = (index + i) == 0
			is_last = (index + i) == (len(statistics) - 1)
			x_data.append(x_offset + (i+0)*x_delta + (0.05 if not is_first else 0.0))
			x_data.append(x_offset + (i+1)*x_delta - (0.05 if not is_last else 0.0))
		x_offset += x_distance

	y_axis = []
	y_design = []
	y_data: List[List[float]] = []
	y_labels = []
	y_stats = []
	y_elements = []
	y_elem_axis = []
	for stat in next(iter(statistics.values())).keys():
		design = _GetCategoryDesign(stat)
		y_stats.append(stat)
		y_axis.append(ax1 if design['axis'] == 1 else ax2)
		y_design.append(DESIGNS[style]['status'][stat])
		y_data.append([])
		y_labels.append(_GetLabelName(stat))
		y_elem_axis.append(design['axis'] - 1)

	for stats in statistics.values():
		for index, (stat, value) in enumerate(stats.items()):
			y_data[index].append(value)
			y_data[index].append(value)

	axis_max = [0, 0]
	for axis, design, data, ax_index, stat in zip(y_axis, y_design, y_data, y_elem_axis, y_stats):
		offset = _GetShift(stat)
		elem, = axis.plot(x_data, [value + offset for value in data], **design, zorder=(5+ax_index))
		y_elements.append(elem)
		axis_max[ax_index] = max(axis_max[ax_index], int(max(data)))

	ax1.set_title('Cells')
	ax1.set_xlim(0, x_offset)
	ax1.set_xticks(x_ticks, labels=x_labels, rotation=90, ha='center')
	ax1.set_ylabel('Inputs / Outputs')
	ax1.set_yscale('linear')
	ax1.set_ylim(-0.05 * axis_max[0], axis_max[0] * 1.05)
	ax1.set_yticks(range(axis_max[0] + 1), labels=[f'{y}' for y in range(axis_max[0] + 1)])
	ax2.set_ylabel('Transistors')
	ax2.set_yscale('linear')
	#ax2.set_yticks(range(axis_max[1] + 1), labels=[f'{y}' for y in range(axis_max[1] + 1)])
	ax2.set_ylim(-0.05 * axis_max[1], axis_max[1] * 1.05)

	legend = plegend.Legend(ax1, y_elements, y_labels, loc='upper left')
	ax1.add_artist(legend)

	fig.savefig(path, dpi=300, transparent=True)

	padding = 10
	image = Image.open(path)
	boundingbox = image.getbbox()
	image = image.crop((
		boundingbox[0] - padding,
		boundingbox[1] - padding,
		boundingbox[2] + padding,
		boundingbox[3] + padding
	))
	image.save(path, format='PNG')


def _GetStatistics(meta: Dict[str, Any]) -> CellSizes:
	summary: CellSizes = {}
	for cellName, data in meta.items():
		inputs = len(data.get('inputs', [])) \
			+ len(data.get('sets', [])) \
			+ len(data.get('resets', [])) \
			+ len(data.get('clocks', []))
		outputs = len(data.get('outputs', []))
		transistors = data.get('transistors', 0)
		summary[cellName] = {
			'inputs': inputs,
			'outputs': outputs,
			'transistors': transistors,
		}
	return summary


def _GetCategoryDesign(label: str) -> Dict[str, Any]:
	return {
		'inputs':      { 'axis': 1 },
		'outputs':     { 'axis': 1 },
		'transistors': { 'axis': 2 },
	}.get(label)


def _GetLabelName(label: str) -> str:
	return {
		'inputs':      'Inputs',
		'outputs':     'Outputs',
		'transistors': 'Transistors',
	}.get(label)


def _GetShift(label: str) -> float:
	return {
		'inputs':      0.02,
		'outputs':     -0.02,
		'transistors': 0.0,
	}.get(label)


def _GetCellName(cell: str) -> str:
	for driver in [
		'_X16',
		'_X32',
		'_X1',
		'_X2',
		'_X3',
		'_X4',
		'_X8',
	]:
		if cell.endswith(driver):
			return cell[:-len(driver)]
	return cell


def _GetOrder(cell: str) -> int:
	cell = cell.lower()
	result = -1
	for index, name in enumerate([
		'buf', 'inv', 'clkbuf',
		'tbuf', 'tinv', 'tlat',
		'and', 'or', 'xor',
		'nand', 'nor', 'xnor',
		'mux', 'ha', 'fa',
		'aoi', 'oai',
		'dl', 'dll', 'dlh',
		'dff', 'dffs', 'dffr', 'dffrs',
	]):
		if cell.startswith(name):
			result = max(result, index)

	if result == -1:
		return 100
	else:
		return result

def _IsIgnore(cell: str) -> bool:
	cell = cell.lower()
	for ignored in [
		'fill',
		'antenna',
		'logic',
		'sdff', 'sdffs', 'sdffr', 'sdffrs',
	]:
		if ignored in cell:
			return True
	return False

def _HasLabel(cell: str) -> bool:
	cell = cell.lower()
	for driver in [
		'_x2',
		'_x3',
		'_x4',
		'_x8',
		'_x16',
		'_x32',
	]:
		if cell.endswith(driver):
			return False
	return True

if __name__ == '__main__':
	Main()
