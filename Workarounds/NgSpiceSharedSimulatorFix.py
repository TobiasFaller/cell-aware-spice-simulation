from PySpice.Spice.NgSpice.SimulationType import SIMULATION_TYPE, LAST_VERSION

# Update to most up-to-date ng-spice version
_PREVIOUS_VERSION = LAST_VERSION
LAST_VERSION = 42
for version in range(_PREVIOUS_VERSION, LAST_VERSION + 1):
	SIMULATION_TYPE[version] = SIMULATION_TYPE[_PREVIOUS_VERSION]


def FixNgSpiceSharedSimulator(simulator):
	""" Replaces the shared ng-spice simulator _send_char method
		to fix a bug where the simulator writes "gmin stepping"
		warnings onto stderr which aborts the simulation.
		We want the simulation to continue instead.
	"""
	if simulator.__class__.__name__ != 'NgSpiceSharedCircuitSimulator':
		return simulator

	# NOTE: Fixed implementation handling gmin stepping.
	# Do not import the underlined as they are otherwise
	# declared as free vars which makes code swapping fail.
	# This means we have to live with the IDE complaining about
	# missing variables here.
	def _ngspice_send_char_fix(message_c, ngspice_id, user_data):
		"""Callback for sending output from stdout, stderr to caller"""
		self = ffi.from_handle(user_data)
		message = ffi_string_utf8(message_c)
		_module_logger.debug(message)

		# split message in "<prefix><match = ' '><content>"
		prefix, _, content = message.partition(' ')
		if prefix == 'stderr':
			# gmin stepping warnings might be written to the stderr.
			# So only regard as an error if there is actually
			# an 'Error:' prefix.
			if content.startswith('Error:'):
				if content.strip() == "Note: can't find init file.":
					self._spinit_not_found = True
					self._logger.warning('spinit was not found')
				self._error_in_stderr = True
				self._logger.error(content)
			else:
				self._logger.warning(content)
		else:
			# Fixme: Ngspice writes error on stdout and stderr ...
			if content.startswith('Error:'):
				self._error_in_stdout = True

		if prefix == 'stderr':
			self._stderr.append(content)
		else:
			self._stdout.append(content)

		# Callback for python handling messages
		return self.send_char(message, ngspice_id)

	simulator._ngspice_shared._send_char.__code__ = _ngspice_send_char_fix.__code__
	return simulator
