import logging
import argparse
from typing import List, Tuple, Any


def InitializeLogging(verbose=False):
	ownLoggers = ['__main__', 'Liberty.', 'Logic.', 'Simulation.', 'Spice.', 'Udfm.', 'Workarounds.']

	if verbose:
		logging.basicConfig(level=logging.DEBUG, format='[%(asctime)s][%(levelname)s][%(module)s/%(funcName)s:%(lineno)d] %(message)s')
		for name in logging.root.manager.loggerDict.keys():
			if not any([name.startswith(prefix) for prefix in ownLoggers]):
				logging.getLogger(name).setLevel(logging.ERROR)
	else:
		logging.basicConfig(level=logging.INFO, format='[%(asctime)s][%(levelname)s] %(message)s')


def ParseOptions(parser, choices: List[Tuple[str, Any]]):
	helpParser = argparse.ArgumentParser(add_help=False)
	helpParser.add_argument('-h', '--help', dest='help', nargs='*', default=None, help='Print help text')
	helpOptions, _ = helpParser.parse_known_args()
	if helpOptions.help is not None:
		topics = helpOptions.help

		helpMatched = [False]
		def _HelpEnum(topic, enum, text):
			if not topic in topics:
				return

			helpMatched[0] = True
			print(text)
			for e in enum:
				print(f'	{e}')

		for option, type in choices:
			_HelpEnum(option, type, f'Supported {option} option values:')

		if not helpMatched[0]:
			parser.print_help()
		exit()

	options = parser.parse_args()
	InitializeLogging(verbose=options.verbose)

	return options


def ParseEnum(option, type, value):
	try:
		return type(value)
	except:
		raise ValueError(f'Unsupported {option} option value "{value}"')


def ParseEnumList(option, type, values):
	def _Split(params):
		for value in params:
			for part in value.split(' '):
				if part == '':
					continue
				yield part

	return [ParseEnum(option, type, value) for value in _Split(values)]
