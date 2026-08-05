from django.test import SimpleTestCase


class HealthCheckTest(SimpleTestCase):
    """Cloud Run이 살아있다고 판단하는 근거가 되는 엔드포인트라 반드시 지켜야 한다."""

    def test_health_returns_ok(self):
        # secure=True로 보내야 한다. 테스트는 DEBUG=False로 돌고,
        # 그때 SECURE_SSL_REDIRECT가 켜져 http 요청은 301로 튕긴다 (운영과 동일한 동작).
        res = self.client.get("/", secure=True)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"status": "ok"})
