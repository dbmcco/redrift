import unittest


class TestImports(unittest.TestCase):
    def test_imports(self) -> None:
        import redrift.cli  # noqa: F401


if __name__ == "__main__":
    unittest.main()
