import tempfile
import unittest
from pathlib import Path
from runtime.instance_lock import instance_lock
from runtime.activation_runtime import ActivationRuntime
from runtime.activation_store import digest
class InstanceTests(unittest.TestCase):
    def test_second_owner_is_blocked(self):
        with tempfile.TemporaryDirectory() as root:
            with instance_lock(root):
                with self.assertRaises(RuntimeError):
                    with instance_lock(root):pass
    def test_lock_reusable_after_clean_exit(self):
        with tempfile.TemporaryDirectory() as root:
            with instance_lock(root):pass
            with instance_lock(root):pass
    def test_exception_releases_owner(self):
        with tempfile.TemporaryDirectory() as root:
            try:
                with instance_lock(root):raise ValueError('test')
            except ValueError:pass
            with instance_lock(root):pass
    def test_importing_composition_does_not_mark_other_job_interrupted(self):
        with tempfile.TemporaryDirectory() as root:
            r=ActivationRuntime(root,None)
            job=r.store.create(digest({'model':1}),'Model','chat',{'items':[{}]})
            r.store.claim(job['id'],job['identity'])
            other=ActivationRuntime(root,None,recover=False)
            self.assertEqual(other.store.get(job['id'])['state'],'preparing')
