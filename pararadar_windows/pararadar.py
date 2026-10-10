"""ParaRadar: verified FFmpeg videos, persistent queue and loopback dashboard."""
from __future__ import annotations
import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import logging
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
from PIL import Image, ImageDraw, ImageFont, ImageStat
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env', override=False, encoding='utf-8-sig')
if os.getenv('FFMPEG_DIR'):
    os.environ['PATH'] = os.environ['FFMPEG_DIR'] + os.pathsep + os.environ.get('PATH', '')
DATA = Path(os.getenv('PARARADAR_DATA', str(ROOT / 'data'))).resolve()
SOURCE = 'https://raw.githubusercontent.com/datasets/s-and-p-500/main/data/data.csv'
MODEL = os.getenv('NVIDIA_MODEL', 'google/gemma-4-31b-it')
TOPICS = ['Uzun vadede dalgalanma', 'Düzenli yatırım ve zamanlama', 'Nominal ve reel getiri',
          'Düşüş dönemlerinde risk', 'Çeşitlendirme neden önemli', 'Geçmiş getiri ve beklenti',
          'Enflasyon ve birikim', 'Yatırım süresi ve risk', 'Bileşik getiri mantığı', 'Endeksleri doğru okumak']

def save(path, data):
    path = Path(path)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)

def run(args, cwd=None, env=None):
    result = subprocess.run([str(a) for a in args], cwd=cwd, env=env, capture_output=True,
                            timeout=600, check=False)
    if result.returncode:
        raise RuntimeError(f'{Path(str(args[0])).name} failed: ' + result.stderr.decode(errors='replace')[-1800:])
    return result

def dependencies():
    for command in ('ffmpeg', 'ffprobe'):
        if not shutil.which(command):
            raise RuntimeError(f'{command} kurulu değil veya PATH içinde değil')
    filters = run(['ffmpeg', '-hide_banner', '-filters']).stdout.decode()
    if not re.search(r'\bass\s', filters):
        raise RuntimeError('FFmpeg libass desteği gerekli')
    exe = os.getenv('ESPEAK_BIN') or shutil.which('espeak-ng')
    if not exe or not Path(exe).is_file():
        raise RuntimeError('Türkçe ses için espeak-ng kurun veya ESPEAK_BIN tanımlayın')
    return exe

def market_data(folder):
    with httpx.Client(timeout=45, follow_redirects=False) as client:
        response = client.get(SOURCE)
        response.raise_for_status()
    raw = response.content
    rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8'))))
    today = dt.date.today().isoformat()
    rows = [r for r in rows if '2016-01-01' <= r['Date'] <= today and float(r['SP500']) > 0]
    if len(rows) < 24 or not all(rows[i]['Date'] < rows[i+1]['Date'] for i in range(len(rows)-1)):
        raise RuntimeError('Finans verisinin tarih veya kapsam doğrulaması başarısız')
    (folder / 'market.csv').write_bytes(raw)
    context = {'source': SOURCE, 'retrieved_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
               'sha256': hashlib.sha256(raw).hexdigest(), 'start': rows[0]['Date'],
               'end': rows[-1]['Date'], 'first': float(rows[0]['SP500']),
               'last': float(rows[-1]['SP500']), 'points': len(rows),
               'frequency': 'monthly', 'live': False}
    save(folder / 'source.json', context)
    return rows, context

