from PySpice.Spice.Netlist import Netlist


def _copy_to_fix(self: Netlist, netlist):
	for name in self.node_names:
		if name != str(self._ground_name):
			netlist._add_node(name)

	for subcircuit in self.subcircuits:
		netlist.subcircuit(subcircuit)

	for element in self.elements:
		element.copy_to(netlist)

	for name, model in self._models.items():
		netlist._models[name] = model.clone()

	netlist.raw_spice = str(self.raw_spice)

	return netlist

Netlist.copy_to.__code__ = _copy_to_fix.__code__

# To have something to import
_ = None
