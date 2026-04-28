from pathlib import Path
from jinja2 import Environment, DictLoader
from enum import StrEnum
from io import TextIOBase

from Udfm.Udfm import Udfm


TEMPLATE_UDFM_SIMPLE = """
<!- macro conditions(ports) -!>
	<!- for name, value in ports.items() -!>
		"<<name>>"=<<value>>
		<!- if not loop.last -!>, <! endif -!>
	<!- endfor -!>
<!- endmacro -!>
<!- macro mapTestType(testType) -!>
	<!- if testType == "static" -!>
		StaticTest
	<!- elif testType == "transition" -!>
		TransitionTest
	<!- elif testType == "dynamic" -!>
		DynamicTest
	<!- endif -!>
<!- endmacro -!>

<!- for cellName, cell in udfm.cells.items() -!>
Cell "<<cellName>>" {
	<!- for faultName, fault in cell.faults.items() !>
	Fault "<<faultName>>" {
		<!- for alternative in fault.alternatives !>
		<!- if compact !>
		test {<< mapTestType(alternative.testType) >> << conditions(alternative.testEffects) >>; Condition << conditions(alternative.testConditions) >>;}
		<!- else !>
		test {
			<< mapTestType(alternative.testType) >> << conditions(alternative.testEffects) >>;
			Condition << conditions(alternative.testConditions) >>;
		}
		<!- endif !>
		<!- endfor !>
	}
	<!- endfor !>
}
<! endfor !>
"""

TEMPLATE_UDFM_TESSENT = """
<!- macro conditions(ports) -!>
	<!- for name, value in ports.items() -!>
		"<<name>>": <<value>>
		<!- if not loop.last -!>; <! endif -!>
	<!- endfor -!>
<!- endmacro -!>
<!- macro mapTestType(testType) -!>
	<!- if testType == "static" -!>
		StaticTest
	<!- elif testType == "transition" -!>
		TransitionTest
	<!- elif testType == "dynamic" -!>
		DynamicTest
	<!- endif -!>
<!- endmacro -!>

UDFM {
	version : 1;
	Properties {
		"library-name"	: "<<library>>;
		"created-by"	: "<<createdBy>>"
	}
}
<!- for faultCategory in faultCategories !>
UdfmType ("<<faultCategory>>") {
	<!- for cellName, cell in udfm.cells.items() !>
	Cell ("<<cellName>>") {
		<!- for faultName, fault in cell.faults.items() if fault.faultCategory == faultCategory !>
		Fault ("<<faultName>>") {
			<!- for alternative in fault.alternatives !>
			<!- if compact !>
			Test {<< mapTestType(alternative.testType) >> {<< conditions(alternative.testEffects) >>;} Conditions {<< conditions(alternative.testConditions) >>;}}
			<!- else !>
			Test {
				<< mapTestType(alternative.testType) >> {<< conditions(alternative.testEffects) >>;}
				Conditions {<< conditions(alternative.testConditions) >>;}
			}
			<!- endif !>
			<!- endfor !>
		}
		<!- endfor !>
	}
	<!- endfor !>
}
<!- endfor !>
"""


class ExportFormat(StrEnum):
	Simple = 'simple'
	SimpleCompact = 'simple-compact'
	Tessent = 'tessent'
	TessentCompact = 'tessent-compact'

	def __str__(self) -> str:
		return self.value


def ExportUdfm(stream: TextIOBase, udfm: Udfm, format: ExportFormat, library: str = "Unknown library") -> None:
	environment = Environment(loader=DictLoader({
			'simple': TEMPLATE_UDFM_SIMPLE,
			'tessent': TEMPLATE_UDFM_TESSENT
		}),
		# Makes escaping easier because curly brackets
		# are used inside the UDFM grammar a lot.
		block_start_string = '<!',
		block_end_string= '!>',
		variable_start_string = '<<',
		variable_end_string= '>>',
		comment_start_string = '<#',
		comment_end_string= '#>'
	)

	def MapFaultCategory(faultCategory: str) -> str:
		return 'all' if faultCategory is None else faultCategory

	template, compact = {
		ExportFormat.Simple: ('simple', False),
		ExportFormat.SimpleCompact: ('simple', True),
		ExportFormat.Tessent: ('tessent', False),
		ExportFormat.TessentCompact: ('tessent', True),
	}.get(format)

	template = environment.get_template(template)
	stream.write(template.render(
		udfm=udfm,
		# Use dictionary to have ordered set behavior
		faultCategories=dict.fromkeys([
			MapFaultCategory(fault.faultCategory)
				for cell in udfm.cells.values()
				for fault in cell.faults.values()
		]).keys(),
		compact=compact,
		library=library,
		createdBy="PySpice cell simulation"
	))
