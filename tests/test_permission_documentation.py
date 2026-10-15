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
            'DISPATCHER': ('dispatcher', 'dispatch'),
            'IMT': ('imt_staff', 'imt', 'eoc_staff', 'eoc'),
            'RETIRED': ('agency_coordinator', 'coordinator', 'retired'),
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
            'DISPATCHER': SimpleNamespace(id=3, role='dispatcher'),
            'IMT': SimpleNamespace(id=4, role='imt_staff'),
            'ADMIN': SimpleNamespace(id=5, role='admin'),
            'RETIRED': SimpleNamespace(id=6, role='retired'),
        }
        incident = SimpleNamespace(user_id=roles['CITIZEN'].id)
        task_incident = SimpleNamespace(id=99)
        resource = SimpleNamespace(id=100)

        expected = {
            'can_view_incident': {'CITIZEN': True, 'RESPONDER': True, 'DISPATCHER': True, 'IMT': True, 'ADMIN': True, 'RETIRED': False},
            'can_edit_incident': {'CITIZEN': True, 'RESPONDER': False, 'DISPATCHER': True, 'IMT': True, 'ADMIN': True, 'RETIRED': False},
            'can_assign_task': {'CITIZEN': False, 'RESPONDER': False, 'DISPATCHER': True, 'IMT': False, 'ADMIN': False, 'RETIRED': False},
            'can_allocate_resource': {'CITIZEN': False, 'RESPONDER': False, 'DISPATCHER': True, 'IMT': False, 'ADMIN': False, 'RETIRED': False},
            'can_verify_incident': {'CITIZEN': False, 'RESPONDER': False, 'DISPATCHER': False, 'IMT': True, 'ADMIN': True, 'RETIRED': False},
            'can_issue_alert': {'CITIZEN': False, 'RESPONDER': False, 'DISPATCHER': False, 'IMT': True, 'ADMIN': False, 'RETIRED': False},
            'can_manage_users': {'CITIZEN': False, 'RESPONDER': False, 'DISPATCHER': False, 'IMT': False, 'ADMIN': True, 'RETIRED': False},
            'can_view_analytics': {'CITIZEN': False, 'RESPONDER': False, 'DISPATCHER': True, 'IMT': True, 'ADMIN': True, 'RETIRED': False},
            'can_manage_facilities': {'CITIZEN': False, 'RESPONDER': False, 'DISPATCHER': False, 'IMT': True, 'ADMIN': False, 'RETIRED': False},
            'can_manage_evacuation_centers': {'CITIZEN': False, 'RESPONDER': False, 'DISPATCHER': False, 'IMT': True, 'ADMIN': False, 'RETIRED': False},
            'can_log_incident_report': {'CITIZEN': False, 'RESPONDER': False, 'DISPATCHER': False, 'IMT': True, 'ADMIN': True, 'RETIRED': False},
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