def generate(topic, context, folder):
    key = os.getenv('NVIDIA_API_KEY')
    if not key:
        raise RuntimeError('NVIDIA_API_KEY eksik; sahte AI içeriğiyle devam edilmiyor')
    prompt = (
        'Özgün Türkçe finans eğitimi seslendirme metni yaz. Yalnızca JSON döndür: '
        '{"title":"en fazla 35 karakter", "script":"75 ile 95 Türkçe kelime"}. '
        'Cümleler kısa, dilbilgisi doğru ve doğal Türkçe konuşma dilinde olsun. '
        'Metin içinde talimatları tekrar etme; meta açıklama yazma. '
        'Markdown, başlık, madde, parantez kullanma. '
        'S&P 500 ifadesini seslendirmede "Amerikan beş yüz endeksi" diye yaz. '
        'Bu grafik aylık tarihsel veridir; bugünün fiyatını temsil etmez. Kaynakta olmayan '
        'fiyat, olay veya haber uydurma. Metinde sayı, tarih, yüzde veya dönem karşılaştırması kullanma. '
        'Endeksin uzun vadeli yükselişini ve ara dalgalanmaları genel olarak anlat. '
        'Panik satışı, mutlaka toparlanma veya belirli tarihli kriz iddiası kurma. '
        'Konuyu grafiğe bağla. '
        'Son cümle tam olarak "Yatırım tavsiyesi değildir." olsun. Kazanç garantisi verme. '
        f'Konu: {topic}. Doğrulanmış veri özeti: {json.dumps(context, ensure_ascii=False)}'
    )
    with httpx.Client(timeout=httpx.Timeout(180, connect=15)) as client:
        for attempt in range(2):
            try:
                response = client.post('https://integrate.api.nvidia.com/v1/chat/completions',
                    headers={'Authorization': 'Bearer ' + key}, json={
                        'model': MODEL, 'messages': [{'role': 'user', 'content': prompt}],
                        'max_tokens': 1200, 'temperature': 0.7, 'stream': False,
                        'chat_template_kwargs': {'enable_thinking': False}})
                break
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt: raise
                logging.warning('NVIDIA bağlantısı kesildi; bir kez yeniden deneniyor (ek API kullanımı olabilir)')
                time.sleep(3)
        if response.status_code != 200:
            raise RuntimeError(f'NVIDIA HTTP {response.status_code}; anahtar veya model erişimini kontrol edin')
        choice = response.json()['choices'][0]
        if choice.get('finish_reason') != 'stop':
            raise RuntimeError('AI metni eksik döndü')
        content = choice['message']['content'].strip()
    content = re.sub(r'^```(?:json)?\s*|\s*```$', '', content)
    result = json.loads(content)
    script = result['script'].strip()
    script = re.sub(r'S\s*&\s*P\s*500', 'Amerikan beş yüz endeksi', script)
    result['script'] = script
    save(folder / 'ai_response.json', result)
    if not 40 <= len(script.split()) <= 125 or len(result['title']) > 70:
        raise RuntimeError(f"AI metni süre veya başlık sınırına uymadı (kelime={len(script.split())}, başlık={len(result['title'])})")
    if not script.endswith('Yatırım tavsiyesi değildir.') or not any(c in script for c in 'çğıöşüÇĞİÖŞÜ'):
        raise RuntimeError('Türkçe içerik doğrulaması başarısız')
    if re.search(r'\d', script) or any(s in script.lower() for s in ('en fazla bir', 'sayısal karşılaştırma yap', 'sadece json')):
        raise RuntimeError('AI metni desteklenmeyen sayı veya üretim talimatı içeriyor')
    result.update(model=MODEL, topic=topic, ai_generated=True)
    save(folder / 'content.json', result)
    return result

def font(size):
    candidates = [os.getenv('PARARADAR_FONT', ''), 'C:/Windows/Fonts/arial.ttf',
                  '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']
    for p in candidates:
        if p and Path(p).is_file():
            return ImageFont.truetype(p, size)
    raise RuntimeError('Türkçe karakter destekli font gerekli: PARARADAR_FONT')

def wrap(text, width=28):
    lines, line = [], ''
    for word in text.split():
        if len(line + ' ' + word) > width and line:
            lines.append(line)
            line = word
        else:
            line = (line + ' ' + word).strip()
    return lines + ([line] if line else [])

