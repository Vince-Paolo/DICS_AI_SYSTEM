import unittest

from app import app


class RouteMapTestCase(unittest.TestCase):
    def test_each_application_path_and_method_has_one_owner(self):
        route_owners = {}
        for rule in app.url_map.iter_rules():
            methods = rule.methods - {'HEAD', 'OPTIONS'}
            for method in methods:
                key = (rule.rule, method)
                route_owners.setdefault(key, []).append(rule.endpoint)

        duplicates = {
            key: endpoints for key, endpoints in route_owners.items()
            if len(endpoints) > 1
        }
        self.assertEqual(duplicates, {})

    def test_mutating_application_routes_do_not_accept_get(self):
        mutation_paths = {
            '/admin/users/add',
            '/coordinator/resources/allocate',
            '/incident/<int:incident_id>/activate-response',
            '/responder-task/<int:task_id>/update',
            '/responder-task/<int:task_id>/complete',
        }

        for rule in app.url_map.iter_rules():
            if rule.rule in mutation_paths:
                self.assertNotIn('GET', rule.methods, rule.rule)


if __name__ == '__main__':
    unittest.main()