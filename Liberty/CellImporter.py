from liberty.parser import parse_liberty, EscapedString
from typing import Dict, List, Tuple
from pathlib import Path
from logging import warning
from collections.abc import Callable

from Logic.Logic import EvaluateEquation, NormalizeEquation
from Spice.SpiceModelInfo import SpiceModelInfo
from Simulation.SimulationInput import GetStaticBinaryPatterns

def ImportLibertyCells(path: Path, cellFilter: Callable[[str], bool] = None) -> Dict[str, SpiceModelInfo]:
	def _GetValue(name):
		return name.value if isinstance(name, EscapedString) else name

	with open(path, 'rt', encoding='utf-8') as file:
		library = parse_liberty(file.read())

	cells = {}
	for cell in library.get_groups('cell'):
		cellName = _GetValue(cell.args[0])
		if cellFilter is not None and not cellFilter(cellName):
			continue

		if len(cell.get_groups('statetable')) > 0:
			warning(f'Cell {cellName} uses state tables and will be ignored. State tables are currently not supported.')
			continue

		cellAttributes = {
			'ff_state_internal': [],
			'ff_state_positive': [],
			'ff_state_negative': [],
			'ff_data': [],
			'ff_clock': [],
			'ff_set': [],
			'ff_reset': [],
			'latch_state_positive': [],
			'latch_state_negative': [],
			'latch_data': [],
			'latch_enable': [],
			'latch_set': [],
			'latch_reset': [],
		}

		inputPins = []
		outputPins = []
		clockPins = []
		clockPolarities = []
		powerPins = []
		groundPins = []
		for pin in cell.get_groups('pg_pin'):
			pinName = _GetValue(pin.args[0])
			pinType = _GetValue(pin['pg_type'])
			if pinType == 'primary_power':
				powerPins.append(pinName)
			elif pinType == 'primary_ground':
				groundPins.append(pinName)

		originalVarMap: List[Tuple[str, str]] = []
		states: List[str] = []
		equations: Dict[str, str] = {}
		for index, flipflop in enumerate(cell.get_groups('ff')):
			posState = _GetValue(flipflop.args[0]) if len(flipflop.args) >= 1 else None
			negState = _GetValue(flipflop.args[1]) if len(flipflop.args) >= 2 else None
			if posState is None:
				raise ValueError(f'Positive state for flipflop in cell {cellName} is required')
			if negState is None:
				raise ValueError(f'Negative state for flipflop in cell {cellName} is required')

			cellAttributes["flipflop"] = True
			cellAttributes["sequential"] = True

			varIntState = f'__ff{index}_i'
			varPosState = f'__ff{index}_p'
			varNegState = f'__ff{index}_n'
			states += [varIntState, varPosState, varNegState]
			originalVarMap.append((posState, f'{varPosState}'))
			originalVarMap.append((negState, f'{varNegState}'))

			nextState = _GetValue(flipflop['next_state']) if 'next_state' in flipflop else None
			clockState = _GetValue(flipflop['clocked_on']) if 'clocked_on' in flipflop else None
			setState = _GetValue(flipflop['preset']) if 'preset' in flipflop else '0'
			resetState = _GetValue(flipflop['clear']) if 'clear' in flipflop else '0'
			setReset1State = _GetValue(flipflop['clear_preset_var1']) if 'clear_preset_var1' in flipflop else 'X'
			setReset2State = _GetValue(flipflop['clear_preset_var2']) if 'clear_preset_var2' in flipflop else 'X'
			if nextState is None:
				raise ValueError(f'Next state for flipflop {posState} / {negState} in cell {cellName} is required')
			if clockState is None:
				raise ValueError(f'Clock for flipflop {posState} / {negState} in cell {cellName} is required')

			cellAttributes["ff_state_internal"].append(varIntState)
			cellAttributes["ff_state_positive"].append(varPosState)
			cellAttributes["ff_state_negative"].append(varNegState)
			cellAttributes["ff_data"].append(nextState)
			cellAttributes["ff_clock"].append(clockState)
			cellAttributes["ff_set"].append(setState)
			cellAttributes["ff_reset"].append(resetState)

			setReset1State = { 'L': '0', 'H': '1', 'N': varPosState, 'T': f'~{varPosState}', 'X': '"X"' }.get(setReset1State)
			setReset2State = { 'L': '0', 'H': '1', 'N': varNegState, 'T': f'~{varNegState}', 'X': '"X"' }.get(setReset2State)

			partialState1 = f'ite({clockState}, {nextState}, {varIntState})'
			partialState2 = f'ite(~{clockState}, {varIntState}, {varPosState})'
			if setState != '0':
				partialState1 = f'({partialState1} | ({setState}))'
				partialState2 = f'({partialState2} | ({setState}))'
			if resetState != '0':
				partialState1 = f'({partialState1} & ~({resetState}))'
				partialState2 = f'({partialState2} & ~({resetState}))'

			if setState != '0' and resetState != '0':
				equations[varIntState] = f'ite(({setState}) & ({resetState}), "X", ({partialState1}))'
				equations[varPosState] = f'ite(({setState}) & ({resetState}), {setReset1State}, ({partialState2}))'
				equations[varNegState] = f'ite(({setState}) & ({resetState}), {setReset2State}, ~({partialState2}))'
			else:
				equations[varIntState] = f'{partialState1}'
				equations[varPosState] = f'{partialState2}'
				equations[varNegState] = f'~({partialState2})'

		for index, latch in enumerate(cell.get_groups('latch')):
			posState = _GetValue(latch.args[0]) if len(latch.args) >= 1 else None
			negState = _GetValue(latch.args[1]) if len(latch.args) >= 2 else None
			if posState is None:
				raise ValueError(f'Positive state for latch in cell {cellName} is required')
			if negState is None:
				raise ValueError(f'Negative state for latch in cell {cellName} is required')

			cellAttributes["latch"] = True
			cellAttributes["sequential"] = True

			varPosState = f'__dl{index}_p'
			varNegState = f'__dl{index}_n'
			states += [varPosState, varNegState]
			originalVarMap.append((posState, f'{varPosState}'))
			originalVarMap.append((negState, f'{varNegState}'))

			nextState = _GetValue(latch['data_in']) if 'data_in' in latch else None
			enableState = _GetValue(latch['enable']) if 'enable' in latch else '1'
			setState = _GetValue(latch['preset']) if 'preset' in latch else '0'
			resetState = _GetValue(latch['clear']) if 'clear' in latch else '0'
			setReset1State = _GetValue(latch['clear_preset_var1']) if 'clear_preset_var1' in latch else 'X'
			setReset2State = _GetValue(latch['clear_preset_var2']) if 'clear_preset_var2' in latch else 'X'
			if nextState is None:
				raise ValueError(f'Next state for latch {posState} / {negState} in cell {cellName} is required')

			cellAttributes["latch_state_positive"].append(varPosState)
			cellAttributes["latch_state_negative"].append(varNegState)
			cellAttributes["latch_data"].append(nextState)
			cellAttributes["latch_enable"].append(enableState)
			cellAttributes["latch_set"].append(setState)
			cellAttributes["latch_reset"].append(resetState)

			setReset1State = { 'L': '0', 'H': '1', 'N': varPosState, 'T': f'~{varPosState}', 'X': '"X"' }.get(setReset1State)
			setReset2State = { 'L': '0', 'H': '1', 'N': varNegState, 'T': f'~{varNegState}', 'X': '"X"' }.get(setReset2State)

			partialState = f'ite({enableState}, {nextState}, {varPosState})'
			if setState != '0':
				partialState = f'({partialState} | ({setState}))'
			if resetState != '0':
				partialState = f'({partialState} & ~({resetState}))'

			if setState != '0' and resetState != '0':
				equations[varPosState] = f'ite(({setState}) & ({resetState}), {setReset1State}, ({partialState}))'
				equations[varNegState] = f'ite(({setState}) & ({resetState}), {setReset2State}, ~({partialState}))'
			else:
				equations[varPosState] = f'{partialState}'
				equations[varNegState] = f'~({partialState})'

		for pin in cell.get_groups('pin'):
			pinName = _GetValue(pin.args[0])
			if pinName in inputPins + outputPins + clockPins + powerPins + groundPins:
				continue

			pinDirection = _GetValue(pin['direction']) if 'direction' in pin else None
			pinClock = _GetValue(pin['clock']) if 'clock' in pin else False
			if pinClock:
				if pinName not in clockPins:
					# Clock polarities are computed later
					clockPins.append(pinName)
					clockPolarities.append(None)
			elif pinDirection == 'input':
				inputPins.append(pinName)
			elif pinDirection == 'output':
				outputPins.append(pinName)
				if 'function' in pin:
					equations[pinName] = _GetValue(pin['function'])
				elif 'state_function' in pin:
					equations[pinName] = _GetValue(pin['state_function'])
				else:
					raise ValueError(f'There is no function for output {pinName} in cell {cellName}')
				if 'three_state' in pin:
					equations[pinName] = f'ite({_GetValue(pin["three_state"])}, "X", {equations[pinName]})'
			elif pinDirection == 'internal':
				continue

		# Use reversed order to first match longer names, then shorter ones,
		# e.g., IQN before IQ such that the state is assigned correctly.
		originalVarMap.sort()
		def _normalize_equation(equation):
			equation = NormalizeEquation(equation)
			for original, state in reversed(originalVarMap):
				equation = equation.replace(original, state)
			return equation

		for pin, equation in equations.items():
			equations[pin] = _normalize_equation(equation)

		cellAttributes['ff_state_internal'] = [ _normalize_equation(equation) for equation in cellAttributes['ff_state_internal'] ]
		cellAttributes['ff_state_positive'] = [ _normalize_equation(equation) for equation in cellAttributes['ff_state_positive'] ]
		cellAttributes['ff_state_negative'] = [ _normalize_equation(equation) for equation in cellAttributes['ff_state_negative'] ]
		cellAttributes['ff_data'] = [ _normalize_equation(equation) for equation in cellAttributes['ff_data'] ]
		cellAttributes['ff_clock'] = [ _normalize_equation(equation) for equation in cellAttributes['ff_clock'] ]
		cellAttributes['ff_set'] = [ _normalize_equation(equation) for equation in cellAttributes['ff_set'] ]
		cellAttributes['ff_reset'] = [ _normalize_equation(equation) for equation in cellAttributes['ff_reset'] ]
		cellAttributes['latch_state_positive'] = [ _normalize_equation(equation) for equation in cellAttributes['latch_state_positive'] ]
		cellAttributes['latch_state_negative'] = [ _normalize_equation(equation) for equation in cellAttributes['latch_state_negative'] ]
		cellAttributes['latch_data'] = [ _normalize_equation(equation) for equation in cellAttributes['latch_data'] ]
		cellAttributes['latch_enable'] = [ _normalize_equation(equation) for equation in cellAttributes['latch_enable'] ]
		cellAttributes['latch_set'] = [ _normalize_equation(equation) for equation in cellAttributes['latch_set'] ]
		cellAttributes['latch_reset'] = [ _normalize_equation(equation) for equation in cellAttributes['latch_reset'] ]

		if "flipflop" in cellAttributes:
			if len(clockPins) != 1:
				raise ValueError('Clock pin for flip-flops not found')

		if not "sequential" in cellAttributes:
			cellAttributes["combinational"] = True

		for index, clockPin in enumerate(clockPins):
			clockPolarity = None
			for clockEquation in cellAttributes['ff_clock']:
				possibleDependencies = [
					*[pin for pin in inputPins if pin != clockPin],
					*cellAttributes['ff_state_internal'],
					*cellAttributes['ff_state_positive'],
					*cellAttributes['ff_state_negative'],
					*cellAttributes['latch_state_positive'],
					*cellAttributes['latch_state_negative'],
				]
				dependencies = []
				for pin in possibleDependencies:
					if pin in clockEquation:
						dependencies.append(pin)

				for pattern in GetStaticBinaryPatterns(dependencies):
					negativeValue = EvaluateEquation(clockEquation, { **pattern, clockPin: '0' })
					positiveValue = EvaluateEquation(clockEquation, { **pattern, clockPin: '1' })
					polarity = {
						('0', '0'): None,
						('0', '1'): 'positive',
						('1', '1'): None,
						('1', '0'): 'negative'
					}.get((negativeValue, positiveValue))

					if clockPolarity is None:
						clockPolarity = polarity
					elif polarity is None or polarity == clockPolarity:
						continue
					else:
						raise ValueError(f'Flip-flop clock "{clockEquation}" has changing polarity regarding clock {clockPin}.' \
							' Cells with positive and negative dependencies on the same clock are not supported.')

			for enableEquation in cellAttributes['latch_enable']:
				possibleDependencies = [
					*[pin for pin in inputPins if pin != clockPin],
					*cellAttributes['ff_state_internal'],
					*cellAttributes['ff_state_positive'],
					*cellAttributes['ff_state_negative'],
					*cellAttributes['latch_state_positive'],
					*cellAttributes['latch_state_negative'],
				]
				dependencies = []
				for pin in possibleDependencies:
					if pin in enableEquation:
						dependencies.append(pin)

				for pattern in GetStaticBinaryPatterns(dependencies):
					negativeValue = EvaluateEquation(enableEquation, { **pattern, clockPin: '0' })
					positiveValue = EvaluateEquation(enableEquation, { **pattern, clockPin: '1' })
					polarity = {
						('0', '0'): None,
						('0', '1'): 'positive',
						('1', '1'): None,
						('1', '0'): 'negative'
					}.get((negativeValue, positiveValue))

					if clockPolarity is None:
						clockPolarity = polarity
					elif polarity is None or polarity == clockPolarity:
						continue
					else:
						raise ValueError(f'Latch enable "{enableEquation}" has changing polarity regarding clock {clockPin}.' \
							' Cells with positive and negative dependencies on the same clock are not supported.')

			clockPolarities[index] = clockPolarity
			if clockPolarity is None:
				warning(f'Clock "{clockPin}" of cell {cellName} is not connected to any flip-flop')

		cells[cellName] = SpiceModelInfo(
			name=cellName,
			attributes=cellAttributes,
			inputs=inputPins,
			outputs=outputPins,
			clocks=clockPins,
			clockPolarities=clockPolarities,
			grounds=groundPins,
			supplies=powerPins,
			internal=[],
			equations=equations,
			states=states,
			cell=None,
		)

	return cells
