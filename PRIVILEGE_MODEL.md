# DICS-AI Privilege Model

This document defines who is allowed to do what, and replaces the current
ad-hoc checks (`is_admin_or_coordinator()` etc.) with a model that matches
the real-world Incident Command System (ICS) roles the app is built around.
It is the reference to code against — if a route's behavior doesn't match
this doc, either the route or the doc is wrong and should be fixed.

## 1. Roles and their real-world job

| Role | Real-world analogue | Scope |
|---|---|---|
| `citizen` | Member of the public | Only their own reports/alerts |
| `field_responder` | Boots-on-the-ground crew (fire/medical/rescue) | Only tasks assigned to their own agency |
| `agency_coordinator` | Desk lead for one responding agency (e.g. BFP, Red Cross) | Their own agency's tasks/resources/reports; read-only view of the wider response they're part of |
| `incident_commander` | ICS Incident Commander | Full control of the incidents/responses assigned to them |
| `eoc_staff` | Emergency Operations Center watch officer | Read-only, org-wide situational awareness across *all* agencies and incidents |
| `admin` | System administrator | User accounts, system configuration, backups — plus emergency override of everything else |

The key design principle: **operational authority (running a response) and
system administration (managing accounts) are different jobs and must not
share a permission check.** Today `is_admin_or_coordinator()` conflates
them — that's the root problem.

## 2. Capability matrix

Legend: **Y** = full write access · **R** = read-only · **–** = no access

| Capability | citizen | field_responder | agency_coordinator | incident_commander | eoc_staff | admin |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| File a citizen incident report | Y | – | – | – | – | – |
| View own reports/alerts | Y | – | – | – | – | – |
| View/update tasks assigned to their own agency | – | Y | Y | – | – | – |
| Submit field reports | – | Y | – | – | – | – |
| Manage their own agency's resources (request/update) | – | – | Y | – | – | – |
| Submit agency situation reports | – | – | Y | – | – | – |
| View all agencies' tasks/resources within a response they're part of | – | – | R | Y | R | R |
| Activate an incident response (open `IncidentResponse`) | – | – | – | Y | – | Y |
| Assign tasks to agencies / allocate resources for a response | – | – | – | Y | – | Y |
| Approve/compile situation reports & timeline for a response | – | – | – | Y | – | – |
| Close a response / post-incident evaluation | – | – | – | Y | – | Y |
| Cross-agency, org-wide monitoring dashboard (all incidents, all resources, analytics) | – | – | – | – | R | R |
| Verify a citizen report / toggle public alert flag | – | – | – | Y (own incidents) | Y | Y |
| Manage user accounts (create/edit/disable/change role) | – | – | – | – | – | Y |
| Export DB backups | – | – | – | – | – | Y |

Notes on the two contested rows:

- **Verify/toggle alerts** — this is a public-safety broadcast action, not
  routine agency coordination, so it stays out of the coordinator's hands.
  It belongs to whoever is watching the whole picture: EOC staff (their
  literal job), the commander for incidents in their own response, and
  admin as a fallback/override.
- **Cross-agency read access** — coordinators need to *see* what other
  agencies are doing on a shared response to coordinate effectively, but
  should not be able to edit another agency's tasks or resources. EOC gets
  the same read-only visibility, but across the entire org rather than one
  response.

## 3. Replacement permission helpers (`blueprints/common.py`)

Replace the current flat checks with ones that separate **role identity**
from **resource ownership**, since several capabilities above depend on
*which* incident/response/agency is involved, not just the role name.

