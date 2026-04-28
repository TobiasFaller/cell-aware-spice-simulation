import logging

from pathlib import Path
from PySpice.Spice.Parser import SpiceParser, SubCircuit

def ParseSpiceCell(path: Path, name: str):
    parser = SpiceParser(path)
    for cell_statement in parser.subcircuits:
        if cell_statement.name == name:
            cell = SubCircuit(cell_statement.name, *cell_statement.nodes)
            SpiceParser._build_circuit(cell, cell_statement._statements, ground=0)
            return cell
    return None


import PySpice.Spice.Parser
PySpice.Spice.Parser._module_logger.setLevel(logging.ERROR)
