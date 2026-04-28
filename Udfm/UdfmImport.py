from lark import Lark, Token, Tree, v_args
from lark.visitors import Transformer_InPlaceRecursive, _VArgsWrapper
from pathlib import Path
from typing import List, Dict, Tuple
from logging import debug
from io import TextIOBase
import gc

from Udfm.Udfm import Udfm, UdfmCell, UdfmFault, UdfmTestAlternative

UDFM_GRAMMAR = r"""
	?start: tessent_udfm | simple_udfm

	simple_udfm: simple_udfm_cell*
	simple_udfm_cell: "Cell" simple_udfm_cell_name "{" simple_udfm_fault* "}"
	simple_udfm_cell_name: quoted_string
	simple_udfm_fault: "Fault" simple_udfm_fault_name "{" simple_udfm_alternative* "}"
	simple_udfm_fault_name: quoted_string
	simple_udfm_alternative: "test" "{" simple_udfm_test_type simple_udfm_effects ";" "Condition" simple_udfm_conditions ";" "}"
	simple_udfm_test_type: STRING
	simple_udfm_effects: (","? simple_udfm_port_value)+
	simple_udfm_conditions: (","? simple_udfm_port_value)+
	simple_udfm_port_value: quoted_string "=" LOGIC_VALUE

	tessent_udfm: tessent_udfm_metadata tessent_udfm_category*
	tessent_udfm_metadata: "UDFM" "{" tessent_udfm_version ";" tessent_udfm_properties "}"
	tessent_udfm_version: "version" ":" STRING
	tessent_udfm_properties: "Properties" "{" (";"? tessent_udfm_property)* "}"
	tessent_udfm_property: quoted_string ":" quoted_string
	tessent_udfm_category: "UdfmType" "(" tessent_udfm_category_name ")" "{" tessent_udfm_cell* "}"
	tessent_udfm_category_name: quoted_string
	tessent_udfm_cell: "Cell" "(" tessent_udfm_cell_name ")" "{" tessent_udfm_fault* "}"
	tessent_udfm_cell_name: quoted_string
	tessent_udfm_fault: "Fault" "(" tessent_udfm_fault_name ")" "{" tessent_udfm_alternative* "}"
	tessent_udfm_fault_name: quoted_string
	tessent_udfm_alternative: "Test" "{" tessent_udfm_test_type tessent_udfm_effects "Conditions" tessent_udfm_conditions "}"
	tessent_udfm_test_type: STRING
	tessent_udfm_effects: "{" tessent_udfm_port_value+ "}"
	tessent_udfm_conditions: "{" tessent_udfm_port_value+ "}"
	tessent_udfm_port_value: quoted_string ":" LOGIC_VALUE ";"

	quoted_string: QUOTED_STRING

	STRING: /\w+/
	QUOTED_STRING: /"[^"]*"/
	LOGIC_VALUE: /[01UX]+/

	%import common.WS
	%ignore WS
"""

