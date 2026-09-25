import sys
import unittest

HEALTHY = sys.argv.pop() == "healthy"


class ServiceTests(unittest.TestCase):
    def test_response(self) -> None:
        response = {"status": 200 if HEALTHY else 503}
        self.assertEqual(response["status"], 200)

    def test_serialization(self) -> None:
        import json

        self.assertEqual(json.loads(json.dumps({"id": 7})), {"id": 7})


if __name__ == "__main__":
    unittest.main()
