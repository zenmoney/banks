"""Smoke result validator tests; generated fixtures are not real Compose passes."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from PIL import Image, ImageDraw

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import render_smoke as smoke


class RenderSmoke(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.output=Path(temp.name)
        self.result={'status':'success','id':10,'source':{'sha256':smoke.sha(smoke.SVG)},
                     'xml':{'resourceName':'bank_10','sha256':'a'*64,'loadedSha256':'a'*64},
                     'renderer':'offscreen CPU Skia/painterResource','images':[]}
        for theme in ('light','dark'):
            path=self.output/(theme+'.png')
            image=Image.new('RGB',(240,240),'white')
            ImageDraw.Draw(image).rectangle((10,10,50,50),fill='red')
            image.save(path)
            self.result['images'].append({'theme':theme,'path':path.name,'sha256':smoke.sha(path.read_bytes())})
        self.save()

    def save(self):
        (self.output/'result.json').write_text(json.dumps(self.result))

    def test_both_frames_and_exact_source_xml_bindings_required(self):
        self.assertEqual(smoke.validate_export(self.output,10,smoke.SVG)['status'],'success')
        self.result['xml']['loadedSha256']='b'*64;self.save()
        with self.assertRaises(ValueError): smoke.validate_export(self.output,10,smoke.SVG)

    def test_blank_or_tampered_frame_is_not_a_pass(self):
        image=Image.new('RGB',(240,240),'white');image.save(self.output/'light.png')
        with self.assertRaises(ValueError): smoke.validate_export(self.output,10,smoke.SVG)
        self.result['images'][0]['sha256']=smoke.sha((self.output/'light.png').read_bytes());self.save()
        with self.assertRaisesRegex(ValueError,'blank'): smoke.validate_export(self.output,10,smoke.SVG)


