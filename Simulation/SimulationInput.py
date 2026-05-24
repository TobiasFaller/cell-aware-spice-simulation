from collections import defaultdict
from typing import Dict, List
from sympy.combinatorics import GrayCode


def GetStaticBinaryPatterns(inputs) -> List[Dict[str, str]]:
	""" Creates binary counter inputs for the input ports.

	>>> GetStaticBinaryPatterns(['A'])
	[{'A': '0'}, {'A': '1'}]

	>>> GetStaticBinaryPatterns(['A', 'B'])
	[{'A': '0', 'B': '0'}, {'A': '0', 'B': '1'}, {'A': '1', 'B': '0'}, {'A': '1', 'B': '1'}]
	"""
	input_count = len(inputs)
	if input_count == 0:
		return [{}]

	patterns = []
	for input in range(0, 2**input_count):
		binary = f'{input:0{input_count}b}'
		patterns.append(dict(
			(port, binary[index]) for index, port in enumerate(inputs)
		))
	return patterns


def GetStaticGrayPatterns(inputs) -> List[Dict[str, str]]:
	""" Creates gray counter inputs for the input ports.

	>>> GetStaticGrayPatterns(['A'])
	[{'A': '0'}, {'A': '1'}]

	>>> GetStaticGrayPatterns(['A', 'B'])
	[{'A': '0', 'B': '0'}, {'A': '0', 'B': '1'}, {'A': '1', 'B': '1'}, {'A': '1', 'B': '0'}]
	"""
	input_count = len(inputs)
	if input_count == 0:
		return [{}]

	gray_code = GrayCode(input_count)
	gray_list = list(gray_code.generate_gray())

	patterns = []
	for input in range(0, 2**input_count):
		patterns.append(dict(
			(port, str(gray_list[input][index])) for index, port in enumerate(inputs)
		))
	return patterns


def GetDynamicTransitionPatterns(inputs) -> List[Dict[str, str]]:
	""" Creates transition inputs for the input ports.

	>>> GetDynamicTransitionPatterns(['A'])
	[{'A': '01'}, {'A': '10'}]

	>>> GetDynamicTransitionPatterns(['A', 'B'])  # doctest: +NORMALIZE_WHITESPACE
	[{'A': '01', 'B': '00'}, {'A': '00', 'B': '01'}, {'A': '01', 'B': '11'}, {'A': '11', 'B': '01'},
	 {'A': '10', 'B': '00'}, {'A': '00', 'B': '10'}, {'A': '10', 'B': '11'}, {'A': '11', 'B': '10'}]

	>>> GetDynamicTransitionPatterns(['A', 'B', 'C'])  # doctest: +NORMALIZE_WHITESPACE +ELLIPSIS
	[{'A': '01', 'B': '00', 'C': '00'},
	 {'A': '00', 'B': '01', 'C': '00'},
	 {'A': '00', 'B': '00', 'C': '01'},
	 {'A': '01', 'B': '00', 'C': '11'},
	 {'A': '11', 'B': '01', 'C': '00'},
	 {'A': '00', 'B': '11', 'C': '01'},
	 ...
	 {'A': '10', 'B': '11', 'C': '11'},
	 {'A': '11', 'B': '10', 'C': '11'},
	 {'A': '11', 'B': '11', 'C': '10'}]
	"""
	input_count = len(inputs)
	if input_count == 0:
		return [{}]

	active_transition = ["01", "10"]
	sequence_result = []
	for transition in active_transition:
		sequence = ["" for _ in range(input_count)]
		for index in range(len(transition)):
			sequence[0] += transition[index]

		for input in range(0, 2**(input_count - 1)):
			new_sequence = sequence[:]
			binary = f'{input:0{input_count-1}b}'
			for input in range(1, input_count):
				for _ in range(len(transition)):
					new_sequence[input] += binary[input - 1]

			sequence_result.append(new_sequence)
			for i in range(0, input_count - 1):
				# "Rotate" list to get this input combination once for every input; e.g. A1=01, A2=11 -> A1=11, A2=01
				sequence_result.append(new_sequence[-(i+1):] + new_sequence[:-(i+1)])

	patterns = []
	for combination in sequence_result:
		condition = defaultdict(str)
		for index, port in enumerate(inputs):
			for value in combination[index]:
				condition[port] += str(value)
		patterns.append(dict(condition))
	return patterns

