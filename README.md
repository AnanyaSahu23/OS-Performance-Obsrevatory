# OS Performance Observatory

A Linux performance-monitoring application with a Streamlit dashboard, system
health indicators, CPU and memory assessments, process attribution, structured
diagnoses, recommendations and SQLite history. Findings indicate possible
resource pressure, not confirmed bottlenecks. No automatic corrective actions
are taken.

## Requirements

- Python 3.11+ required
- Linux/Ubuntu (native, VM or WSL; WSL is not required)
- Dependencies from `requirements.txt`

## Setup

From the repository root, using a Python 3.11+ interpreter:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Run the dashboard

```bash
streamlit run app.py
```

Open the URL printed by Streamlit (usually <http://localhost:8501>).
The dashboard refreshes every two seconds. In a VM, metrics describe the guest,
not the host.

## Check the collector

```bash
python -c "from collector import collect_snapshot; print(collect_snapshot())"
```

Snapshots contain UTC timestamps, CPU/RAM/swap metrics, running-process counts,
process records and separate CPU/RAM rankings. Initial CPU readings may be zero;
processes that exit or become inaccessible during collection are skipped.

## System Health Indicator

Reports Healthy, Warning, Critical or Unknown using thresholds in `config.py`.

### Initial threshold configuration

| Indicator | Warning | Critical |
| --- | --- | --- |
| CPU utilization | >= 80% for 3 consecutive samples | >= 95% for 3 consecutive samples |
| Available RAM | <= 20% | <= 10% |
| RAM utilization | >= 90% | >= 95% |
| Swap utilization | >= 50% with RAM >= 85% | >= 80% with RAM >= 90% |

Any critical condition produces Critical; otherwise a warning produces Warning.
No breached thresholds produces Healthy. Missing required RAM data produces
Unknown. A single CPU spike does not establish sustained pressure.

## CPU and Memory Bottleneck Assessment

Uses `BOTTLENECK_CONFIG` in `config.py`, separately from health thresholds:

- **CPU:** >= 80% for three consecutive samples indicates possible pressure.
  A high reading without sustained evidence is treated as transient.
- **Memory:** <= 20% available RAM is a low-memory indicator. High RAM utilization,
  elevated swap or increasing swap provides corroborating evidence.
- Missing or invalid required metrics produce an insufficient-evidence finding.

Each finding includes supporting observations and an investigation step.
Resource utilization alone does not establish application performance impact.

## Diagnosis and recommendations

Diagnoses include severity, evidence, observed processes and duration when known.
Memory duration measures low available RAM, not the entire corroborated condition.
Configurable recommendations provide informational next steps for CPU, memory
and swap conditions.

## Historical storage

SQLite stores metrics, health samples and diagnosis changes in
`data/observatory.sqlite3`. The dashboard displays trends and recent diagnoses.
The path and default 30-day retention are configured in `STORAGE_CONFIG`.
Use a writable local filesystem, not a network or VM-shared folder.

## Feature D: Statistical Process Attribution

### Methodology

- Retains up to 120 snapshots; requires at least 15 valid paired observations.
- Uses Pearson correlation between system and process CPU utilization.
- Compares process RSS changes with available RAM and swap-used changes.
- Identifies process instances by PID and creation time to reduce PID-reuse errors.

Configure thresholds and history limits in `PROCESS_ATTRIBUTION_CONFIG`.

### Interpretation and limitations

Positive CPU correlation means activity tends to rise together. Negative
RSS/available-RAM correlation means process growth tends to coincide with less
available RAM. Positive RSS/swap correlation means process growth tends to
coincide with more swap use. These associations do not prove causation.

Constant or insufficient data cannot produce meaningful correlations. Missing
process CPU observations are treated as zero, which can distort results when a
process is inaccessible rather than stopped.

## Controlled Stress Test

Use the dashboard's Start/Stop controls for a bounded CPU demonstration: default
10 seconds and one worker, limited to 30 seconds and two workers (or available CPUs).
Workers expire independently and are cleaned up by the controller. Configuration
is in `STRESS_TEST_CONFIG`. One test can run per server process across its sessions;
the existing pipeline displays actual observations without guaranteeing pressure detection.

## Run tests

```bash
python -m unittest discover -s tests -v
```

## Implementation boundaries

- `collector.py`: system and process sampling.
- `health.py`, `analyzer.py`, `attribution.py`: health, pressure and process analysis.
- `diagnosis.py`, `recommendations.py`: structured findings and next steps.
- `storage.py`: SQLite persistence; `app.py`: dashboard integration.
- `tests/`: unit and integration tests.
