"""Regression: reject the original black/silent-video failure, protect queue limits."""
import json
from pathlib import Path
import tempfile
import unittest
import concurrent.futures
from unittest.mock import patch
import pararadar as p

class Regression(unittest.TestCase):
    def test_black_silent_wrong_duration_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=Path(tmp)
            p.run(['ffmpeg','-v','error','-n','-f','lavfi','-i','color=black:s=720x1280:r=25:d=20',
                   '-f','lavfi','-i','anullsrc=r=22050:cl=mono','-t','20',
                   '-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p','-c:a','aac',f/'video.mp4'])
            with self.assertRaises(RuntimeError):
                p.validate(f,25,[{'start':0,'end':25,'text':'Altyazı yok'}])
            checks=json.loads((f/'test_results.json').read_text())['checks']
            for name in ('no_black_segments','visible_frames','audible_audio','duration','subtitles_burned_in'):
                self.assertFalse(checks[name],name)

    def test_concurrent_queue_limit(self):
        old=p.DATA
        with tempfile.TemporaryDirectory() as tmp:
            p.DATA=Path(tmp)
            p.db().close()
            def submit(_):
                try: return p.enqueue(1)
                except ValueError:return None
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
                    results=list(pool.map(submit,range(6)))
                self.assertEqual(sum(x is not None for x in results),3)
            finally:p.DATA=old

    def test_resume_preserves_completed_and_partial_files(self):
        old=p.DATA
        with tempfile.TemporaryDirectory() as tmp:
            p.DATA=Path(tmp)
            try:
                identifier=p.enqueue(2)
                root=p.DATA/'videos'/identifier
                (root/'01').mkdir(parents=True)
                (root/'01'/'video.mp4').write_bytes(b'completed video sentinel')
                (root/'02').mkdir()
                (root/'02'/'partial.txt').write_text('keep this failed attempt')
                with p.db() as db:
                    db.execute("UPDATE jobs SET state='failed',completed=1 WHERE id=?",(identifier,))
                p.resume(identifier)
                with p.db() as db:job=dict(db.execute('SELECT * FROM jobs WHERE id=?',(identifier,)).fetchone())
                def replacement(topic,folder):
                    folder.mkdir();(folder/'video.mp4').write_bytes(b'new second video')
                with patch.object(p,'produce',side_effect=replacement) as producer:
                    p.process_job(job)
                    self.assertEqual(producer.call_count,1)
                self.assertEqual((root/'01'/'video.mp4').read_bytes(),b'completed video sentinel')
                self.assertEqual(len(list((root/'failed_attempts').glob('*/partial.txt'))),1)
                with p.db() as db:
                    result=db.execute('SELECT state,completed FROM jobs WHERE id=?',(identifier,)).fetchone()
                    self.assertEqual(tuple(result),('done',2))
            finally:p.DATA=old

if __name__=='__main__':unittest.main()
