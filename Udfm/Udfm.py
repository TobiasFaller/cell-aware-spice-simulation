from typing import Dict, List, Any
from dataclasses import dataclass, field


@dataclass
class UdfmTestAlternative(object):
	testType: str
	testConditions: Dict[str, str]
	testEffects: Dict[str, str]

	def __repr__(self) -> str:
		return f'        type: {self.testType} conditions: {self.testConditions}, effects: {self.testEffects}'

@dataclass
class UdfmFault(object):
	faultName: str
	faultCategory: str
	alternatives: List[UdfmTestAlternative] = field(default_factory=list)

	def AddPattern(self, testType: str, testConditions: Dict[str, str], testEffects: Dict[str, str]) -> UdfmTestAlternative:
		alternative = UdfmTestAlternative(testType, testConditions, testEffects)
		self.alternatives.append(alternative)
		return alternative

	def Sort(self):
		self.alternatives.sort(key=lambda item: list(item.testConditions.values()))

	def __repr__(self) -> str:
		return f'    {self.faultName} ({self.faultCategory})' \
			+ '\n'.join([repr(alternative) for alternative in self.alternatives])

@dataclass
class UdfmCell(object):
	cellName: str
	faults: Dict[str, UdfmFault] = field(default_factory=dict)

	def AddPattern(self, faultName: str, faultCategory: str, testType: str, testConditions: Dict[str, str], testEffects: Dict[str, str]) -> UdfmTestAlternative:
		self.AddFault(faultName, faultCategory)
		return self.faults[faultName].AddPattern(testType, testConditions, testEffects)

	def AddFault(self, faultName: str, faultCategory: str) -> UdfmFault:
		if not faultName in self.faults:
			self.faults[faultName] = UdfmFault(faultName, faultCategory)
		return self.faults[faultName]

	def Sort(self):
		self.faults = dict(sorted(self.faults.items(), key=lambda item: item[1].faultName))
		for fault in self.faults.values():
			fault.Sort()

	def __repr__(self) -> str:
		return self.cellName + '\n'.join([repr(fault) for fault in self.faults])

@dataclass
class Udfm(object):
	cells: Dict[str, UdfmCell] = field(default_factory=dict)

	def AddPattern(self, cellName: str, faultName: str, faultCategory: str, testType: str, testConditions: Dict[str, str], testEffects: Dict[str, str]) -> UdfmTestAlternative:
		self.AddCell(cellName)
		return self.cells[cellName].AddPattern(faultName, faultCategory, testType, testConditions, testEffects)

	def AddFault(self, cellName: str, faultName: str, faultCategory: str, alternatives: List[UdfmTestAlternative]) -> UdfmFault:
		self.AddCell(cellName)
		for alternative in alternatives:
			self.cells[cellName].AddPattern(faultName, faultCategory, alternative.testType, alternative.testConditions, alternative.testEffects)
		return self.cells[cellName]

	def AddCell(self, cellName: str) -> UdfmCell:
		if cellName not in self.cells:
			self.cells[cellName] = UdfmCell(cellName)
		return self.cells[cellName]

	def Sort(self):
		self.cells = dict(sorted(self.cells.items(), key=lambda item: item[0]))
		for cell in self.cells.values():
			cell.Sort()

	def __repr__(self) -> str:
		return '\n\n'.join([repr(cell) for cell in self.cells])
