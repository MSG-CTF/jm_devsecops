from django.test import SimpleTestCase


class HealthCheckTest(SimpleTestCase):
    def test_healthz_returns_ok(self):
        response = self.client.get(
            "/healthz",
            secure=True,
            HTTP_HOST="example.com",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
