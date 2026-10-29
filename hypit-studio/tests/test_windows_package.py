"""Installer safety checks that do not execute Windows binaries on Linux."""
from pathlib import Path
import json
import tempfile
import unittest
import zipfile
from unittest.mock import patch
import windows

class WindowsPackage(unittest.TestCase):
 def test_extracts_only_known_media_tools(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);archive=root/'tools.zip'
   with zipfile.ZipFile(archive,'w') as z:
    z.writestr('build/bin/ffmpeg.exe',b'MZffmpeg')
    z.writestr('build/bin/ffprobe.exe',b'MZffprobe')
    z.writestr('build/bin/ffplay.exe',b'MZunused')
    z.writestr('build/LICENSE',b'Build license')
   windows.extract_ffmpeg(archive,root/'out')
   self.assertEqual((root/'out/bin/ffmpeg.exe').read_bytes(),b'MZffmpeg')
   self.assertTrue((root/'out/LICENSE').is_file())
   self.assertFalse((root/'out/bin/ffplay.exe').exists())

 def test_refuses_incomplete_or_unsafe_archive(self):
  for entries in [{'build/bin/ffmpeg.exe':b'MZ'},{'../outside.exe':b'MZ','build/bin/ffmpeg.exe':b'MZ','build/bin/ffprobe.exe':b'MZ'}]:
   with tempfile.TemporaryDirectory() as folder:
    root=Path(folder);archive=root/'tools.zip'
    with zipfile.ZipFile(archive,'w') as z:
     for name,data in entries.items():z.writestr(name,data)
    with self.assertRaises(RuntimeError):windows.extract_ffmpeg(archive,root/'out')
    self.assertFalse((root/'outside.exe').exists())

 def test_wheel_checksums_detect_corruption(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);wheels=root/'vendor/wheels';wheels.mkdir(parents=True)
   wheel=wheels/'test.whl';wheel.write_bytes(b'verified package')
   (root/'vendor/wheels-sha256.json').write_text(json.dumps({'test.whl':windows.digest(wheel)}))
   with patch.object(windows,'ROOT',root),patch.object(windows,'WHEELS',wheels):
    windows.check_wheels()
    wheel.write_bytes(b'changed')
    with self.assertRaisesRegex(RuntimeError,'checksum failed'):windows.check_wheels()

if __name__=='__main__':unittest.main()
