# Permission Matrix

The project currently uses a mix of role names and older aliases. This matrix reflects the intended role model for the September milestone.

| Action | Citizen | Responder | Coordinator | Commander | EOC | Admin |
| --- | --- | --- | --- | --- | --- | --- |
| Submit report | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| View own report | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| View assigned task | — | ✅ | ✅ | ✅ | ✅ | ✅ |
| Manage response | — | — | ✅ | ✅ | ✅ | ✅ |
| Assign tasks | — | — | ✅ | ✅ | ✅ | ✅ |
| Allocate resources | — | — | ✅ | ✅ | ✅ | ✅ |
| Verify incidents | — | — | — | — | ✅ | ✅ |
| Issue alerts | — | — | — | ✅ | ✅ | — |
| Manage facilities | — | — | — | — | ✅ | — |
| View analytics | — | — | ✅ | ✅ | ✅ | ✅ |
| Manage users | — | — | — | — | — | ✅ |

## Notes

- The legacy role names used by the app are mapped to the newer model as follows: `agency_coordinator` → `COORDINATOR`, `incident_commander` → `COMMANDER`, `field_responder` → `RESPONDER`, `eoc_staff` → `EOC`, and `citizen` / `user` → `CITIZEN`.
- The permission helpers in the code should be treated as the source of truth for enforcement.
