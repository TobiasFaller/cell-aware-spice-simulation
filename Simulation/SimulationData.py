from typing import Dict, List, Any
from collections import defaultdict
from dataclasses import field
from marshmallow import Schema, fields
from marshmallow_dataclass import dataclass

from Simulation.Serialization import SingleFieldSchema, ObjectSchema


class SimulationRunSchema(ObjectSchema):
	clazz = 'SimulationRun'
	jobId = fields.Int(data_key='id')
	inputType = fields.Str(data_key='type')
	inputs = fields.Dict(key=fields.Str(), values=fields.Str())
	clocks = fields.Dict(key=fields.Str(), values=fields.Str())
	outputs = fields.Dict(key=fields.Str(), values=fields.Str())

@dataclass
class SimulationRun:
	jobId: int
	inputType: str
	inputs: Dict[str, str]
	clocks: Dict[str, str]
	outputs: Dict[str, str]

	def __str__(self) -> str:
		inputsText = ' '.join([f'{port}={value}' for port, value in self.inputs.items()])
		clocksText = ' '.join([f'{port}={value}' for port, value in self.clocks.items()])
		outputsText = ' '.join([f'{port}={value}' for port, value in self.outputs.items()])
		inputsText = '-' if inputsText == '' else inputsText
		clocksText = '-' if clocksText == '' else clocksText
		outputsText = '-' if outputsText == '' else outputsText
		return f'job: {self.jobId}, {self.inputType} inputs: {inputsText}, clocks: {clocksText}, outputs: {outputsText}'

	def __repr__(self) -> str:
		return str(self)


class SimulationFaultSchema(SingleFieldSchema):
	clazz = 'SimulationFault'
	runs = fields.List(fields.Nested(SimulationRunSchema))

@dataclass
class SimulationFault:
	runs: List[SimulationRun] = field(default_factory=list)

	def __str__(self) -> str:
		return ''.join([f'\n        {run}' for run in self.runs])

	def __repr__(self) -> str:
		return f'SimulationFault ({len(self.runs)} runs)'


class SimulationCellSchema(SingleFieldSchema):
	clazz = 'SimulationCell'
	faults = fields.Dict(keys=fields.Str(), values=fields.Nested(SimulationFaultSchema))

@dataclass
class SimulationCell:
	faults: Dict[str, SimulationFault] = field(default_factory=lambda: defaultdict(SimulationFault))

	def __str__(self) -> str:
		return ''.join([f'\n    {name}{fault}' for name, fault in self.faults.items()])

	def __repr__(self) -> str:
		return f'SimulationCell ({len(self.faults)} faults)'


class SimulationDataSchema(SingleFieldSchema):
	clazz = 'SimulationData'
	cells = fields.Dict(keys=fields.Str(), values=fields.Nested(SimulationCellSchema))

@dataclass
class SimulationData:
	cells: Dict[str, SimulationCell] = field(default_factory=lambda: defaultdict(SimulationCell))

	def __str__(self) -> str:
		return ''.join([f'\n{name}{cell}' for name, cell in self.cells.items()])

	def __repr__(self) -> str:
		return f'SimulationData ({len(self.cells)} cells)'

	def ToDict(self) -> Dict[str, Any]:
		return SimulationDataSchema().dump(self)

	def FromDict(data: Dict[str, Any]) -> 'SimulationCategory':
		return SimulationDataSchema().load(data)


class SimulationCategorySchema(SingleFieldSchema):
	clazz = 'SimulationCategory'
	categories = fields.Dict(keys=fields.Str(), values=fields.Nested(SimulationDataSchema))

@dataclass
class SimulationCategory:
	categories: Dict[str, SimulationData] = field(default_factory=lambda: defaultdict(SimulationData))

	def AddRun(self, jobId, category, cellName, faultName, inputType, inputs, clocks, outputs):
		self.categories[category].cells[cellName].faults[faultName].runs.append(
			SimulationRun(jobId, inputType, inputs, clocks, outputs))

	def GetCell(self, categoryName: str, cellName: str) -> SimulationCell:
		return self.categories[categoryName].cells[cellName]

	def GetFault(self, categoryName: str, cellName: str, faultName: str) -> SimulationFault:
		return self.categories[categoryName].cells[cellName].faults[faultName]

	def SetCell(self, categoryName: str, cellName: str, cell: SimulationCell) -> SimulationCell:
		self.categories[categoryName].cells[cellName] = cell
		return cell

	def SetFault(self, categoryName: str, cellName: str, faultName: str, fault: SimulationFault) -> SimulationFault:
		self.categories[categoryName].cells[cellName].faults[faultName] = fault
		return fault

	def __str__(self) -> str:
		return '\n\n'.join([f'{name}{simulation}' for name, simulation in self.categories.items()])

	def __repr__(self) -> str:
		return f'SimulationCategory ({len(self.categories)} categories)'

	def ToDict(self) -> Dict[str, Any]:
		return SimulationCategorySchema().dump(self)

	def FromDict(data: Dict[str, Any]) -> 'SimulationCategory':
		return SimulationCategorySchema().load(data)
