from logging import warning
from dataclasses import dataclass
from typing import Dict, List
from collections import defaultdict

from Simulation.SimulationData import SimulationData
from Logic.Logic import EvaluateEquations
from Spice.SpiceModelInfo import SpiceModelInfo


@dataclass
class CheckViolation:
	inputs: Dict[str, str]
	clocks: Dict[str, str]
	outputs: Dict[str, str]
	expected: Dict[str, str]
	jobId: int

@dataclass
class CheckResult:
	validated: bool
	violations: List[CheckViolation]


def CheckFaultFreeSimulation(simulation: SimulationData, cells: Dict[str, SpiceModelInfo]) -> Dict[str, CheckResult]:
	results: Dict[str, CheckResult] = defaultdict(lambda: CheckResult(validated=False, violations=[]))

	for cellName, simulationCell in simulation.cells.items():
		if len(cells[cellName].equations) == 0:
			warning(f'Skipping validating cell {cellName} as no equations have been specified')
			continue

		results[cellName].validated = True
		for output in cells[cellName].outputs:
			if output not in cells[cellName].equations:
				warning(f'There is not equation for output {output} of {cellName}')
				results[cellName].validated = False

		for fault in simulationCell.faults.values():
			for run in fault.runs:
				equationResults = EvaluateEquations(cells[cellName].equations, run.inputs)
				for outputName, expectedOutput in run.outputs.items():
					if outputName in equationResults and not all([_ValueMatch(expected, value) \
							for value, expected in zip(expectedOutput, equationResults[outputName])]):
						break
				else:
					continue

				expectedResults = { key: equationResults[key] for key in run.outputs.keys() }
				results[cellName].violations += [
					CheckViolation(
						inputs=run.inputs,
						clocks=run.clocks,
						outputs=run.outputs,
						expected=expectedResults,
						jobId=run.jobId
					)
				]

	return results

def _ValueMatch(expected: str, value: str):
	return expected == 'X' or value == expected
