import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from dotenv import dotenv_values
from configure_key import configure, discover

class DiscoveryTests(unittest.TestCase):
    def test_desktop_key_is_saved_without_logging(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'NVIDIA_API_KEY': ''}):
            root = Path(tmp)
            key = 'nvapi-' + 'X' * 32
            (root / 'anahtar.txt').write_text(key)
            target = root / '.env'
            target.write_text('NVIDIA_API_KEY=\n')
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertTrue(configure(target, [root]))
            self.assertEqual(dotenv_values(target, encoding='utf-8-sig')['NVIDIA_API_KEY'], key)
            self.assertNotIn(key, output.getvalue())

    def test_code_is_parsed_without_execution_and_other_keys_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            key = 'nvapi-' + 'A' * 32
            (root / 'main.py').write_text("raise RuntimeError('must not run')\nNVIDIA_API_KEY = " + repr(key))
            (root / 'binance_key.txt').write_text('unrelated-exchange-secret')
            self.assertEqual(discover([root]), {key})

    def test_ambiguous_keys_do_not_modify_configuration(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'NVIDIA_API_KEY': ''}):
            root = Path(tmp)
            (root / 'key.txt').write_text('nvapi-' + 'A' * 32 + '\nnvapi-' + 'B' * 32)
            target = root / '.env'
            target.write_text('NVIDIA_API_KEY=\n')
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertFalse(configure(target, [root]))
            self.assertEqual(target.read_text(), 'NVIDIA_API_KEY=\n')

if __name__ == '__main__':
    unittest.main()
