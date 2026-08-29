import inspect
import re
import unittest
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


if __name__ == '__main__':
    unittest.main()
