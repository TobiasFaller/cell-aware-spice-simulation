from PySpice.Spice.Netlist import Circuit
from PySpice.Spice.Simulation import CircuitSimulation
from PySpice.Unit import *
from PySpice.Unit.SiUnits import *

from Spice.SpiceModelInfo import SpiceModelInfo
from Spice.SpiceParameters import TestbenchParameters, SimulationParameters, SimulationType


class SpiceTestbenchGenerator:
	""" This is a wrapper creating a testbench for the SPICE simulation. """

	def __init__(self, modelInfo: SpiceModelInfo, testbenchParams: TestbenchParameters, simulationParams: SimulationParameters):
		""" Creates the testbench circuitry around the spi cell model.

			The testbench includes the following components:
			- supply voltage (VDD); value set by TestbenchParameters.SUPPLY_VOLTAGE
			- voltage sources for each input of the cell
				- SimulationType.STATIC_OPERATING_POINT simulation, the input has one constant value
					TestbenchParameters.INPUT_VOLTAGE_LOW / TestbenchParameters.INPUT_VOLTAGE_HIGH
				- SimulationType.TRANSITION simulation, the input can have different values over time points
			- series resistance for each input voltage of size TestbenchParameters.INPUT_SERIES_RESISTANCE
			- output series resistance (TestbenchParameters.OUTPUT_SERIES_RESISTANCE) as well as two resistors
				creating a path to VDD & GND (TestbenchParameters.OUTPUT_LEAKAGE_RESISTOR)
			- load capacitor from TestbenchParameters.LOAD_CAPACITANCE
		"""
		def _GetVoltageLevel(level) -> Volt:
			if level == 0 or level == '0':
				return float(testbenchParams.INPUT_VOLTAGE_LOW)
			elif level == 1 or level == '1':
				return float(testbenchParams.INPUT_VOLTAGE_HIGH)
			elif level == 'X':
				return 0.5*float(testbenchParams.INPUT_VOLTAGE_HIGH) \
					+ 0.5*float(testbenchParams.INPUT_VOLTAGE_LOW)
			else:
				raise ValueError(f"Unknown logic value {level} to convert to voltage level")

		self._circuit = Circuit("Testbench")

		# Include dependencies
		for model in testbenchParams.TRANSISTOR_MODELS:
			# Include models directly (for testing purposes)
			# with open(model.absolute(), 'rt', encoding='utf-8') as stream:
			# 	self._circuit.raw_spice += '\n'.join([line.strip() for line in stream if line.strip() != '.end'])
			self._circuit.include(f'{model.absolute()}')
		self._circuit.subcircuit(testbenchParams.CELL_MODEL)

		# Start of testbench
		cellInputs = [
			{  # Mapping of cell supply ports to testbench supply ports
				testbenchParams.SUPPLY_NAME_VDD: 'VDD',
				testbenchParams.SUPPLY_NAME_VSS: '0',
			}.get(node, node) for node in testbenchParams.CELL_MODEL.external_nodes
		]
		self._circuit.X(modelInfo.name.lower(), modelInfo.name, *cellInputs)
		self._circuit.V('supply_VDD', 'IN_VDD', '0', testbenchParams.SUPPLY_VOLTAGE)
		self._circuit.R('s_Vdd', 'IN_VDD', 'VDD', testbenchParams.SUPPLY_SERIES_RESISTANCE)
		self._circuit.C('p_Vdd', 'VDD', '0', testbenchParams.SUPPLY_PARASITIC_CAPACITANCE)

		# Create input voltages for the cell
		for port_index,  port in enumerate(modelInfo.inputs + modelInfo.clocks):
			logicValues = simulationParams.INPUT_VALUES[port] if port in modelInfo.inputs \
				else simulationParams.INPUT_CLOCKS[port]
			if len(logicValues) > 1:
				inputPoints = [
					f"{float(time)}s {_GetVoltageLevel(voltage)}V"
					for voltage, time in zip(logicValues, simulationParams.INPUT_STEPS)
				]
				supplyFunction = f"PWL({' '.join(inputPoints)})"
			else:
				supplyFunction = f"DC {_GetVoltageLevel(logicValues[0])}V"

			self._circuit.V(f'supply_in_{port}', f'IN_{port}', '0', supplyFunction)
			self._circuit.R(f'sr_in_{port}', f'IN_{port}', port, testbenchParams.INPUT_SERIES_RESISTANCE)
			self._circuit.C(f'pc_in_{port}_Vdd', port, 'VDD', testbenchParams.INPUT_PARASITIC_CAPACITANCE)
			self._circuit.C(f'pc_in_{port}_Vss', port, '0', testbenchParams.INPUT_PARASITIC_CAPACITANCE)

		# Create output loads
		for port in modelInfo.outputs:
			self._circuit.R(f'sr_out_{port}', port, f'OUT_{port}', testbenchParams.OUTPUT_SERIES_RESISTANCE)
			self._circuit.C(f'pc_out_{port}_Vdd', port, 'VDD', testbenchParams.OUTPUT_PARASITIC_CAPACITANCE)
			self._circuit.C(f'pc_out_{port}_Vss', port, '0', testbenchParams.OUTPUT_PARASITIC_CAPACITANCE)
			self._circuit.R(f'loadr_out_{port}_Vdd', f'OUT_{port}', 'VDD', testbenchParams.OUTPUT_LEAKAGE_RESISTOR)
			self._circuit.R(f'loadr_out_{port}_Vss', f'OUT_{port}', '0', testbenchParams.OUTPUT_LEAKAGE_RESISTOR)
			self._circuit.C(f'loadc_out_{port}', f'OUT_{port}', '0', testbenchParams.LOAD_CAPACITANCE)

	def ExportTestbench(self, path, simulationParams: SimulationParameters):
		simulation = CircuitSimulation(self._circuit, temperature=25, nominal_temperature=25)
		simulation.options(GMIN=simulationParams.SIMULATION_GMIN)
		simulation.SIMULATOR = 'ngspice'
		if simulationParams.SIMULATION_TYPE == SimulationType.STATIC_OPERATING_POINT:
			simulation.operating_point()
		elif simulationParams.SIMULATION_TYPE == SimulationType.TRANSITION:
			simulation.transient(
				step_time=simulationParams.SIMULATION_STEP,
				end_time=simulationParams.SIMULATION_TIME
			)
		else:
			raise ValueError(f"Unknown simulation type {simulationParams.SIMULATION_TYPE}")

		with open(path, 'wt', encoding='utf-8') as file:
			file.write(str(simulation))

	@property
	def circuit(self):
		return self._circuit
