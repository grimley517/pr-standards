
## 5. Logging and alerting (deployable service)
- New failure paths need a structured log at the right level, with correlation/request ID, and without secrets or PII.
- New external dependencies, jobs or endpoints need metrics/health signals and an alert (in the repo's alerts/monitoring config) for failure or saturation, plus a runbook line in docs/observability.md.
- Flag swallowed exceptions, `print`/`console.log` used as logging, and alerts that page without an action to take.
