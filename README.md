# OS Performance Observatory

The current implementation includes a `psutil` collector, a Streamlit
dashboard, a rule-based health indicator, and an evidence-based assessment of
possible CPU and memory pressure. It does not confirm performance bottlenecks
or recommend automated corrective actions.

## Requirements

- Python 3.11 or newer (recommended)
- Linux/Ubuntu for the intended target platform
- Dependencies from `requirements.txt`

SQLite, `/proc` access, and CSV support can be added later using Python's
standard library; no extra packages are needed for them.

## Setup

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Run the dashboard

```bash
streamlit run app.py
```

Open the local URL printed by Streamlit (usually <http://localhost:8501>).
The dashboard refreshes every two seconds and retains up to 60 observations
per browser session.

## Check the collector

```bash
python -c "from collector import collect_snapshot; print(collect_snapshot())"
```

Each snapshot includes a UTC timestamp, CPU utilization, RAM and swap metrics,
the number of processes currently reported as running, and separate CPU- and
memory-ranked process lists. CPU values are interval-based, so the initial
reading can be zero. Processes may exit or become inaccessible during a
sample and are skipped.

## System Health Indicator

The System Health Indicator classifies the observed system as Healthy, Warning, or Critical using configurable resource-pressure thresholds.

### Initial threshold configuration

| Indicator | Warning | Critical |
| --- | --- | --- |
| CPU utilization | >= 80% for 3 consecutive samples | >= 95% for 3 consecutive samples |
| Available RAM | <= 20% | <= 10% |
| RAM utilization | >= 90% | >= 95% |
| Swap utilization | >= 50% with RAM utilization >= 85% | >= 80% with RAM utilization >= 90% |

The thresholds are defined in `config.py` and can be adjusted for different testing environments.

### Decision rules

- Critical: At least one critical condition is met.
- Warning: No critical condition is met, but at least one warning condition is met.
- Healthy: No configured warning or critical condition is detected.
- Unknown: Required RAM information is invalid or unavailable.

CPU pressure is assessed over consecutive samples to avoid classifying a single spike as sustained pressure. Swap utilization is evaluated alongside RAM pressure rather than in isolation.

### Limitations

The indicator is a simplified, rule-based assessment of selected resource metrics. It does not establish the root cause of a performance problem or represent universal system health. Its results depend on the configured thresholds, sampling interval, operating system, workload, and available metrics.

## CPU and Memory Bottleneck Assessment

The assessment analyzes recent system metrics to identify evidence of possible
CPU or memory pressure. It reports separate CPU and memory findings, supporting
observations, and recommended investigation steps.

### Initial assessment rules

**CPU**

- CPU utilization at or above 80% for three consecutive samples indicates
  possible CPU pressure.
- A high current reading without enough sustained samples is reported as
  possible transient CPU pressure. Missing or invalid readings produce an
  insufficient-evidence result.
- High utilization does not establish that CPU activity is causing an
  application performance problem.

**Memory**

- Available RAM at or below 20% of total RAM is treated as a low-memory
  indicator.
- Low available RAM combined with high RAM utilization, elevated swap usage,
  or increasing swap usage provides stronger evidence of possible memory
  pressure. At or below 10% available RAM is the critical low-memory range.
- High RAM utilization or swap usage alone does not establish a bottleneck.
- Missing or invalid required metrics produce an insufficient-evidence result.

The thresholds and sample counts are project-defined defaults in
`BOTTLENECK_CONFIG` in `config.py`, separate from the health-indicator
thresholds. The dashboard samples every two seconds, so three consecutive CPU
samples represent approximately six seconds of observed pressure, depending
on collection and rendering time.

### Output and limitations

Each assessment includes a status, evidence, a recommended next step, and an
explicit limitation. The analyzer does not return a “Confirmed bottleneck”
status: confirmation requires evidence of actual performance impact, such as
increased application response time or slower task completion, alongside
further investigation.

## Run tests

```bash
python -m unittest discover -s tests -v
```

## Try a controlled CPU workload (Linux)

In a separate terminal, run `yes > /dev/null`, observe the dashboard, and stop
the workload with Ctrl+C. Use one instance only and stop it when finished.
Overall CPU percentage depends on the number of available CPU cores.

## Implementation boundaries

- `collector.py` owns operating-system sampling and returns plain dictionaries.
- `app.py` renders the dashboard and keeps short-lived metric history in the
  Streamlit session; it does not persist data.
- `health.py` and `analyzer.py` evaluate configured pressure indicators but do
  not diagnose or confirm performance bottlenecks.
- `tests/` covers the collector, health indicator, and assessment logic using
  synthetic metrics where appropriate.

This separation gives future analyzer, recommendation, and storage modules a
stable snapshot interface to integrate with.