def artwork(rows, context, content, folder):
    # Rasterize once per video. No OpenCV and no per-frame Gaussian blur.
    im = Image.new('RGB', (720, 1280), '#edf3fa')
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, 720, 120), fill='#112d50')
    d.text((45, 35), 'PARARADAR', font=font(42), fill='white')
    y = 155
    for line in wrap(content['title'], 26):
        d.text((42, y), line, font=font(36), fill='#112d50')
        y += 46
    d.text((42, 305), 'S&P 500 • Aylık tarihsel veri', font=font(25), fill='#385575')
    d.rounded_rectangle((30, 360, 690, 850), radius=24, fill='white')
    values = [float(r['SP500']) for r in rows]
    low, high = min(values)*0.94, max(values)*1.04
    def point(i):
        return (100 + i * 545 / (len(values)-1), 760-(values[i]-low)/(high-low)*315)
    for k in range(5):
        yy = 760-k*315/4
        d.line((100, yy, 645, yy), fill='#dde5ef', width=2)
        d.text((40, yy-12), str(round(low+k*(high-low)/4)), font=font(17), fill='#385575')
    pts = [point(i) for i in range(len(values))]
    d.line(pts, fill='#008f7e', width=5)
    x,y=pts[-1]
    d.ellipse((x-7,y-7,x+7,y+7),fill='#112d50')
    d.text((100, 785), context['start'][:7], font=font(20), fill='#385575')
    d.text((540, 785), context['end'][:7], font=font(20), fill='#385575')
    d.text((45, 875), 'Kaynak: datasets / Shiller / FRED', font=font(22), fill='#385575')
    d.text((45, 910), 'Canlı fiyat değildir • Endeks puanı', font=font(21), fill='#385575')
    d.rounded_rectangle((25, 977, 695, 1195),radius=20,fill='#112d50')
    d.text((72, 1220), 'Eğitim amaçlıdır • Yatırım tavsiyesi değildir', font=font(21), fill='#385575')
    im.save(folder / 'background.png')