```python
from flask import session
from models import User

# ---- role identity checks -------------------------------------------------

def current_user():
    if 'username' not in session:
        return None
    return User.query.filter_by(username=session['username']).first()

def has_role(*roles):
    return 'username' in session and session.get('role') in roles

def is_admin():
    return has_role('admin')

def is_incident_commander():
    return has_role('incident_commander')

def is_agency_coordinator():
    return has_role('agency_coordinator')

def is_field_responder():
    return has_role('field_responder')

def is_eoc_staff():
    return has_role('eoc_staff')

# ---- capability checks (what the matrix above encodes) --------------------

def can_manage_users():
    return is_admin()

def can_export_backups():
    return is_admin()

def can_verify_or_alert(incident=None):
    """Verify citizen reports / toggle public alert flag."""
    if has_role('admin', 'eoc_staff'):
        return True
    if is_incident_commander() and incident is not None:
        user = current_user()
        return incident.response is not None and incident.response.commander_id == user.id
    return False

def can_manage_response(response):
    """Activate/close a response, assign tasks, allocate resources."""
    if is_admin():
        return True
    user = current_user()
    return is_incident_commander() and user and response.commander_id == user.id

def can_view_response(response):
    """Read-only cross-agency visibility into one response."""
    if has_role('admin', 'eoc_staff'):
        return True
    if can_manage_response(response):
        return True
    user = current_user()
    if is_agency_coordinator() and user:
        # coordinator is part of this response if their agency has a task/resource on it
        return any(t.assigned_to_agency == user.agency for t in response.tasks) or \
               any(r.agency == user.agency for r in response.resources)
    return False

def can_manage_agency_data(agency):
    """Coordinator/field responder editing their own agency's tasks/resources/reports."""
    user = current_user()
    return bool(user) and has_role('agency_coordinator', 'field_responder') and user.agency == agency

def can_view_org_wide():
    """EOC dashboard, analytics, cross-incident monitoring."""
    return has_role('admin', 'eoc_staff', 'incident_commander')
```

`get_coordinator_agency()` stays as-is.

## 4. Route → required-permission mapping

| Route | Today | Should require |
|---|---|---|
| `/admin/users*` | `is_admin_or_coordinator()` | `can_manage_users()` |
| `/admin/backup` | `role == 'admin'` (already correct) | `can_export_backups()` |
| `/admin/alerts`, `/admin/alerts/<id>/toggle`, `/admin/incidents/<id>/verify` | `is_admin_or_coordinator()` | `can_verify_or_alert(incident)` |
| `/coordinator/*` (tasks, team, resources, reports, comms) | `is_admin_or_coordinator()` | `can_manage_agency_data(user.agency)`, plus `can_view_response()` for cross-agency reads |
| `/incident-commander-dashboard`, `/incident/<id>/activate-response`, `/incident-response/<id>/*` (assign-task, allocate-resource, close, create-report, update-task, update-resource) | `is_incident_commander()` (role in `['incident_commander','admin']`) | `can_manage_response(response)` |
| `/incident-response/<id>` (overview), `/tasks`, `/resources`, `/reports`, `/timeline` (view pages) | same as above | `can_view_response(response)` — allow read for coordinators/EOC too |
| `/eoc-dashboard`, `/eoc/incidents`, `/eoc/resources`, `/analytics` | `'username' in session` only (no role check) | `can_view_org_wide()` |
| `/hazard-map`, `/ics`, `/protocols` | `'username' in session` only | any authenticated role (genuinely shared reference info) — keep as-is, but confirm intentionally |
| `/responder-*` | `role == 'field_responder'` (already correct) | unchanged |
| `/citizen-*`, `/incidents`, `/alerts` | `'username' in session`, filtered by `user_id` | unchanged, but see §5 — stop reusing these for other roles |

## 5. Fallout from this change (what else needs to move)

- **Sidebar dead links** (`/alerts`, `/incidents` shown to admin/coordinator/commander/EOC) get fixed as a side effect: those roles should link to `/admin/alerts` (verify/alert feed) or `/eoc/incidents` (org-wide monitoring) instead of the citizen-scoped routes.
- **`create_situation_report`'s broken coordinator branch** goes away naturally: coordinators no longer appear in the allowed-role check at all, since situation-report *approval/compilation* is commander work — coordinators submit their own agency reports (`/coordinator/reports/submit`), which is a separate, already-correct route.
- **Admin's emergency-override capability** (`can_manage_users`, `can_verify_or_alert`, `can_manage_response` all return `True` for `is_admin()`) needs to be a conscious, documented decision, not an accident — admin is the only role allowed to reach into operational territory it doesn't "own," for support/incident-recovery purposes.

## 6. Open decisions before implementation

These aren't code questions — they're policy questions the doc author (you)
should confirm before I refactor the routes to match:

1. Should `incident_commander` be allowed to verify/toggle alerts only for
   incidents inside their own active response, or should any commander be
   able to verify any unverified citizen report (first-come basis)?
2. Should `agency_coordinator` read access to a response be automatic
   (any coordinator can view any response their agency has a task on), or
   should it require the commander to explicitly add them?
3. Is admin's blanket override intentional for all four operational
   capabilities, or should some (e.g. `can_manage_response`) require admin
   to explicitly "take over" a response rather than silently qualifying?
