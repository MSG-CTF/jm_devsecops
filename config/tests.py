from django.test import SimpleTestCase


class RootEndpointTest(SimpleTestCase):
    """예제 프로젝트의 기본 응답이 깨지지 않았는지 확인한다."""

    def test_root_returns_ok(self):
        # secure=True로 보내야 한다. 테스트는 DEBUG=False로 돌고,
        # 그때 SECURE_SSL_REDIRECT가 켜져 http 요청은 301로 튕긴다 (운영과 동일한 동작).
        res = self.client.get("/", secure=True)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"status": "ok"})
