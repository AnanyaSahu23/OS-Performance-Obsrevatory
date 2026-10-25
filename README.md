# OS Performance Observatory

The current implementation includes a `psutil` collector, a Streamlit
dashboard, a rule-based health indicator, evidence-based CPU and memory
assessments, structured diagnosis records, configurable informational
recommendations, and SQLite history for metric samples and diagnosis changes.
It does not confirm performance bottlenecks or take corrective action.

## Requirements

- **Python 3.11+ required** (`storage.py` uses SQLite error-code attributes added in Python 3.11)
- Linux/Ubuntu for the intended target platform
- Dependencies from `requirements.txt`

SQLite support uses Python's standard-library `sqlite3` module. System metrics
are collected through `psutil`.

WSL is not required. The intended target is Linux, including native Ubuntu,
Ubuntu under WSL 2, Ubuntu virtual machines, and compatible Linux distributions.
Linux VMs monitor guest resources, not the macOS or Windows host.

## Setup

From the repository root, check `python3 --version`; it must report 3.11 or newer.
If `python3` is older, use an installed Python 3.11+ interpreter (for example,
`python3.11`) in the virtual-environment creation command. Ensure that interpreter's
virtual-environment support is installed.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

The default SQLite location is `data/observatory.sqlite3` relative to the repository.
Ensure this directory is writable, or set `STORAGE_CONFIG["database_path"]` in
`config.py` to another writable local location. The directory must allow SQLite
to create its database, WAL and shared-memory files. Use a local filesystem rather
than a network or VM-shared folder.

## Run the dashboard

```bash
streamlit run app.py
```

