from typing import Dict, List
from collections import defaultdict


def GetPatternTransitions(first: Dict[str, str], second: Dict[str, str]) -> Dict[str, str]:
	ports = dict([(port, None) for port in first.keys()] + [(port, None) for port in second.keys()]).keys()
	result: Dict[str, str] = {}
	for port in ports:
		if (first.get(port, 'X') + second.get(port, 'X')) in ['01', 'X1', '0X']:
			result[port] = 'rising'
		elif (first.get(port, 'X') + second.get(port, 'X')) in ['10', 'X0', '1X']:
			result[port] = 'falling'
	return result


def ConvertPatternsToFilteredSequence(pattern: List[Dict[str, str]], filter: List[str]) -> Dict[str, str]:
	if len(pattern) > 0:
		return { port: ''.join([values[port] for values in pattern]) for port in pattern[0].keys() if port in filter }
	else:
		return {}


def ExpandClocks(inputs: Dict[str, str], clocks: Dict[str, str], clockPolarities: Dict[str, str]):
	"""
	>>> ExpandClocks({'I': '01X'}, {'C': 'PPP'}, {'C': 'positive'})
	([0, 0, 0, 1, 1, 1, 2, 2, 2], {'I': '000111XXX'}, {'C': '010010010'})
	"""
	numberTimeframes = max([len(values) for values in inputs.values()])
	if len(clocks) == 0:
		return list(range(numberTimeframes)), inputs, clocks

	indexResult: List[int] = []
	inputResult: Dict[str, str] = dict.fromkeys(inputs.keys(), '')
	clockResult: Dict[str, str] = dict.fromkeys(clocks.keys(), '')
	for timeframe in range(numberTimeframes):
		isLastTimeframe = (timeframe == numberTimeframes - 1)
		isClockPulse = any([(clockValue[timeframe] == 'P') for clockValue in clocks.values()])
		expandedLength = 3 if isClockPulse else 1

		indexResult += [timeframe for _ in range(expandedLength)]
		for inputName, inputValue in inputs.items():
			inputResult[inputName] += inputValue[timeframe] * expandedLength
		for clockName, clockValue in clocks.items():
			if clockValue[timeframe] in '01':
				clockResult[clockName] += clockValue[timeframe] * expandedLength
			elif clockValue[timeframe] in 'P':
				clockExpanded = { None: 'XXX', 'positive': '010', 'negative': '101' }.get(clockPolarities[clockName])
				clockResult[clockName] += clockExpanded[0:expandedLength]

	return indexResult, inputResult, clockResult


def CompactClocks(inputs: Dict[str, str], clocks: Dict[str, str], clockPolarities: Dict[str, str], outputs: Dict[str, str]):
	"""
	>>> CompactClocks({'I': '000111XXX'}, {'C': '010010010'}, {'C': 'positive'}, {'O': 'XX000111X'})
	([0, 3, 6], {'I': '01X'}, {'C': 'PPP'}, {'O': 'X01'})
	"""
	numberTimeframes = max([len(values) for values in inputs.values()])
	if len(clocks) == 0:
		return list(range(numberTimeframes)), inputs, clocks, outputs

	indexResult: List[int] = []
	inputResult = dict.fromkeys(inputs.keys(), '')
	clockResult = dict.fromkeys(clocks.keys(), '')
	outputResult = dict.fromkeys(outputs.keys(), '')
	clockState = defaultdict(lambda: 'X')
	for timeframe in range(numberTimeframes):
		inputEdge = None
		outputEdge = None

		for clockName, clockValue in clocks.items():
			current = clockValue[timeframe]
			previous = clockState[clockName]
			clockState[clockName] = current
			if clockPolarities[clockName] == 'positive':
				# Data is latched at positive edge
				# Data is visible at negative edge
				clockInputEdge = (previous, current) == ('0', '1')
				clockOutputEdge = (previous, current) == ('1', '0')
			elif clockPolarities[clockName] == 'negative':
				# Data is latched at negative edge
				# Data is visible at positive edge
				clockInputEdge = (previous, current) == ('1', '0')
				clockOutputEdge = (previous, current) == ('0', '1')
			else:
				raise ValueError('Unknown clock polarity')

			if inputEdge is None:
				inputEdge = clockInputEdge
			elif inputEdge != clockInputEdge:
				raise ValueError("Clocks don't agree on input edge. This is unsupported")

			if outputEdge is None:
				outputEdge = clockOutputEdge
			elif outputEdge != clockOutputEdge:
				raise ValueError("Clocks don't agree on output edge. This is unsupported")

		if inputEdge:
			# Capture the signal right before the edge
			captureTimeframe = timeframe - 1
			indexResult.append(captureTimeframe)
			for clockName in clockResult.keys():
				clockResult[clockName] += 'P'
			for inputName, inputValue in inputs.items():
				inputResult[inputName] += inputValue[captureTimeframe]
			for outputName, outputValue in outputs.items():
				outputResult[outputName] += outputValue[captureTimeframe]

	for inputName, inputValue in inputResult.items():
		if len(indexResult) != len(inputValue):
			raise ValueError(f'Input {inputName} length mismatch to timeframes ({len(inputValue)} vs {len(indexResult)})')

	for clockName, clockValue in clockResult.items():
		if len(indexResult) != len(clockValue):
			raise ValueError(f'Clock {clockName} length mismatch to timeframes ({len(clockValue)} vs {len(indexResult)})')

	for outputName, outputValue in outputResult.items():
		if len(indexResult) != len(outputValue):
			raise ValueError(f'Output {outputName} length mismatch to timeframes ({len(outputValue)} vs {len(indexResult)})')

	return indexResult, inputResult, clockResult, outputResult
