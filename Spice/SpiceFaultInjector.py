from enum import StrEnum
from typing import Iterator, Dict, Any, Union
from dataclasses import dataclass
from itertools import combinations
from collections import Counter

from PySpice.Spice.Netlist import Netlist, SubCircuit, Element, Node
from PySpice.Spice.BasicElement import Mosfet

from Spice.SpiceParameters import FaultParameters
from Spice.SpiceModelInfo import SpiceModelInfo


class FaultType(StrEnum):
	FaultFree = "fault-free"

	# Stuck-At fault models
	StuckAt = "stuck-at"

	# Cell-Aware fault models
	Open = "open"
	Bridge = "bridge"
	PortOpen = "port-open"
	PortBridge = "port-bridge"
	Tdrive = "transistor-drive"
	Tleak = "transistor-leak"

	def __str__(self) -> str:
		return self.value


@dataclass
class FaultInjection:
	faultType: FaultType
	faultParameters: Dict[str, Any]

	def __str__(self) -> str:
		fault = self.faultType
		params = self.faultParameters
		if fault == FaultType.FaultFree:
			return f'{fault}'
		elif fault == FaultType.StuckAt:
			return f'{fault} {params["stuck-at"]} {params["port"]}'
		elif fault == FaultType.Open:
			return f'{fault} {params["element"]}:{params["pin"]}'
		elif fault == FaultType.Bridge:
			return f'{fault} {params["element1"]}:{params["pin1"]} and {params["element2"]}:{params["pin2"]}'
		elif fault == FaultType.PortOpen:
			return f'{fault} {params["port"]}'
		elif fault == FaultType.PortBridge:
			return f'{fault} {params["port1"]} and {params["port2"]}'
		elif fault == FaultType.Tdrive:
			return f'{fault} {params["mosfet"]}'
		elif fault == FaultType.Tleak:
			return f'{fault} {params["mosfet"]}'
		else:
			raise ValueError(f'Unimplemented fault type {fault}')

	def __hash__(self):
		return hash((self.faultType, frozenset(self.faultParameters.items())))

	@property
	def path(self) -> str:
		return str(self).replace(' ', '-').replace(':', '-')


def _GetAllPins(netlist: Netlist):
	for element in netlist.elements:
		# Only target primitive elements for now
		if not isinstance(element, Element):
			continue

		for pin in element.pins:
			yield (element, pin)

def _GetAllFets(netlist: Netlist):
	for element in netlist.elements:
		if not isinstance(element, Mosfet):
			continue

		yield element.name


def PlanFaultInjections(model: SpiceModelInfo, faultType: FaultType) -> Iterator[FaultInjection]:
	ioPorts = set(model.inputs + model.outputs)

	if faultType == FaultType.FaultFree:
		yield FaultInjection(FaultType.FaultFree, {})

	elif faultType == FaultType.StuckAt:
		for port in model.inputs + model.outputs:
			yield FaultInjection(FaultType.StuckAt, {
				'port': port,
				'stuck-at': 1
			})
			yield FaultInjection(FaultType.StuckAt, {
				'port': port,
				'stuck-at': 0
			})

	elif faultType == FaultType.Open:
		allPins = list(_GetAllPins(model.cell))
		netOccurrences = Counter([pin.node.name for _, pin in allPins])

		for element, pin in allPins:
			# To reduce the number of faults analogous faults are not considered.
			# For example, a simple wire between only two transistor ports is only
			# considered once as a resistor at either port has the same effect.
			# When more than two transistor ports are connected to the same wire every
			# connection has to be considered and the test number can not be reduced.
			if netOccurrences[pin.node.name] == 2:
				continue

			yield FaultInjection(FaultType.Open, {
				'element': element.name,
				'pin': pin.name
			})

	elif faultType == FaultType.Bridge:
		# Each pin combination can have a bridge fault
		allPins = list(_GetAllPins(model.cell))
		for (element1, pin1), (element2, pin2) in combinations(allPins, 2):
			# Exclude stuck-at faults
			# if pin1.node.name in ioPorts or pin2.node.name in ioPorts:
			# 	continue

			# Exclude short of wire to itself
			if pin1.node.name == pin2.node.name:
				continue

			yield FaultInjection(FaultType.Bridge, {
				'element1': element1.name,
				'pin1': pin1.name,
				'element2': element2.name,
				'pin2': pin2.name
			})

	elif faultType == FaultType.PortOpen:
		for port in model.inputs + model.outputs:
			yield FaultInjection(FaultType.PortOpen, {
				'port': port
			})

	elif faultType == FaultType.PortBridge:
		for port1, port2 in combinations(model.inputs + model.outputs, 2):
			yield FaultInjection(FaultType.PortBridge, {
				'port1': port1,
				'port2': port2
			})

	elif faultType == FaultType.Tdrive:
		for fet in _GetAllFets(model.cell):
			yield FaultInjection(FaultType.Tdrive, {
				'mosfet': fet
			})

	elif faultType == FaultType.Tleak:
		for fet in _GetAllFets(model.cell):
			yield FaultInjection(FaultType.Tleak, {
				'mosfet': fet
			})


