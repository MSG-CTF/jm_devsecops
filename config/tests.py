from django.test import SimpleTestCase


class HealthCheckTest(SimpleTestCase):
    """Cloud Run이 살아있다고 판단하는 근거가 되는 엔드포인트라 반드시 지켜야 한다."""

    def test_health_returns_ok(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"status": "ok"})
