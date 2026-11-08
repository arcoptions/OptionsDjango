from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from .performance import REQUEST_QUERY_BUDGETS


class RequestQueryBudgetTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username="performance-operator",
            password="test-password",
            is_staff=True,
        )
        self.client.force_login(user)

    def test_critical_operator_pages_stay_within_query_budgets(self):
        for route_name, budget in REQUEST_QUERY_BUDGETS.items():
            with self.subTest(route_name=route_name, budget=budget):
                with CaptureQueriesContext(connection) as queries:
                    response = self.client.get(reverse(route_name))

                self.assertEqual(response.status_code, 200)
                self.assertLessEqual(
                    len(queries),
                    budget,
                    f"{route_name} used {len(queries)} queries; budget is {budget}",
                )