def InjectFault(model: SpiceModelInfo, faultInjection: FaultInjection, faultParams: FaultParameters) -> SubCircuit:
	# String names for nodes inserted in the spi file (e.g. for open faults);
	# they can be arbitrary as long as you don't use some reserved/already used strings
	DUMMY_NET_NAME = "dummy"
	NEW_DRAIN_NAME = "dummyDrain"
	NEW_SOURCE_NAME = "dummySource"

	if faultInjection.faultType == FaultType.FaultFree:
		return model.cell

	faultyCell = SubCircuit(model.cell.name, *model.cell.external_nodes, **model.cell.parameters)
	model.cell.copy_to(faultyCell)

	faultType = faultInjection.faultType
	injectParams = faultInjection.faultParameters

	def _SetPin(element: str, pin: str, node: Union[str, Node]) -> None:
		getattr(faultyCell.element(element), pin)._node \
			= faultyCell.get_node(node, True)

	def _GetPin(element: str, pin: str) -> Node:
		return getattr(faultyCell.element(element), pin)._node

	if faultType == FaultType.StuckAt:
		net = { 0: 'VSS', 1: 'VDD' }.get(injectParams['stuck-at'])
		faultyCell.R(f'fault_stuck_at', injectParams['port'], net, faultParams.STUCK_RESISTOR_VALUE)

	elif faultType == FaultType.Open:
		net = _GetPin(injectParams['element'], injectParams['pin'])
		_SetPin(injectParams['element'], injectParams['pin'], DUMMY_NET_NAME)
		faultyCell.R(f'fault_open', net, DUMMY_NET_NAME, faultParams.OPEN_RESISTOR_VALUE)

	elif faultType == FaultType.Bridge:
		net1 = _GetPin(injectParams['element1'], injectParams['pin1'])
		net2 = _GetPin(injectParams['element2'], injectParams['pin2'])
		faultyCell.R(f'fault_bridge', net1, net2, faultParams.SHORT_RESISTOR_VALUE)

	elif faultType == FaultType.PortOpen:
		for element, pin in _GetAllPins(faultyCell):
			if pin.node.name == injectParams['port']:
				_SetPin(element.name, pin.name, DUMMY_NET_NAME)
		faultyCell.R(f'fault_port_open', injectParams['port'], DUMMY_NET_NAME, faultParams.OPEN_RESISTOR_VALUE)

	elif faultType == FaultType.PortBridge:
		faultyCell.R(f'fault_port_bridge', injectParams['port1'], injectParams['port2'], faultParams.SHORT_RESISTOR_VALUE)

	elif faultType == FaultType.Tdrive:
		netDrain = _GetPin(injectParams['mosfet'], 'drain')
		netSource = _GetPin(injectParams['mosfet'], 'source')
		_SetPin(injectParams['mosfet'], 'drain', NEW_DRAIN_NAME)
		_SetPin(injectParams['mosfet'], 'source', NEW_SOURCE_NAME)
		faultyCell.R(f'fault_tdrive_drain', netDrain, NEW_DRAIN_NAME, faultParams.T_DRIVE_RESISTOR_VALUE)
		faultyCell.R(f'fault_tdrive_source', netSource, NEW_SOURCE_NAME, faultParams.T_DRIVE_RESISTOR_VALUE)

	elif faultType == FaultType.Tleak:
		netDrain = _GetPin(injectParams['mosfet'], 'drain')
		netSource = _GetPin(injectParams['mosfet'], 'source')
		faultyCell.R(f'fault_tleak', netDrain, netSource, faultParams.T_LEAK_RESISTOR_VALUE)

	return faultyCell
