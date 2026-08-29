# Permission Matrix

This is the single human-readable permission reference for DICS-AI. The
executable policy lives in `services/permissions.py`; route-level ownership
checks (for example, a coordinator may update only their agency's resources)
are part of the route contract and must not be inferred from role alone.

## Role Names

| Canonical role | Stored aliases |
| --- | --- |
| `CITIZEN` | `citizen`, `user` |
| `RESPONDER` | `field_responder`, `responder` |
| `COORDINATOR` | `agency_coordinator`, `coordinator` |
| `COMMANDER` | `incident_commander`, `commander` |
| `EOC` | `eoc_staff`, `eoc` |
| `ADMIN` | `admin` |

## Capability Matrix

`Y` means the role passes the capability helper. `-` means it does not. A
`Y` result never bypasses object ownership or assignment checks enforced by a
route.

| Permission helper | Citizen | Responder | Coordinator | Commander | EOC | Admin |
| --- | :-: | :-: | :-: | :-: | :-: | :-: |
| `can_view_incident` | Y* | Y | Y | Y | Y | Y |
| `can_edit_incident` | Y* | - | Y | Y | Y | Y |
| `can_assign_task` | - | - | - | Y | Y | Y |
| `can_allocate_resource` | - | - | - | Y | Y | Y |
| `can_verify_incident` | - | - | - | - | Y | Y |
| `can_issue_alert` | - | - | - | Y | Y | - |
| `can_manage_users` | - | - | - | - | - | Y |
| `can_view_analytics` | - | - | - | Y | Y | Y |
| `can_manage_facilities` | - | - | - | - | Y | - |
| `can_manage_evacuation_centers` | - | - | Y | - | Y | - |
| `can_request_resources` | - | - | Y | - | - | - |
| `can_decide_resource_request` | - | - | - | Y | Y | Y |
| `can_log_incident_report` | - | - | - | - | Y | Y |

\* Citizen incident viewing/editing is limited to incidents owned by that
citizen. All helpers return `False` for a missing user or required object.

## Route Rules

- Responder routes require the `RESPONDER` role and scope task updates to the
	responder's agency.
- Coordinator task, resource, and report workflows scope writes to the
	coordinator's agency or participating responses.
- Commander response mutations require the commander assigned to that
	response. EOC and admin access is governed by the specific route policy.
- Shared reference pages may be available to all authenticated roles; this
	does not grant operational write access.

## Consistency

Do not add a second permission matrix. Update `services/permissions.py` first,
then update this document and run the permission consistency test. The
superseded design notes in `PRIVILEGE_MODEL.md` are retained only as a pointer
to this document.
