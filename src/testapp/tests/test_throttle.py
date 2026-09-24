from django.test import Client, TestCase


class ThrottleTests(TestCase):
    def setUp(self) -> None:
        # Sort JSON keys for deterministic comparisons.
        self.client = Client(headers={"x-json-sorted": "true"})

    def test_throttle(self):
        for _ in range(5):
            r = self.client.get("/api/v1/throttled/")
            self.assertEqual(r.status_code, 200)
        r = self.client.get("/api/v1/throttled/")
        self.assertEqual(r.status_code, 429)
