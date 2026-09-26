import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from runtime import chat_profiles as p

class ProfileTests(unittest.TestCase):
    def test_families_default_context(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(p.bridge.DRIVE_CACHE,'root',Path(temp)):
            self.assertEqual(p.load_profile('Qwen','a')['context'],4096)
            for name in ['DeepSeek','Qwen Coder','Creative Writing','Webnovel Writer']:
                self.assertEqual(p.load_profile(name,'a')['context'],8192)
    def test_separate_and_persistent(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(p.bridge.DRIVE_CACHE,'root',Path(temp)):
            p.save_profile('A','a',{'context':2048});p.save_profile('A','b',{'context':8192})
            self.assertEqual(p.load_profile('A','a')['context'],2048)
            self.assertEqual(p.load_profile('A','b')['context'],8192)
    def test_path_identity_is_hashed(self):self.assertEqual(len(p.profile_path('..','../unsafe').stem),64)
    def test_fractional_context_rejected(self):
        with self.assertRaises(ValueError):p.normalize_profile({'context':1024.5})
    def test_bool_thread_rejected(self):
        with self.assertRaises(ValueError):p.normalize_profile({'threads':True})
    def test_excessive_context_rejected(self):
        with self.assertRaises(ValueError):p.normalize_profile({'context':999999999})
    def test_unknown_mode_rejected(self):
        with self.assertRaises(ValueError):p.normalize_profile({'load_mode':'run arbitrary shell'})
    def test_new_load_flag(self):
        args=p.command('server',Path('m.gguf'),{},'--load-mode --ctx-size')
        self.assertIn('--load-mode',args);self.assertIn('4096',args)
    def test_legacy_no_mmap(self):self.assertIn('--no-mmap',p.command('server',Path('m.gguf'),{},'--no-mmap'))
    def test_unsupported_dio_refused(self):
        with self.assertRaises(RuntimeError):p.command('server',Path('m.gguf'),{'load_mode':'dio'},'--no-mmap')

if __name__=='__main__':unittest.main()
