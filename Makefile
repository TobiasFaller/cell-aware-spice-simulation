ALL_STEPS=liberty-meta fault-injection fault-evaluation fault-plot fault-export
ALL_FAULTS=stuck-at open bridge port-open port-bridge transistor-drive transistor-leak bridge
ALL_INPUTS=static transition
ALL_CELLS=^.*$$

export GENERIC_LIB_CONF=Configurations/generic.jsonc
export GENERIC_LIB_PATH=~/Source/freitest/data/cell-libraries/generic
export GENERIC_OUT_PATH=~/Source/data/model-cellaware/generic
export GENERIC_CELLS=^(BUF|AND|OR|XOR|INV|NAND|NOR|XNOR|MUX|DL|DLR|DFF|DFFR)$$


export RUN_STEPS=${ALL_STEPS}
export RUN_FAULTS=${ALL_FAULTS}
export RUN_INPUTS=${ALL_INPUTS}
export RUN_CELLS=${ALL_CELLS}
export DEBUG_VERBOSE=--verbose
export DEBUG_LIBERTY_META=
export DEBUG_FAULT_INJECTION=--debug export-plan
export DEBUG_FAULT_EVALUATION=
export DEBUG_FAULT_PLOT=
export DEBUG_CONVERT_JSON=
export DEBUG_CONVERT_STATEMACHINE=
export DEBUG_CONVERT_UDFM=
export DEBUG_STATE_EXPLORATION=

all:
	@echo "Targets:"
	@echo "  generic: FreiTest minimal generic cell library"

install:
	pypy3 -m venv .venv
	. .venv/bin/activate && pypy3 -m pip install -r requirements.txt

.PHONY: generic
generic: install
	. .venv/bin/activate && $(MAKE) ${RUN_STEPS} \
		LIB_NAME='generic' \
		LIB_CONF='$(shell realpath ${GENERIC_LIB_CONF})' \
		LIB_PATH='$(shell realpath ${GENERIC_LIB_PATH})' \
		OUTPUT_PATH="$(shell realpath ${GENERIC_OUT_PATH})" \
		CELLS='${RUN_CELLS}' \
		INPUTS='${RUN_INPUTS}' \
		FAULTS='${RUN_FAULTS}'


liberty-meta:
	mkdir -p "${OUTPUT_PATH}/0-meta"
	pypy3 UtilityLibertyToMeta.py ${DEBUG_VERBOSE} ${DEBUG_LIBERTY_META} \
		--library "${LIB_CONF}" \
		--library-path "${LIB_PATH}" \
		--output "${OUTPUT_PATH}/0-meta/library.meta.json" \
		2>&1 | tee "${OUTPUT_PATH}/0-meta/process.log"
	ln -sTf "${OUTPUT_PATH}/0-meta/library.meta.json" "${OUTPUT_PATH}/0-meta.library.json"
	ln -sTf "${OUTPUT_PATH}/0-meta/process.log" "${OUTPUT_PATH}/0-meta.log"

fault-injection:
	@rm -rf "${OUTPUT_PATH}/1-simulation/jobs"; true
	mkdir -p "${OUTPUT_PATH}/1-simulation"
	pypy3 ToolSpiceToSimulationData.py ${DEBUG_VERBOSE} ${DEBUG_FAULT_INJECTION} \
		--library "${LIB_CONF}" \
		--library-path "${LIB_PATH}" \
		--output "${OUTPUT_PATH}/1-simulation" \
		--output-compress \
		--cells "${CELLS}" \
		--inputs "${INPUTS}" \
		--faults "${FAULTS}" \
		2>&1 | tee "${OUTPUT_PATH}/1-simulation/process.log"
	ln -sTf "${OUTPUT_PATH}/1-simulation/simulation.data.json.gz" "${OUTPUT_PATH}/1-simulation.data.json.gz"
	ln -sTf "${OUTPUT_PATH}/1-simulation/simulation.status.json.gz" "${OUTPUT_PATH}/1-simulation.status.json.gz"
	ln -sTf "${OUTPUT_PATH}/1-simulation/process.log" "${OUTPUT_PATH}/1-simulation.log"

	gunzip -c "${OUTPUT_PATH}/1-simulation/simulation.data.json.gz" \
		| pypy3 -m json.tool > "${OUTPUT_PATH}/1-simulation/simulation.data.json"
	gunzip -c "${OUTPUT_PATH}/1-simulation/simulation.status.json.gz" \
		| pypy3 -m json.tool > "${OUTPUT_PATH}/1-simulation/simulation.status.json"
	ln -sTf "${OUTPUT_PATH}/1-simulation/simulation.data.json" "${OUTPUT_PATH}/1-simulation.data.json"
	ln -sTf "${OUTPUT_PATH}/1-simulation/simulation.status.json" "${OUTPUT_PATH}/1-simulation.status.json"

