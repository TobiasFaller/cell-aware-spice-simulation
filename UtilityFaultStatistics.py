import json
import argparse
import pathlib
import gzip
import numpy
import matplotlib.pyplot as pyplot
import matplotlib.legend as plegend

from typing import Dict, Any
from collections import defaultdict
from enum import StrEnum
from logging import info, debug

from Logging.Logging import ParseOptions, ParseEnum, ParseEnumList
from Simulation.ClassificationData import ClassificationCategory, ClassificationStatus
from Spice.SpiceFaultInjector import FaultType


STYLE='light'

ClassificationSummary = Dict[str, Dict[str, int]]


class DebugOptions(StrEnum):
	pass


def Main() -> None:
	parser = argparse.ArgumentParser(
		prog='UtilityFaultStatistics',
		description='Exports and plots fault statistics',
		add_help=True
	)
	parser.add_argument('--input', dest='input_path', type=str, required=True, help='Path to fault statistics file')
	parser.add_argument('--input-compressed', dest='input_compress', action='store_true', default=False, help='Assume input is compressed')
	parser.add_argument('--output', dest='output_path', type=str, required=True, help='Output plots to this path')
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
	open_func = gzip.open if options.input_compress else open
	info(f'Loading fault statistics from {options.input_path}')
	with open_func(options.input_path, 'rt', encoding='utf-8') as stream:
		classifications: ClassificationCategory = ClassificationCategory.FromDict(json.load(stream))

	info(f'Plotting both statistics to {options.output_path}')
	_PlotStatistics(options.output_path, _GetStatistics(classifications))


def _PlotStatistics(path: pathlib.Path, statistics: ClassificationSummary) -> None:
	if STYLE == 'light':
		pyplot.rcParams['hatch.linewidth'] = 3
		pyplot.rcParams['hatch.color'] = '#00000055'
		groupstyle = { 'color': 'black', 'linestyle': '--' }
	elif STYLE == 'dark':
		pyplot.rcParams['hatch.linewidth'] = 3
		pyplot.rcParams['hatch.color'] = '#00000099'
		pyplot.rcParams['text.color'] = 'white'
		pyplot.rcParams['savefig.edgecolor'] = 'white'
		pyplot.rcParams['patch.edgecolor'] = 'white'
		pyplot.rcParams['boxplot.whiskerprops.color'] = 'white'
		pyplot.rcParams['boxplot.flierprops.markeredgecolor'] = 'white'
		pyplot.rcParams['boxplot.capprops.color'] = 'white'
		pyplot.rcParams['boxplot.boxprops.color'] = 'white'
		pyplot.rcParams['axes.labelcolor'] = 'white'
		pyplot.rcParams['axes.edgecolor'] = 'white'
		pyplot.rcParams['ytick.color'] = 'white'
		pyplot.rcParams['xtick.color'] = 'white'
		pyplot.rcParams['legend.labelcolor'] = 'black'
		groupstyle = { 'color': 'white', 'linestyle': '--' }

	fig, ax1 = pyplot.subplots()
	fig.set_size_inches(10.0, 6.0)
	fig.tight_layout(pad=5)
	ax2 = ax1.twinx()

	space = 0.1
	groupspace = 1.0

	previousAxis = 1
	hoffset = 0.0
	hoffsets = []
	voffsets = []
	elements = []
	labels = []
	sortedStatistics = sorted(statistics.items(), key=lambda elem: (_GetCategoryDesign(elem[0])['order']))
	for index, (label, stats) in enumerate(sortedStatistics):
		design = _GetCategoryDesign(label)
		axis = ax1 if design['axis'] == 1 else ax2
		if design['axis'] == 2 and previousAxis == 1:
			pyplot.axvline(hoffset + groupspace / 2.0, **groupstyle)
			hoffset += groupspace
		elif index != 0:
			hoffset += space
		previousAxis = design['axis']

		voffset = 0.0
		rects = []
		for metric, value in stats.items():
			rects.append(axis.bar(hoffset + 0.5, value, 1.0, bottom=voffset, **_GetStatusDesign(metric)))
			voffset += value
		hoffset += 1.0
		elements += [ rects ]
		labels += [ stats.keys() ]
		voffsets += [ voffset ]
		hoffsets += [ hoffset - 0.5 ]

	ax1.set_title('Fault Classifications')
	ax1.set_xticks(hoffsets, [label for label, _ in sortedStatistics], rotation=45, ha='center')
	ax1.set_ylabel('Faults')
	ax1.set_yscale('linear')
	ax2.set_ylabel('Faults')
	ax2.set_yscale('linear')

	labelElements = elements[0]
	labelNames = [_GetLabelName(label) for label in labels[0]]
	legend = plegend.Legend(ax1, labelElements, labelNames, loc='upper left')
	ax1.add_artist(legend)

	fig.savefig(path, dpi=300, transparent=True)


def _GetStatistics(classifications: ClassificationCategory) -> ClassificationSummary:
	summary: ClassificationSummary = {}
	for categoryName, category in classifications.categories.items():
		summary[categoryName] = {
			'detected': 0,
			'equivalent-detected': 0,
			'undefined': 0,
			'equivalent-undefined': 0,
			'undetected': 0,
		}

		for cellName, cell in category.cells.items():
			for fault in cell.faults.values():
				equivalent = False
				while True:
					if fault.status == ClassificationStatus.EquivalentTo:
						equivalent = True
						for otherCategory in classifications.categories.values():
							if cellName not in otherCategory.cells:
								continue
							if fault.equivalent not in otherCategory.cells[cellName].faults:
								continue
							fault = otherCategory.cells[cellName].faults[fault.equivalent]
							break
						else:
							raise ValueError(f'Couldn\'t find fault {fault.equivalent}')
						continue

					status = {
						ClassificationStatus.Detected: 'detected',
						ClassificationStatus.Undetected: 'undetected',
						ClassificationStatus.Undefined: 'undefined',
					}.get(fault.status)
					summary[categoryName][f'equivalent-{status}' if equivalent else status] += 1
					break
	return summary


def _GetStatusDesign(status: str) -> Dict[str, Any]:
	""" Taken from https://matplotlib.org/stable/users/prev_whats_new/dflt_style_changes.html """
	return {
		'detected':              { 'color': '#1f77b4' },
		'equivalent-detected':   { 'color': '#8cbde0' },
		'undefined':             { 'color': "#ffab0e" },
		'equivalent-undefined':  { 'color': "#f3e45f" },
		'undetected':            { 'color': "#f33535" },
	}.get(status)


def _GetCategoryDesign(categoryName: str) -> Dict[str, Any]:
	return {
		str(FaultType.FaultFree):  { 'axis': 1, 'order': 1 },
		str(FaultType.StuckAt):    { 'axis': 1, 'order': 2 },
		str(FaultType.Open):       { 'axis': 1, 'order': 6 },
		str(FaultType.Bridge):     { 'axis': 2, 'order': 7 },
		str(FaultType.PortOpen):   { 'axis': 1, 'order': 4 },
		str(FaultType.PortBridge): { 'axis': 1, 'order': 5 },
		str(FaultType.Tdrive):     { 'axis': 1, 'order': 3 },
		str(FaultType.Tleak):      { 'axis': 1, 'order': 4 },
	}.get(categoryName)


def _GetLabelName(status: str) -> str:
	return {
		'detected':              'Detected',
		'equivalent-detected':   'Detected (equivalent)',
		'undefined':             'Undefined',
		'equivalent-undefined':  'Undefined (equivalent)',
		'undetected':            'Undetected',
	}.get(status)


if __name__ == '__main__':
	Main()