def ass_time(seconds):
    centis = round(seconds*100)
    h, rem = divmod(centis, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f'{h}:{m:02}:{s:02}.{cs:02}'

def narration(script, folder, exe):
    # Generate each short phrase separately: subtitle boundaries match real audio,
    # rather than guessing sentence timings from a single long TTS file.
    words = script.split()
    chunks, group = [], []
    for word in words:
        if group and len(' '.join(group + [word])) > 50:
            chunks.append(' '.join(group)); group=[]
        group.append(word)
        if len(group)>=5 or (word.endswith(('.', '?', '!')) and len(group)>=3):
            chunks.append(' '.join(group)); group=[]
    if group: chunks.append(' '.join(group))
    env = os.environ.copy()
    if os.getenv('ESPEAK_LIB'): env['LD_LIBRARY_PATH'] = os.getenv('ESPEAK_LIB')
    options = ['--path='+os.environ['ESPEAK_DATA']] if os.getenv('ESPEAK_DATA') else []
    events, pcm, offset, params = [], [], 0.0, None
    for i, text in enumerate(chunks):
        part = folder / f'speech_{i:03}.wav'
        run([exe, *options, '-v', 'tr', '-s', '155', '-w', part, text], env=env)
        with wave.open(str(part), 'rb') as audio:
            if params is None: params=audio.getparams()
            if (audio.getnchannels(),audio.getsampwidth(),audio.getframerate()) != (params.nchannels,params.sampwidth,params.framerate):
                raise RuntimeError('TTS PCM formatları uyuşmuyor')
            duration=audio.getnframes()/audio.getframerate()
            pcm.append(audio.readframes(audio.getnframes()))
        events.append({'start':offset,'end':offset+duration,'text':text})
        offset+=duration
    with wave.open(str(folder/'voice.wav'),'wb') as audio:
        audio.setparams(params)
        audio.writeframes(b''.join(pcm))
    if not 20 <= offset <= 75: raise RuntimeError(f'Ses süresi beklenen aralıkta değil: {offset:.2f}')
    header = '''[Script Info]
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280
WrapStyle: 2
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,38,&H00FFFFFF,&H0000DBFF,&H00502D11,&H00502D11,-1,0,0,0,100,100,0,0,1,1,0,5,50,50,0,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    lines=[]
    for ev in events:
        # Short two-line captions positioned away from platform buttons.
        rows=wrap(ev['text'], 24)
        if len(rows)>3: raise RuntimeError('Altyazı okunabilirlik sınırını aşıyor')
        caption=r'\N'.join(rows).replace('{','').replace('}','')
        lines.append(f"Dialogue: 0,{ass_time(ev['start'])},{ass_time(ev['end'])},Default,,0,0,0,,{{\\pos(360,1085)\\fad(70,70)}}{caption}")
    (folder/'subtitles.ass').write_text(header+'\n'.join(lines)+'\n',encoding='utf-8-sig')
    save(folder/'subtitles.json', events)
    return offset, events

def render(folder, duration):
    run(['ffmpeg','-hide_banner','-loglevel','error','-n','-loop','1','-framerate','25',
         '-i','background.png','-i','voice.wav','-vf',
         'ass=subtitles.ass,drawbox=x=30:y=952:w=660:h=6:color=0x008f7e:t=fill',
         '-af','loudnorm=I=-16:TP=-1.5:LRA=11','-t',str(duration),'-r','25',
         '-c:v','libx264','-preset','veryfast','-crf','21','-pix_fmt','yuv420p',
         '-c:a','aac','-b:a','128k','-movflags','+faststart','video.mp4'],cwd=folder)

def validate(folder, expected, events):
    path=folder/'video.mp4'
    probe=json.loads(run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',path]).stdout)
    video=next(s for s in probe['streams'] if s['codec_type']=='video')
    audio=next(s for s in probe['streams'] if s['codec_type']=='audio')
    actual=float(probe['format']['duration'])
    # Decode every frame, measure black segments and audio volume over entire file.
    scan=run(['ffmpeg','-hide_banner','-xerror','-i',path,'-vf','blackdetect=d=0.2:pix_th=0.10:pic_th=0.98',
              '-af','volumedetect','-f','null','-']).stderr.decode(errors='replace')
    mean=float(re.search(r'mean_volume: ([-\w.]+) dB',scan).group(1))
    peak=float(re.search(r'max_volume: ([-\w.]+) dB',scan).group(1))
    samples=[]
    for i,fraction in enumerate((0.1,0.5,0.9)):
        ts=actual*fraction
        # Fade transitions intentionally have no full-opacity text. Test the
        # midpoint of the caption covering the target time, not its fade-out.
        active=next((e for e in events if e['start']<=ts<e['end']),events[-1])
        ts=(active['start']+active['end'])/2
        target=folder/f'frame_{i}.png'
        run(['ffmpeg','-v','error','-y','-ss',str(ts),'-i',path,'-frames:v','1',target])
        im=Image.open(target).convert('L')
        stats=ImageStat.Stat(im)
        # Check burned-in caption: a large number of white glyph pixels in dark box.
        caption=im.crop((45,995,675,1175))
        white=sum(n for level,n in enumerate(caption.histogram()) if level>205)
        samples.append({'seconds':round(ts,2),'mean_luma':round(stats.mean[0],2),
                        'contrast_std':round(stats.stddev[0],2),'subtitle_white_pixels':white})
    checks={'portrait_9_16':video['width']*16==video['height']*9,
            'h264_yuv420p':video['codec_name']=='h264' and video['pix_fmt']=='yuv420p',
            'aac_audio':audio['codec_name']=='aac',
            'duration':abs(actual-expected)<0.2 and 20<=actual<=75,
            'av_sync':abs(float(video['duration'])-float(audio['duration']))<0.2,
            'no_black_segments':'black_start:' not in scan,
            'visible_frames':all(s['mean_luma']>35 and s['contrast_std']>25 for s in samples),
            'audible_audio':math.isfinite(mean) and -35<mean<-5 and peak<=0,
            'subtitles_burned_in':all(s['subtitle_white_pixels']>350 for s in samples),
            'subtitle_timeline':len(events)>=5 and abs(events[-1]['end']-expected)<0.03
                and all(0<=e['start']<e['end']<=expected+0.01 for e in events)}
    report={'passed':all(checks.values()),'checks':checks,'duration_seconds':actual,
            'resolution':[video['width'],video['height']],'fps':video['avg_frame_rate'],
            'audio_mean_db':mean,'audio_peak_db':peak,'samples':samples,
            'subtitle_events':len(events),'speech_engine':'eSpeak NG tr (offline, synthetic)',
            'limits':'Ses anlaşılabilirliği, finans yorumunun doğruluğu ve tam metin OCR incelemesi insan kontrolü gerektirir.'}
    save(folder/'test_results.json',report)
    if not report['passed']: raise RuntimeError('Video testi başarısız: '+str([k for k,v in checks.items() if not v]))
    return report

def produce(topic, folder):
    folder.mkdir(parents=True,exist_ok=False)
    exe=dependencies()
    logging.info('Finans verisi alınıyor: %s',folder.name)
    rows,ctx=market_data(folder)
    logging.info('Türkçe metin üretiliyor: %s (%s)',topic,MODEL)
    content=generate(topic,ctx,folder)
    artwork(rows,ctx,content,folder)
    logging.info('Türkçe ses ve altyazı hazırlanıyor: %s',folder.name)
    duration,events=narration(content['script'],folder,exe)
    logging.info('FFmpeg video kodlanıyor: %s',folder.name)
    render(folder,duration)
    report=validate(folder,duration,events)
    logging.info('Video tamamlandı: %s (%ss)',folder/'video.mp4',report['duration_seconds'])
    return report

class Database(sqlite3.Connection):
    def __exit__(self, exc_type, exc, traceback):
        try:
            return super().__exit__(exc_type, exc, traceback)
        finally:
            self.close()

def db():
    DATA.mkdir(parents=True,exist_ok=True)
    connection=sqlite3.connect(DATA/'queue.sqlite',timeout=30,factory=Database)
    connection.row_factory=sqlite3.Row
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, count INTEGER, state TEXT, completed INTEGER, error TEXT, created REAL)')
    return connection

def enqueue(count):
    if not 1<=count<=10: raise ValueError('Video sayısı 1–10 olmalı')
    job=uuid.uuid4().hex
    with db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0]>=3:
            raise ValueError('En fazla üç bekleyen iş olabilir')
        conn.execute('INSERT INTO jobs VALUES (?,?,?,0,NULL,?)',(job,count,'queued',time.time()))
    return job

def process_job(job):
    folder=DATA/'videos'/job['id']
    folder.mkdir(parents=True,exist_ok=True)
    try:
        # First full video must pass before any further video is produced.
        for i in range(job['completed'],job['count']):
            destination=folder/f'{i+1:02}'
            if destination.exists():
                try:
                    cached=json.loads((destination/'test_results.json').read_text())
                    events=json.loads((destination/'subtitles.json').read_text())
                    if cached.get('passed'):
                        validate(destination,events[-1]['end'],events)
                        with db() as conn:
                            conn.execute('UPDATE jobs SET completed=? WHERE id=?',(i+1,job['id']))
                        logging.info('Önceden doğrulanmış video korundu: %s',destination)
                        continue
                except (OSError,ValueError,KeyError,IndexError,RuntimeError):
                    pass
                archive=folder/'failed_attempts'
                archive.mkdir(exist_ok=True)
                destination.rename(archive/f'{i+1:02}_{uuid.uuid4().hex[:8]}')
            produce(TOPICS[i],destination)
            with db() as conn: conn.execute('UPDATE jobs SET completed=? WHERE id=?',(i+1,job['id']))
        with db() as conn: conn.execute("UPDATE jobs SET state='done' WHERE id=?",(job['id'],))
    except Exception as exc:
        logging.error('Üretim durdu: %s',str(exc))
        with db() as conn: conn.execute("UPDATE jobs SET state='failed',error=? WHERE id=?",(str(exc)[:1800],job['id']))

def worker():
    with db() as conn:
        # Restart never silently repeats a charged API call or overwrites output.
        conn.execute("UPDATE jobs SET state='interrupted',error='Süreç yeniden başladı; tamamlanan videolar korundu' WHERE state='running'")
    while True:
        with db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
            if row: conn.execute("UPDATE jobs SET state='running' WHERE id=?",(row['id'],))
        if row: process_job(dict(row))
        else: time.sleep(2)

def resume(identifier):
    if not re.fullmatch(r'[0-9a-f]{32}',identifier): raise ValueError('Geçersiz iş kimliği')
    with db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT * FROM jobs WHERE id=?',(identifier,)).fetchone()
        if not row or row['state'] not in ('failed','interrupted'):
            raise ValueError('Yalnızca başarısız veya kesilen işler devam ettirilebilir')
        if conn.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0]>=3:
            raise ValueError('Kuyruk dolu')
        conn.execute("UPDATE jobs SET state='queued',error=NULL WHERE id=?",(identifier,))
    return identifier

def acquire_lock():
    DATA.mkdir(parents=True,exist_ok=True)
    handle=open(DATA/'worker.lock','a+b')
    handle.seek(0); handle.write(b'0'); handle.flush(); handle.seek(0)
    try:
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError('Bu veri klasöründe başka bir ParaRadar çalışanı var')
    return handle

class Handler(BaseHTTPRequestHandler):
    def reply(self,data,status=200,kind='application/json; charset=utf-8'):
        raw=(json.dumps(data,ensure_ascii=False) if isinstance(data,(dict,list)) else data).encode()
        self.send_response(status); self.send_header('Content-Type',kind)
        self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    def do_GET(self):
        if self.path=='/':
            self.reply((ROOT/'dashboard.html').read_text(encoding='utf-8'),kind='text/html; charset=utf-8')
        elif self.path=='/api/status':
            with db() as conn: jobs=[dict(r) for r in conn.execute('SELECT * FROM jobs ORDER BY created DESC LIMIT 30')]
            self.reply({'jobs':jobs,'data_path':str(DATA),'publishing':'disabled',
                        'tiktok_missing':[k for k in ('TIKTOK_CLIENT_KEY','TIKTOK_ACCESS_TOKEN') if not os.getenv(k)]})
        else: self.reply({'error':'Bulunamadı'},404)
    def do_POST(self):
        # Local API requires exact Origin + JSON; prevents drive-by browser requests.
        origin=f'http://127.0.0.1:{self.server.server_port}'
        if self.headers.get('Origin')!=origin or self.headers.get('Content-Type')!='application/json':
            return self.reply({'error':'Yerel panelden işlem yapın'},403)
        if self.path!='/api/jobs':return self.reply({'error':'Bulunamadı'},404)
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=100: raise ValueError('Geçersiz istek boyutu')
            data=json.loads(self.rfile.read(length))
            if type(data.get('count')) is not int:raise ValueError('Geçersiz video sayısı')
            self.reply({'id':enqueue(data['count'])},202)
        except (ValueError,KeyError):self.reply({'error':'Video sayısı 1–10; en fazla üç bekleyen iş'},400)
    def log_message(self,fmt,*args):
        logging.info('Panel: '+fmt,*args)

def main():
    parser=argparse.ArgumentParser(description='ParaRadar arka plan video üretimi')
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('run');p.add_argument('--count',type=int,default=1)
    p=sub.add_parser('serve');p.add_argument('--port',type=int,default=8765)
    p=sub.add_parser('enqueue');p.add_argument('--count',type=int,default=10)
    p=sub.add_parser('resume');p.add_argument('--id',required=True)
    sub.add_parser('status')
    args=parser.parse_args()
    DATA.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s',
        handlers=[logging.FileHandler(DATA/'worker.log',encoding='utf-8'),logging.StreamHandler()])
    logging.getLogger('httpx').setLevel(logging.WARNING)
    if args.command=='enqueue': print(enqueue(args.count));return
    if args.command=='resume': print(resume(args.id));return
    if args.command=='status':
        with db() as conn: print(json.dumps([dict(r) for r in conn.execute('SELECT * FROM jobs ORDER BY created DESC')],ensure_ascii=False,indent=2))
        return
    lock=acquire_lock()
    try:
        if args.command=='run':
            job=enqueue(args.count)
            with db() as conn:
                conn.execute("UPDATE jobs SET state='running' WHERE id=?",(job,))
                row=dict(conn.execute('SELECT * FROM jobs WHERE id=?',(job,)).fetchone())
            process_job(row)
            with db() as conn: result=dict(conn.execute('SELECT * FROM jobs WHERE id=?',(job,)).fetchone())
            print(json.dumps(result,ensure_ascii=False,indent=2))
            if result['state']!='done': raise SystemExit(1)
        else:
            server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
            threading.Thread(target=worker,daemon=True).start()
            logging.info('ParaRadar paneli: http://127.0.0.1:%s',args.port)
            server.serve_forever()
    finally: lock.close()

if __name__=='__main__':main()
