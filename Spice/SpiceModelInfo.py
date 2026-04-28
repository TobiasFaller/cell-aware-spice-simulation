from typing import List, Dict,  Any
from dataclasses import dataclass

from PySpice.Spice.Parser import SubCircuit


@dataclass
class SpiceModelInfo:
	name: str
	attributes: Dict[str, Any]
	inputs: List[str]
	outputs: List[str]
	clocks: List[str]
	clockPolarities: List[str]
	grounds: List[str]
	supplies: List[str]
	internal: List[str]

	equations: Dict[str, str]
	states: List[str]

	cell: SubCircuit = None