Open the local URL printed by Streamlit (usually <http://localhost:8501>).
The dashboard refreshes every two seconds, retains a short in-session window
for live analysis, and stores metric samples and diagnosis changes in SQLite.

## Check the collector

```bash
python -c "from collector import collect_snapshot; print(collect_snapshot())"
```

Each snapshot includes a UTC timestamp, CPU utilization, RAM and swap metrics,
the number of processes currently reported as running, all accessible process
records, and separate CPU- and memory-ranked process lists. Process records
include PID, process creation time, CPU utilization, resident memory (RSS) in
bytes, and memory utilization. CPU values are interval-based, so the initial
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
samples span approximately four seconds between the first and last observation,
depending on collection and rendering time.

### Output and limitations

Each assessment includes a status, evidence, a recommended next step, and an
explicit limitation. The analyzer does not return a “Confirmed bottleneck”
status: confirmation requires evidence of actual performance impact, such as
increased application response time or slower task completion, alongside
further investigation.

## Diagnosis and recommendations

`diagnosis.py` converts analyzer findings into structured records containing
condition, status, severity, evidence status, supporting evidence, observed
processes, and a duration estimate when the sample history supports one.
Observed top processes are presented as resource consumers, not as proven
causes. Missing duration evidence remains unavailable rather than being
invented.

`duration_scope` identifies the metric window being measured. Memory duration
measures **low available RAM**, including when low RAM predates corroborating
swap or utilization evidence; it does not assert that the combined pressure
condition persisted for that entire interval. Unknown analyzer statuses remain
unknown. Missing process lists leave the other diagnosis evidence usable.

`recommendations.py` selects informational next steps using configurable rules
in `config.py`. Recommendations do not terminate processes or alter system
settings. Edit `RECOMMENDATION_RULES` to tune wording or add rules.
The dashboard uses this dedicated engine as its recommendation authority.
The analyzer retains its original generic recommendation strings for existing
callers; those strings are not a second dashboard recommendation path. Structured
swap evidence selects rising-swap or elevated-swap guidance when RAM is low.

## Historical storage

`storage.py` stores samples and diagnosis state transitions in
`data/observatory.sqlite3`. The dashboard reads recent stored samples for its
trend chart and displays recent diagnosis changes. The database path and
retention period are configured through `STORAGE_CONFIG` in `config.py`; the
default retention is 30 days. SQLite files under `data/` are ignored by Git.

Actual health samples, including reasons and indicators, are stored separately
from diagnosis transitions and retrieved oldest-first with `get_recent_health`.
Metric samples are also returned oldest-first; diagnosis transitions are returned
newest-first. New timestamps are canonical UTC ISO strings; legacy naive timestamps
are interpreted as UTC. Existing valid offset timestamps remain readable.

Diagnosis rows retain transition time and onset evidence while updating the latest
evidence, contributors, recommendation and `last_seen_at` during an unchanged
state. Severity changes create another transition within the same observed
incident. Explicit recovery closes that incident with `resolved_at` and
`incident_duration_seconds`. This elapsed observation span is distinct from the
diagnosis's metric-window `duration_seconds`; it does not prove uninterrupted
pressure between observations. Repeated recovery samples preserve the final
incident duration. Additive schema updates preserve pre-existing rows, but missing
historical incident details cannot be reconstructed retroactively.
For each condition, only observations newer than its latest accepted observation
may mutate incident state. Late observations and duplicate/conflicting observations
at the same timestamp are ignored for lifecycle updates (first accepted wins).
They do not reopen completed incidents; metric and health storage remain independent.

WAL, bounded lock handling and serialized writes support concurrent dashboard
access. Expected SQLite/filesystem failures show an incomplete-history warning
while live monitoring continues; programming errors remain visible. The default
database is shared by sessions monitoring this application host. It does not yet
identify separate hosts or separate monitoring sessions.
Each dashboard session initializes history once per database/schema identity.
Refreshes check the schema cookie without rerunning migrations; initialization
is retried after failure and repeated for new sessions or changed databases.

## Feature D: Statistical Process Attribution

### Objective

Identify processes whose CPU and memory behavior is statistically associated
with changes in system resource utilization. This feature reports associations,
not proof that a process caused a performance problem.

### Methodology

- Collect timestamped system metrics and all accessible process metrics.
- Retain a rolling history of up to 120 snapshots in Streamlit session state.
- Use Pearson's correlation coefficient for system CPU utilization versus
  process CPU utilization across the full observation window.
- Compare adjacent changes in process RSS with changes in available RAM and
  swap used.
- Identify process instances using PID and creation time when available to
  reduce errors caused by PID reuse.
- Display correlation coefficient, strength, direction, sample count, system
  pressure context, and supporting top-three process overlap.

By default, analysis requires at least 15 valid paired observations. The
dashboard considers CPU at or above 80% to be CPU pressure. Memory pressure
context is low available RAM (20% or less), or RAM utilization of at least 90%
alongside swap utilization of at least 50%. Correlation strength boundaries,
pressure thresholds, and history limits are configurable in
`PROCESS_ATTRIBUTION_CONFIG` in `config.py`; these are project-defined
heuristics, not universal standards.

### Interpretation and limitations

A positive CPU correlation means system CPU and process CPU tend to rise
together. A negative RSS/available-RAM correlation means process memory growth
tends to coincide with falling available RAM. A positive RSS/swap correlation
means process memory growth tends to coincide with rising swap use.

Pearson correlation is undefined when there are too few valid samples or no
variation in either variable. Missing process observations are treated as zero
for CPU series in this baseline, which can be misleading if a process was
inaccessible rather than stopped. Memory changes are compared only when the
same process instance is observed in both adjacent snapshots. Memory RSS,
available RAM, and swap are also related through system memory accounting.
Correlation does not establish causation or user-visible performance impact;
results depend on the observation window, sampling frequency, process
visibility, and metric variation.

## Run tests

```bash
python -m unittest discover -s tests -v
```

Database tests use temporary databases. With the declared runtime dependencies
installed, the suite also runs Streamlit AppTest smoke tests using real collection
and an unavailable-storage case. It checks synthetic pressure/recovery through
the full pipeline and reads persistence from a separate Python process. Tests do
not launch stress workloads.

## Try a controlled CPU workload (Linux)

In a separate terminal, run `yes > /dev/null`, observe the dashboard, and stop
the workload with Ctrl+C. Use one instance only and stop it when finished.
Overall CPU percentage depends on the number of available CPU cores.

## Implementation boundaries

- `collector.py` owns operating-system sampling and returns plain dictionaries.
- `app.py` integrates the collector, health indicator, analyzer, diagnosis,
  recommendations, and storage modules into the dashboard.
- `health.py` and `analyzer.py` evaluate configured pressure indicators;
  `diagnosis.py` explains those findings without claiming confirmed causation.
- `recommendations.py` returns safe informational steps. `storage.py` saves
  samples and diagnosis state changes in SQLite.
- `attribution.py` calculates statistical associations from retained snapshots
  without claiming causation.
- `tests/` covers the collector, health indicator, and assessment logic using
  synthetic metrics where appropriate.

The modules exchange plain dictionaries so they can be reviewed and changed
independently.
