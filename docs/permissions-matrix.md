# Permission Matrix

This is the single human-readable permission reference for DICS-AI. The
executable policy lives in `services/permissions.py`; route-level ownership
and assignment checks are part of the route contract and must not be inferred
from role alone.

## Role Names

| Canonical role | Stored aliases |
| --- | --- |
| `CITIZEN` | `citizen`, `user` |
| `RESPONDER` | `field_responder`, `responder` |
| `DISPATCHER` | `dispatcher`, `dispatch` |
| `IMT` | `imt_staff`, `imt`, `eoc_staff`, `eoc` |
| `RETIRED` | `retired`, `agency_coordinator`, `coordinator` (disabled legacy accounts) |
| `ADMIN` | `admin` |

## Capability Matrix

`Y` means the role passes the capability helper. `-` means it does not. A
`Y` result never bypasses object ownership or assignment checks enforced by a
route.

| Permission helper | Citizen | Responder | Dispatcher | IMT | Admin |
| --- | :-: | :-: | :-: | :-: | :-: |
| `can_view_incident` | Y* | Y | Y | Y | Y |
| `can_edit_incident` | Y* | - | Y | Y | Y |
| `can_assign_task` | - | - | Y | - | - |
| `can_allocate_resource` | - | - | Y | - | - |
| `can_verify_incident` | - | - | - | Y | Y |
| `can_issue_alert` | - | - | - | Y | - |
| `can_manage_users` | - | - | - | - | Y |
| `can_view_analytics` | - | - | Y | Y | Y |
| `can_manage_facilities` | - | - | - | Y | - |
| `can_manage_evacuation_centers` | - | - | - | Y | - |
| `can_log_incident_report` | - | - | - | Y | Y |

\* Citizen incident viewing/editing is limited to incidents owned by that
citizen. All helpers return `False` for a missing user or required object.

## Route Rules

- Dispatcher routes own task assignment and operational resource allocation.
  Assignments target an active field responder or a team containing active
  field responders.
- Response team creation and roster editing are available to both Dispatchers
  and IMT staff. IMT may organize CDRRMO field responders but cannot dispatch
  tasks or allocate operational resources.
- Responder task updates are scoped to directly assigned tasks, team
  assignments.
- IMT response pages provide system-wide oversight; task assignment and
  resource allocation remain Dispatcher-only.
- Shared reference pages may be available to all authenticated roles; this
	does not grant operational write access.

## Consistency

Do not add a second permission matrix. Update `services/permissions.py` first,
then update this document and run the permission consistency test. The
superseded design notes in `PRIVILEGE_MODEL.md` are retained only as a pointer
to this document.
