from PySpice.Spice.Netlist import Netlist
from PySpice.Probe.WaveForm import Analysis

def NetlistGetItemFix(self, attribute_name):
	if attribute_name in ['_elements', '_models', '_nodes']:
		return super(Netlist, self).__getattribute__(attribute_name)

	if attribute_name in self._elements:
		return self.element(attribute_name)
	elif attribute_name in self._models:
		return self.model(attribute_name)
	# Fixme: subcircuits
	elif attribute_name in self._nodes:
		return self.node(attribute_name)
	else:
		raise IndexError(attribute_name)   # KeyError


def AnalysisGetItemFix(self, attribute_name):
	if attribute_name in ['_nodes', '_branches', '_elements', '_internal_parameters']:
		return super(Analysis, self).__getattribute__(attribute_name)

	try:
		return self._get_item(attribute_name)
	except IndexError:
		return self._get_item(attribute_name.lower())


Netlist.__getitem__.__code__ = NetlistGetItemFix.__code__
Analysis.__getitem__.__code__ = AnalysisGetItemFix.__code__

# To have something to import
_ = None