@v_args(inline=True)
class UdfmTransformer(Transformer_InPlaceRecursive):

	def __init__(self, cell_name_filter=None, fault_name_filter=None, fault_type_filter=None, visit_tokens: bool = True) -> None:
		super().__init__(visit_tokens)

		self.cell_name_filter = (lambda cell_name: True) if cell_name_filter is None else cell_name_filter
		self.fault_type_filter = (lambda fault_type: True) if fault_type_filter is None else fault_type_filter
		self.fault_name_filter = (lambda fault_name: True) if fault_name_filter is None else fault_name_filter

		# Flags that disable token -> object transformation when not required.
		self.ignore_category = False
		self.ignore_cell = False
		self.ignore_fault = False
		self.fault_category = None

	def simple_udfm(self, *cells: UdfmCell) -> Udfm:
		return Udfm(dict([(cell.cellName, cell) for cell in cells if cell is not None]))

	def simple_udfm_cell(self, cellName: str, *faults: UdfmFault) -> UdfmCell:
		gc.collect()
		if self.ignore_cell:
			return None
		faults = dict([(fault.faultName, fault) for fault in faults if fault is not None])
		debug(f'Parsed cell {cellName} ({len(faults)} faults) / {sum([len(fault.alternatives) for fault in faults.values()])} alternatives)')
		return UdfmCell(cellName, faults)

	def simple_udfm_cell_name(self, cellName: str) -> str:
		self.ignore_cell = self.ignore_category or not self.cell_name_filter(cellName)
		self.ignore_fault = self.ignore_cell
		return cellName

	def simple_udfm_fault(self, faultName: str, *alternatives: UdfmTestAlternative) -> UdfmFault:
		if self.ignore_fault:
			return None
		return UdfmFault(faultName, None, alternatives)

	def simple_udfm_fault_name(self, faultName: str) -> str:
		self.ignore_fault = self.ignore_cell or not self.fault_name_filter(faultName)
		return faultName

	def simple_udfm_test_type(self, testType: str) -> str:
		if self.ignore_fault:
			return None
		return {
			'StaticTest': 'static',
			'TransitionTest': 'transition',
			'DynamicTest': 'dynamic'
		}.get(testType, None)

	def simple_udfm_alternative(self, testType: str, effects: Dict[str, str], conditions: Dict[str, str]) -> UdfmTestAlternative:
		if self.ignore_fault:
			return None
		return UdfmTestAlternative(testType, conditions, effects)

	def simple_udfm_effects(self, *portValues: Tuple[str, str]) -> Dict[str, str]:
		if self.ignore_fault:
			return None
		return dict(portValues)

	def simple_udfm_conditions(self, *portValues: Tuple[str, str]) -> Dict[str, str]:
		if self.ignore_fault:
			return None
		return dict(portValues)

	def simple_udfm_port_value(self, port: Token, value: Token) -> Tuple[str, str]:
		if self.ignore_fault:
			return None
		return (str(port), str(value))

	def tessent_udfm(self, metaData: Dict[str, str], *faultCategories: Tuple[str, List[UdfmCell]]) -> Udfm:
		cells: Dict[str, UdfmCell] = {}
		for categoryName, categoryCells in faultCategories:
			for cell in categoryCells:
				if cell is None:
					continue
				if cell.cellName not in cells:
					cells[cell.cellName] = UdfmCell(cell.cellName)
				cells[cell.cellName].faults.update(cell.faults)
		return Udfm(cells)

	def tessent_udfm_metadata(self, version: str, properties: Dict[str, str]) -> Dict[str, str]:
		return {
			**properties,
			'version': version
		}

	def tessent_udfm_version(self, version: str) -> str:
		return version

	def tessent_udfm_properties(self, *properties: Tuple[str, str]) -> Dict[str, str]:
		return dict(properties)

	def tessent_udfm_property(self, name: str, value: str) -> Tuple[str, str]:
		return (name, value)

	def tessent_udfm_category(self, name: str, *faults: UdfmFault) -> Tuple[str, List[UdfmFault]]:
		return (name, faults)

	def tessent_udfm_category_name(self, name: str) -> str:
		self.ignore_category = not self.fault_type_filter(name)
		self.ignore_cell = self.ignore_category
		self.ignore_fault = self.ignore_cell
		self.fault_category = name
		return name

	def tessent_udfm_cell(self, cellName: str, *faults: UdfmFault) -> UdfmCell:
		gc.collect()
		if self.ignore_cell:
			return None
		faults = dict([(fault.faultName, fault) for fault in faults if fault is not None])
		debug(f'Parsed cell {cellName} ({len(faults)} faults) / {sum([len(fault.alternatives) for fault in faults.values()])} alternatives)')
		return UdfmCell(cellName, faults)

	def tessent_udfm_cell_name(self, cellName: str) -> str:
		self.ignore_cell = self.ignore_category or not self.cell_name_filter(cellName)
		self.ignore_fault = self.ignore_cell
		return cellName

	def tessent_udfm_fault(self, faultName: str, *alternatives: UdfmTestAlternative) -> UdfmFault:
		if self.ignore_fault:
			return None
		return UdfmFault(faultName, self.fault_category, alternatives)

	def tessent_udfm_fault_name(self, faultName: str) -> str:
		self.ignore_fault = not self.fault_name_filter(faultName)
		return faultName

	def tessent_udfm_alternative(self, testType: str, effects: Dict[str, str], conditions: Dict[str, str]) -> UdfmTestAlternative:
		if self.ignore_fault:
			return None
		return UdfmTestAlternative(testType, conditions, effects)

	def tessent_udfm_test_type(self, testType: str) -> str:
		if self.ignore_fault:
			return None
		return {
			'StaticTest': 'static',
			'TransitionTest': 'transition',
			'DynamicTest': 'dynamic'
		}.get(testType, None)

	def tessent_udfm_effects(self, *portValues: Tuple[str, str]) -> Dict[str, str]:
		if self.ignore_fault:
			return None
		return dict(portValues)

	def tessent_udfm_conditions(self, *portValues: Tuple[str, str]) -> Dict[str, str]:
		if self.ignore_fault:
			return None
		return dict(portValues)

	def tessent_udfm_port_value(self, port: Token, value: Token) -> Tuple[str, str]:
		if self.ignore_fault:
			return None
		return (str(port), str(value))

	def quoted_string(self, value: str) -> str:
		return value[1:-1]

def ImportUdfm(stream: TextIOBase, cell_name_filter=None, fault_type_filter=None, fault_name_filter=None) -> Udfm:
	parser = Lark(UDFM_GRAMMAR,
		parser='lalr',
		lexer='contextual',
		transformer=UdfmTransformer(
			cell_name_filter=cell_name_filter,
			fault_type_filter=fault_type_filter,
			fault_name_filter=fault_name_filter,
			visit_tokens=False
		)
	)
	return parser.parse(stream.read())
