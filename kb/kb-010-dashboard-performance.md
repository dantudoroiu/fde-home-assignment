# KB-010: Dashboards loading slowly or not at all
First check status.brightdesk.example for ongoing incidents; subscribe there for updates.

If there is no incident:
- Dashboards with many widgets over long date ranges can take up to 30 seconds on first load. Results are
  cached for 15 minutes, so later loads are faster.
- Reduce the date range or add filters; enable "Pre-aggregate" on large datasets under Dataset settings.
- A widget showing "Query timed out" exceeded the 60-second query limit. Split it or pre-aggregate.
- Blank dashboards are often caused by browser extensions (ad blockers). Try a private window.

When reporting a performance problem, include the dashboard URL, the time it happened (with time zone),
your browser, and whether colleagues see the same issue. If many users in your workspace cannot load any
dashboard, mention that explicitly so the ticket is treated as a possible outage.
