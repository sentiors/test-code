from app.utils_grafana import check_grafana_health, check_dashboard_folder, check_datasource

print("=== Grafana Health ===")
ok, msg = check_grafana_health()
print(f"Status: {'OK' if ok else 'FAIL'} - {msg}")

print("\n=== Alert Folder ===")
ok, msg = check_dashboard_folder("bf7uaql35dloge")
print(f"Folder: {'OK' if ok else 'FAIL'} - {msg}")

print("\n=== Infinity Datasource ===")
ok, msg = check_datasource("infinity grading API")
print(f"DS: {'OK' if ok else 'FAIL'} - {msg}")