fault-evaluation:
	mkdir -p "${OUTPUT_PATH}/2-faults"
	pypy3 ToolSimulationDataToFaultData.py ${DEBUG_VERBOSE} ${DEBUG_FAULT_EVALUATION} \
		--input "${OUTPUT_PATH}/1-simulation/simulation.data.json.gz" \
		--input-compress \
		--output "${OUTPUT_PATH}/2-faults" \
		--output-compress \
		2>&1 | tee "${OUTPUT_PATH}/2-faults/process-evaluation.log"
	ln -sTf "${OUTPUT_PATH}/2-faults/fault.data.json.gz" "${OUTPUT_PATH}/2-fault.data.json.gz"
	ln -sTf "${OUTPUT_PATH}/2-faults/fault.status.json.gz" "${OUTPUT_PATH}/2-fault.status.json.gz"
	ln -sTf "${OUTPUT_PATH}/2-faults/process-evaluation.log" "${OUTPUT_PATH}/2-fault-evaluation.log"

	gunzip -c "${OUTPUT_PATH}/2-faults/fault.data.json.gz" \
		| pypy3 -m json.tool > "${OUTPUT_PATH}/2-faults/fault.data.json"
	gunzip -c "${OUTPUT_PATH}/2-faults/fault.status.json.gz" \
		| pypy3 -m json.tool > "${OUTPUT_PATH}/2-faults/fault.status.json"
	ln -sTf "${OUTPUT_PATH}/2-faults/fault.data.json" "${OUTPUT_PATH}/2-fault.data.json"
	ln -sTf "${OUTPUT_PATH}/2-faults/fault.status.json" "${OUTPUT_PATH}/2-fault.status.json"

fault-export:
	pypy3 ToolFaultDataToJson.py ${DEBUG_VERBOSE} ${DEBUG_CONVERT_JSON} \
		--input "${OUTPUT_PATH}/2-faults/fault.data.json.gz" \
		--input-compress \
		--output "${OUTPUT_PATH}/2-faults/fault.patterns.json.gz" \
		--output-compress \
		2>&1 | tee "${OUTPUT_PATH}/2-faults/process-json.log"
	ln -sTf "${OUTPUT_PATH}/2-faults/fault.patterns.json.gz" "${OUTPUT_PATH}/2-fault.patterns.json.gz"
	ln -sTf "${OUTPUT_PATH}/2-faults/process-json.log" "${OUTPUT_PATH}/2-fault-json.log"

	pypy3 ToolFaultDataToStateMachine.py ${DEBUG_VERBOSE} ${DEBUG_CONVERT_STATEMACHINE} \
		--input "${OUTPUT_PATH}/2-faults/fault.data.json.gz" \
		--input-compress \
		--output "${OUTPUT_PATH}/2-faults/fault.statemachine.json.gz" \
		--output-compress \
		2>&1 | tee "${OUTPUT_PATH}/2-faults/process-statemachine.log"
	ln -sTf "${OUTPUT_PATH}/2-faults/fault.statemachine.json.gz" "${OUTPUT_PATH}/2-fault.statemachine.json.gz"
	ln -sTf "${OUTPUT_PATH}/2-faults/process-statemachine.log" "${OUTPUT_PATH}/2-fault-statemachine.log"

	pypy3 ToolFaultDataToUdfm.py ${DEBUG_VERBOSE} ${DEBUG_CONVERT_UDFM} \
		--input "${OUTPUT_PATH}/2-faults/fault.data.json.gz" \
		--input-compress \
		--output "${OUTPUT_PATH}/2-faults/fault.udfm.gz" \
		--output-compress \
		--format tessent-compact \
		2>&1 | tee "${OUTPUT_PATH}/2-faults/process-udfm.log"
	ln -sTf "${OUTPUT_PATH}/2-faults/fault.udfm.gz" "${OUTPUT_PATH}/2-fault.udfm.gz"
	ln -sTf "${OUTPUT_PATH}/2-faults/process-udfm.log" "${OUTPUT_PATH}/2-fault-udfm.log"

	gunzip -c "${OUTPUT_PATH}/2-faults/fault.patterns.json.gz" \
		| pypy3 -m json.tool > "${OUTPUT_PATH}/2-faults/fault.patterns.json"
	gunzip -c "${OUTPUT_PATH}/2-faults/fault.statemachine.json.gz" \
		| pypy3 -m json.tool > "${OUTPUT_PATH}/2-faults/fault.statemachine.json"
	gunzip -c "${OUTPUT_PATH}/2-faults/fault.udfm.gz" \
		> "${OUTPUT_PATH}/2-faults/fault.udfm"
	ln -sTf "${OUTPUT_PATH}/2-faults/fault.patterns.json" "${OUTPUT_PATH}/2-fault.patterns.json"
	ln -sTf "${OUTPUT_PATH}/2-faults/fault.statemachine.json" "${OUTPUT_PATH}/2-fault.statemachine.json"
	ln -sTf "${OUTPUT_PATH}/2-faults/fault.udfm" "${OUTPUT_PATH}/2-fault.udfm"

fault-plot:
	pypy3 UtilityFaultStatistics.py ${DEBUG_VERBOSE} ${DEBUG_FAULT_PLOT} \
		--input "${OUTPUT_PATH}/2-faults/fault.status.json.gz" \
		--input-compress \
		--output "${OUTPUT_PATH}/2-fault.status.png" \
		2>&1 | tee "${OUTPUT_PATH}/2-faults/process-plot.log"
	ln -sTf "${OUTPUT_PATH}/2-faults/process-plot.log" "${OUTPUT_PATH}/2-fault-plot.log"

