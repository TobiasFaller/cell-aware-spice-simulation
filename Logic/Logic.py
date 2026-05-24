import re
from dataclasses import dataclass
from typing import Dict, Any, Iterable
from collections import defaultdict

@dataclass
class LogicValue:
	value: str

	def __invert__(self) -> 'LogicValue':
		return LogicValue({
			'0': '1', '1': '0', 'X': 'X',
		}.get(self.value))

	def __or__(self, other: 'LogicValue') -> 'LogicValue':
		return LogicValue({
			'00': '0', '10': '1', 'X0': 'X',
			'01': '1', '11': '1', 'X1': '1',
			'0X': 'X', '1X': '1', 'XX': 'X',
		}.get(self.value + str(other)))

	def __and__(self, other: 'LogicValue') -> 'LogicValue':
		return LogicValue({
			'00': '0', '10': '0', 'X0': '0',
			'01': '0', '11': '1', 'X1': 'X',
			'0X': '0', '1X': 'X', 'XX': 'X',
		}.get(self.value + str(other)))

	def __xor__(self, other: 'LogicValue') -> 'LogicValue':
		return LogicValue({
			'00': '0', '10': '1', 'X0': 'X',
			'01': '1', '11': '0', 'X1': 'X',
			'0X': 'X', '1X': 'X', 'XX': 'X',
		}.get(self.value + str(other)))

	def __bool__(self) -> bool:
		return self.value == '1'

	def __contains__(self, other: 'LogicValue') -> bool:
		""" Check if the other value is a subset of this logic value. """
		return True if self.value == 'X' else (self.value == other.value)

	def __str__(self) -> str:
		return self.value


def LogicIte(cond, then, other):
	if cond == LogicValue('1'):
		return then
	elif cond == LogicValue('0'):
		return other
	else:
		return LogicValue('X')


def _ToLogicValue(value: Any) -> LogicValue:
	if isinstance(value, LogicValue):
		return value
	elif isinstance(value, str):
		return LogicValue(value)
	elif isinstance(value, int):
		return LogicValue(str(value))
	else:
		raise ValueError('Invalid type')


class VariableProxy:
	functions = { 'ite': LogicIte, 'LogicValue': LogicValue }

	def __init__(self, timeframe: Dict[str, str]):
		self.variables = timeframe

	def __getitem__(self, key):
		if key in self.functions:
			return VariableProxy.functions[key]
		elif key in self.variables:
			return _ToLogicValue(self.variables[key])
		else:
			return LogicValue('X')


def NormalizeEquation(equation: str) -> str:
	# Normalize whitespace
	equation = re.sub(r"\s+", ' ', equation)
	# Replace inversions that use a tick (a' -> ~a) and ((a b)' -> (a b)^1)
	equation = re.sub(r"([A-Za-z]+)'", r'~\1', equation)
	equation = re.sub(r"'", r'^1', equation)
	# Replace and operations that are denoted by a space (a b -> a & b)
	equation = re.sub(r"([A-Za-z]+)\s+(?=[A-Za-z])", r'\1 & ', equation)
	# Normalize operators
	for original, replacement in [
		('!', '~'),
		('+', '|'),
		('*', '&'),
	]:
		equation = equation.replace(original, replacement)
	return equation


def EvaluateEquation(equation: str, inputs: Dict[str, str]) -> str:
	equation = equation.replace(r'\w0\w', 'LogicValue("0")')
	equation = equation.replace(r'\w1\w', 'LogicValue("1")')
	equation = equation.replace(r'"0"', 'LogicValue("0")')
	equation = equation.replace(r'"1"', 'LogicValue("1")')
	equation = equation.replace(r'"X"', 'LogicValue("X")')

	output = ''
	for timeframe in _InputsToTimeframes(inputs):
		# Globals can not be used for generating default values
		# as optimizations in CPython don't call the overwritten methods.
		# This is true even for the defaultdict class.
		output += str(eval(equation, {}, VariableProxy(timeframe)))
	return output


def EvaluateEquations(equations: Dict[str, str], inputs: Dict[str, str]) -> Dict[str, str]:
	state = {}
	outputs = defaultdict(lambda: '')

	for timeframe in _InputsToTimeframes(inputs):
		for output, equation in equations.items():
			value = EvaluateEquation(equation, { **state, **timeframe })
			outputs[output] += value

			# Update the state immediately such that outputs of latches
			# and flipflops are not delayed by a timeframe but immediately
			# can represent the internal state of the gate.
			state[output] = value

	return outputs


def _InputsToTimeframes(inputs: Dict[str, str]) -> Iterable[Dict[str, str]]:
	timeframes = max([0] + [len(value) for value in inputs.values()])
	for timeframe in range(timeframes):
		yield dict([(key, inputs[key][timeframe]) for key in inputs.keys()])
