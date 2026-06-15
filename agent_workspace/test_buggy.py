
import unittest
from buggy import add

class TestAdd(unittest.TestCase):
    def test_positive(self):
        self.assertEqual(add(2, 3), 5)  # expects 5 but gets -1

if __name__ == "__main__":
    unittest.main()
