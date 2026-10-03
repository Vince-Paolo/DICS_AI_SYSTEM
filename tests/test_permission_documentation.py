import inspect
import re
import unittest
from types import SimpleNamespace
from pathlib import Path

from services import permissions


class PermissionDocumentationTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.matrix_path = Path(__file__).parents[1] / 'docs' / 'permissions-matrix.md'
        cls.matrix = cls.matrix_path.read_text(encoding='utf-8')

    def test_every_capability_helper_is_documented(self):
        documented_helpers = set(re.findall(r'\| `(can_[a-z_]+)` \|', self.matrix))
        executable_helpers = {
            name for name, value in inspect.getmembers(permissions, inspect.isfunction)
            if name.startswith('can_')
        }

        self.assertEqual(documented_helpers, executable_helpers)

    def test_documented_role_aliases_match_executable_normalization(self):
        expected_aliases = {
            'CITIZEN': ('citizen', 'user'),
            'RESPONDER': ('field_responder', 'responder'),
            'COORDINATOR': ('agency_coordinator', 'coordinator'),
            'COMMANDER': ('incident_commander', 'commander'),
            'EOC': ('eoc_staff', 'eoc'),
            'ADMIN': ('admin',),
        }

        for canonical_role, aliases in expected_aliases.items():
            self.assertIn(f'| `{canonical_role}` |', self.matrix)
            for alias in aliases:
                self.assertEqual(permissions.normalize_role(alias), canonical_role)
                self.assertIn(alias, self.matrix)

    def test_every_canonical_role_is_exercised_against_every_permission_helper(self):
        roles = {
            'CITIZEN': SimpleNamespace(id=1, role='citizen'),
            'RESPONDER': SimpleNamespace(id=2, role='field_responder'),
            'COORDINATOR': SimpleNamespace(id=3, role='agency_coordinator'),
            'COMMANDER': SimpleNamespace(id=4, role='incident_commander'),
            'EOC': SimpleNamespace(id=5, role='eoc_staff'),
            'ADMIN': SimpleNamespace(id=6, role='admin'),
        }
        incident = SimpleNamespace(user_id=roles['CITIZEN'].id)
        task_incident = SimpleNamespace(id=99)
        resource = SimpleNamespace(id=100)

        expected = {
            'can_view_incident': {'CITIZEN': True, 'RESPONDER': True, 'COORDINATOR': True, 'COMMANDER': True, 'EOC': True, 'ADMIN': True},
            'can_edit_incident': {'CITIZEN': True, 'RESPONDER': False, 'COORDINATOR': True, 'COMMANDER': True, 'EOC': True, 'ADMIN': True},
            'can_assign_task': {'CITIZEN': False, 'RESPONDER': False, 'COORDINATOR': True, 'COMMANDER': True, 'EOC': True, 'ADMIN': True},
            'can_allocate_resource': {'CITIZEN': False, 'RESPONDER': False, 'COORDINATOR': True, 'COMMANDER': True, 'EOC': True, 'ADMIN': True},
            'can_verify_incident': {'CITIZEN': False, 'RESPONDER': False, 'COORDINATOR': False, 'COMMANDER': False, 'EOC': True, 'ADMIN': True},
            'can_issue_alert': {'CITIZEN': False, 'RESPONDER': False, 'COORDINATOR': False, 'COMMANDER': True, 'EOC': True, 'ADMIN': False},
            'can_manage_users': {'CITIZEN': False, 'RESPONDER': False, 'COORDINATOR': False, 'COMMANDER': False, 'EOC': False, 'ADMIN': True},
            'can_view_analytics': {'CITIZEN': False, 'RESPONDER': False, 'COORDINATOR': True, 'COMMANDER': True, 'EOC': True, 'ADMIN': True},
            'can_manage_facilities': {'CITIZEN': False, 'RESPONDER': False, 'COORDINATOR': False, 'COMMANDER': False, 'EOC': True, 'ADMIN': False},
            'can_manage_evacuation_centers': {'CITIZEN': False, 'RESPONDER': False, 'COORDINATOR': True, 'COMMANDER': False, 'EOC': True, 'ADMIN': False},
            'can_request_resources': {'CITIZEN': False, 'RESPONDER': False, 'COORDINATOR': True, 'COMMANDER': False, 'EOC': False, 'ADMIN': False},
            'can_decide_resource_request': {'CITIZEN': False, 'RESPONDER': False, 'COORDINATOR': False, 'COMMANDER': True, 'EOC': True, 'ADMIN': True},
            'can_log_incident_report': {'CITIZEN': False, 'RESPONDER': False, 'COORDINATOR': False, 'COMMANDER': False, 'EOC': True, 'ADMIN': True},
        }

        for helper_name, role_expectations in expected.items():
            helper = getattr(permissions, helper_name)
            for role_name, user in roles.items():
                with self.subTest(helper=helper_name, role=role_name):
                    if helper_name in {'can_view_incident', 'can_edit_incident'}:
                        actual = helper(user, incident)
                    elif helper_name == 'can_assign_task':
                        actual = helper(user, task_incident)
                    elif helper_name == 'can_allocate_resource':
                        actual = helper(user, resource)
                    else:
                        actual = helper(user)
                    self.assertEqual(actual, role_expectations[role_name])

        outsider = SimpleNamespace(id=999, role='citizen')
        self.assertFalse(permissions.can_view_incident(outsider, incident))
        self.assertFalse(permissions.can_edit_incident(outsider, incident))


if __name__ == '__main__':
    unittest.main()
