import os
import unittest

os.environ.setdefault('SECRET_KEY', os.environ.get('SECRET_KEY') or 'development-secret')

from tests.test_responder_routes import ResponderRoutesTestCase

suite = unittest.defaultTestLoader.loadTestsFromTestCase(ResponderRoutesTestCase)
result = unittest.TextTestRunner(verbosity=1).run(suite)
if not result.wasSuccessful():
    raise SystemExit(1)
