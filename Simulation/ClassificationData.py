from enum import StrEnum
from typing import Union, Dict, Any
from dataclasses import field
from marshmallow import fields, Schema
from marshmallow_dataclass import dataclass
from collections import defaultdict

from Simulation.Serialization import SingleFieldSchema, ObjectSchema


class ClassificationStatus(StrEnum):
	Detected = 'detected'
	Undetected = 'undetected'
	Undefined = 'undefined'
	EquivalentTo = 'equivalent to'


class ClassificationFaultSchema(ObjectSchema):
	clazz = 'ClassificationFault'
	status = fields.Enum(enum=ClassificationStatus)
	equivalent = fields.String(allow_none=True)

@dataclass
class ClassificationFault:
	status: ClassificationStatus
	equivalent: Union[None, int] = None


class ClassificationCellSchema(SingleFieldSchema):
	clazz = 'ClassificationCell'
	faults = fields.Dict(keys=fields.Str(), values=fields.Nested(ClassificationFaultSchema))

@dataclass
class ClassificationCell:
	faults: Dict[str, ClassificationFault] = field(default_factory=lambda: defaultdict(ClassificationFault))


class ClassificationDataSchema(SingleFieldSchema):
	clazz = 'ClassificationData'
	cells = fields.Dict(keys=fields.Str(), values=fields.Nested(ClassificationCellSchema))

@dataclass
class ClassificationData:
	cells: Dict[str, ClassificationCell] = field(default_factory=lambda: defaultdict(ClassificationCell))

	def ToDict(self) -> Dict[str, Any]:
		return ClassificationDataSchema().dump(self)

	def FromDict(data: Dict[str, Any]) -> 'ClassificationData':
		return ClassificationDataSchema().load(data)


class ClassificationCategorySchema(SingleFieldSchema):
	clazz = 'ClassificationCategory'
	categories = fields.Dict(keys=fields.Str(), values=fields.Nested(ClassificationDataSchema))

@dataclass
class ClassificationCategory:
	categories: Dict[str, ClassificationData] = field(default_factory=lambda: defaultdict(ClassificationData))

	def SetFault(self, categoryName, cellName, faultName, status: ClassificationStatus, equivalent: str = None):
		self.categories[categoryName].cells[cellName].faults[faultName] = ClassificationFault(status, equivalent)

	def ToDict(self) -> Dict[str, Any]:
		return ClassificationCategorySchema().dump(self)

	def FromDict(data: Dict[str, Any]) -> 'ClassificationCategory':
		return ClassificationCategorySchema().load(data)
