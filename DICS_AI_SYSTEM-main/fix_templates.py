from pathlib import Path

root = Path(__file__).resolve().parent
app_path = root / 'app.py'

# Update app.py template references
app_text = app_path.read_text(encoding='utf-8')
replacements = {
    "render_template('login.html'": "render_template('pages/login.html'",
    "render_template('register.html'": "render_template('pages/register.html'",
    "render_template('analytics.html'": "render_template('pages/analytics.html'",
    "render_template('hazard_map.html'": "render_template('pages/hazard_map.html'",
    "render_template('ics.html'": "render_template('pages/ics.html'",
    "render_template('protocols.html'": "render_template('pages/protocols.html'",
    "render_template('citizen_report.html'": "render_template('pages/citizen_report.html'",
    "render_template('citizen_alerts.html'": "render_template('pages/citizen_alerts.html'",
    "render_template('citizen_status.html'": "render_template('pages/citizen_status.html'",
    "render_template('citizen_resources.html'": "render_template('pages/citizen_resources.html'",
    "render_template('incidents.html'": "render_template('pages/incidents.html'",
    "render_template('alerts.html'": "render_template('pages/alerts.html'",
    "render_template('admin_alerts.html'": "render_template('pages/admin_alerts.html'",
    "render_template('ai_prediction.html'": "render_template('pages/ai_prediction.html'",
    "render_template('user_management.html'": "render_template('pages/user_management.html'",
    "render_template('incident_commander_dashboard.html'": "render_template('pages/incident_commander_dashboard.html'",
    "render_template('incident_response_detail.html'": "render_template('pages/incident_response_detail.html'",
    "render_template('assign_task.html'": "render_template('pages/assign_task.html'",
    "render_template('allocate_resource.html'": "render_template('pages/allocate_resource.html'",
    "render_template('create_situation_report.html'": "render_template('pages/create_situation_report.html'",
    "'dashboard.html'": "'pages/dashboard.html'",
}
for old, new in replacements.items():
    app_text = app_text.replace(old, new)
app_path.write_text(app_text, encoding='utf-8')

# Update template inheritance in all moved page templates
for page_file in (root / 'templates' / 'pages').glob('*.html'):
    content = page_file.read_text(encoding='utf-8')
    content = content.replace("{% extends 'base.html' %}", "{% extends 'pages/base.html' %}")
    content = content.replace("{% extends 'base.html' %}", "{% extends 'pages/base.html' %}")
    page_file.write_text(content, encoding='utf-8')

print('Template rerouting complete.')
