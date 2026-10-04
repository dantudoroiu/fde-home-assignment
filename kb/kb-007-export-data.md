# KB-007: Exporting data to CSV and Excel
Any dashboard widget can be exported: open the widget menu (three dots) and choose "Export CSV" or
"Export XLSX". Exports respect the dashboard's current filters and date range.

Limits: interactive exports are capped at 100,000 rows. For larger datasets use a scheduled export
(Settings > Exports), which delivers files of any size to email, S3, or Google Cloud Storage, or use the
Data API (see KB-008).

Common issues:
- Numbers show as dates in Excel: use XLSX export instead of CSV, or import the CSV with column types set
  to text.
- Special characters look garbled: CSV files are UTF-8; in Excel use Data > From Text/CSV and choose UTF-8.
- "Export failed": usually the 100,000-row limit. Narrow the date range or use a scheduled export.
