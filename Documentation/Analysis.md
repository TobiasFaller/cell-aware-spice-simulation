# Analysis

## Running the Analysis

Run the SPICE analysis via `python3 ToolSimulationDataToFaultData.py`.
Use `python3 ToolSimulationDataToFaultData.py --help` to get a list of all command line arguments.
Below is an example that analyses the previously generated simulation data for the BUF cell (buffer) with three fault types:

```bash
python3 ToolSimulationDataToFaultData.py \
    --input $(realpath ../fault-models/generic/simulation.data.json.gz) \
    --input-compress \
    --output $(realpath ../fault-models/generic) \
    --output-compress \
    --cells "^BUF$" \
    --faults "stuck-at open bridge" \
    --debug \
        export-differences \
        export-trimmed-simulations \
    --verbose \
```

The tool performs the following steps in order:

1. Simulation data is imported and sanity-checked for issues
2. Undefiend values from the liberty-model simulation are applied and differences between fault-free and faulty simulations are computed
3. Simulations are checked if they are deterministic (building a prefix code)
4. Simulations are trimmed to required length
5. Faults are evaluated for testabililty and unnecessary patterns are removed
6. Fault data is exported (**fault model**)
   1. Fault behavior in `fault.data.json`
   2. Fault status in `fault.status.json`

## Fault Model

Below is an excerpt of a final fault model which contains a stuck-at fault and an open fault.
The fault model contains only faults that are testable and not redundant.

```json
"BUF": {
    "stuck-at 1 IN1": [
        { "inputs": { "IN1": "0" }, "outputs": { "OUT": "1" } }
    ],
    "stuck-at 0 IN1": [
        { "inputs": { "IN1": "1" }, "outputs": { "OUT": "0" } }
    ],
    "open M3:source": [
        { "inputs": { "IN1": "1" }, "outputs": { "OUT": "X" } },
        { "inputs": { "IN1": "01" }, "outputs": { "OUT": "00" } }
    ],
    ...
}
```

## Fault Status

The full list of faults are given in the fault status report.
If a fault is equivalent to another fault, it is referenced and not included in the fault model.

The analysis produces the following fault statuses:

- `detected`: The fault is testable and produces a logic value opposite to the normal behaviour.
- `undetected`: The fault is not testable and the cell behaves like a normal cell for the inputs applied.
- `undefined`: The fault is not testable and the cell potentially produces undefined logic outputs.
- `equivalent-to`: The fault behaves like the fault mentioned in the `equivalent` field.

```json
"stuck-at": {
	"BUF": {
		"stuck-at 1 IN1": { "status": "detected" },
		"stuck-at 0 IN1": { "status": "detected" },
		"stuck-at 1 OUT": { "status": "equivalent-to", "equivalent": "stuck-at 1 IN1" },
		"stuck-at 0 OUT": { "status": "equivalent-to", "equivalent": "stuck-at 0 IN1" }
	}
},
"open": {
	"BUF": {
		"open M1:drain": { "status": "equivalent-to", "equivalent": "stuck-at 1 IN1" },
		"open M1:source": { "status": "undetected" },
		"open M1:bulk": { "status": "undetected" },
        "open M2:drain": { "status": "equivalent-to", "equivalent": "stuck-at 0 IN1" },
        "open M2:source": { "status": "undetected" },
        "open M2:bulk": { "status": "undetected" },
        "open M3:gate": { "status": "undetected" },
        "open M3:source": { "status": "detected" },
		...
	}
},
"bridge": {
	"BUF": {
        "bridge M1:source and M2:source": { "status": "undefined" },
        ...
    }
}
```
