from odoo.tests.common import TransactionCase


class TestPerfectMatchScaffold(TransactionCase):
    def test_module_loads(self):
        model = self.env["account.reconcile.model"]
        self.assertIn("perfect_match_enabled", model._fields)
        self.assertIn("perfect_match_tolerance", model._fields)
        self.assertIn("perfect_match_use_date", model._fields)
