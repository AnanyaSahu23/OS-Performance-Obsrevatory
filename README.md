# OS Performance Observatory

The current implementation is the live monitoring slice: a `psutil` collector
and a Streamlit dashboard. It reports resource metrics and top processes; it
does not yet detect anomalies, diagnose bottlenecks, or recommend actions.

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
- `app.py` renders the dashboard and keeps short-lived chart history in the
  Streamlit session; no database persistence or diagnosis logic is present.
- `tests/` exercises collector output and process ranking without depending on
  live machine metrics.

This separation gives future analyzer, recommendation, and storage modules a
stable snapshot interface to integrate with.
