import importlib.util
import os
import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "extract_keys_windows.py"


def load_module():
    spec = importlib.util.spec_from_file_location("extract_keys_windows", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ExtractKeysWindowsTests(unittest.TestCase):
    def test_script_hooks_sqlcipher_provider_kdf_abi(self):
        module = load_module()
        script = module.build_frida_script(0x1234)
        self.assertIn("module.base.add(KDF_RVA)", script)
        self.assertIn("this.salt = args[4]", script)
        self.assertIn("this.rounds = args[6].toInt32()", script)
        self.assertIn("this.derivedKey = args[8]", script)

    @unittest.skipUnless(sys.platform == "win32", "Windows PE fixture is the installed Weixin.dll")
    def test_resolver_finds_installed_weixin_kdf(self):
        module = load_module()
        dll = module.find_weixin_dll()
        if not dll:
            self.skipTest("Weixin.dll is not installed")
        rva = module.resolve_sqlcipher_kdf_rva(dll)
        self.assertGreater(rva, 0)
        self.assertLess(rva, os.path.getsize(dll))


if __name__ == "__main__":
    unittest.main()
