import unittest
from pathlib import Path

from tools.check_docs import check_repository


class DocumentationTests(unittest.TestCase):
    def test_documentation_is_portable_and_linked(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual([], check_repository(root))


if __name__ == "__main__":
    unittest.main()

