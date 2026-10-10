# ParaRadar Windows arka plan üretimi

Bu paket, OpenCV veya kare başına GaussianBlur kullanmadan FFmpeg ile 9:16 video üretir.
NVIDIA hosted modelinden özgün Türkçe metin, eSpeak NG ile çevrimdışı Türkçe ses,
konuşma parçalarına göre zamanlanmış ASS altyazıları ve gerçek S&P 500 tarihsel
verisinden grafik oluşturur. Ses sentetiktir; doğal insan sesi kalitesi iddia edilmez.

## Windows kurulumu

Python 3.11+ ve 64 bit Windows gerekir. Paketi bir klasöre çıkarıp o klasörde şu tek
PowerShell komutunu çalıştırın:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install_windows.ps1
```

Varsayılan proje: `C:\Users\murat\OneDrive\Masaüstü\kripto_borsa_bot`.
Başka bir konum için `-ProjectPath 'C:\proje'` parametresini ekleyin.

Kurulum eski `main.py` ve `pararadar_hizli.py` dosyalarını zaman damgalı
`pararadar_yedek` klasörüne kopyalar; özgün dosyaları değiştirmez. Mevcut MP4'ler
silinmez ve aynı isimle yeniden yazılmaz. Yeni uygulama `pararadar_service` altına
kurulur. Güncellemede servis kodu yedeklenir, mevcut videolar yerinde korunur.

FFmpeg yoksa BtbN/FFmpeg-Builds upstream Windows GPL paketini indirir. eSpeak NG
yoksa resmi espeak-ng/espeak-ng 1.52.0 MSI dosyasını uygulama araç klasörüne çıkarır.
MSI yalnızca `/a` ile çıkarılır; sistem geneline sessiz kurulum yapılmaz.

`NVIDIA_API_KEY` mevcut ortamdan veya proje `.env` dosyasından alınır.
Yoksa `pararadar_service\.env` içine eklenmelidir. Anahtar hiçbir loga yazılmaz.
Bulut ortamındaki anahtar Windows'a otomatik aktarılmaz ve pakete dahil edilmez.

Kurulum önce bir tam video üretip doğrular. Hata olursa otomatik görevleri
etkinleştirmez. Başarılı olursa:

- `ParaRadar-Worker`: oturum açıldığında görünmez `pythonw` süreci başlar.
- `ParaRadar-Daily10`: her gün Windows yerel saatiyle **10:00**'da 10 video kuyruğa ekler.
- Ayrıca ilk 10 videoluk üretimi hemen kuyruğa ekler.
- Panel: `http://127.0.0.1:8765`.

Windows oturumu açık ve bilgisayar uyanık olmalıdır. Bu kurulum parola saklamaz;
kullanıcı oturumu kapalıyken veya bilgisayar kapalıyken çalışmaz. Günlük saati
değiştirmek için kurulum komutuna `-DailyTime '18:00'` ekleyin.

## Çıktılar ve durum

MP4: `pararadar_service\data\videos\<iş_id>\01\video.mp4`.
Her videoda `test_results.json`, `content.json`, `source.json`, `market.csv`,
`subtitles.ass`, `subtitles.json`, `voice.wav` ve üç örnek kare bulunur.
Log: `pararadar_service\data\worker.log`.

İlk video tüm testlerden geçmeden aynı işin ikinci videosu başlamaz. Başarısızlıkta
iş durur, önceden üretilen videolar korunur. Süreç kapanıp açılırsa yarım kalan iş
`interrupted` olarak işaretlenir; API çağrıları sessizce tekrar edilmez. Ayrı
iş kimlikleri ve işlem kilidi yanlışlıkla aynı çıktıyı ezmeyi önler.

Geçici bağlantı/zaman aşımı hatasında bir kez daha denenir; bu loga yazılır ve
ek API kullanımı oluşabilir. Model yanıt süresi sınırı 180 saniyedir. Yetki,
kota ve içerik doğrulama hataları otomatik olarak tekrar edilmez.

```powershell
.\pararadar_service\.venv\Scripts\python.exe .\pararadar_service\pararadar.py status
```

Kesilen veya başarısız bir işi `resume --id <iş_id>` ile kuyruğa döndürebilirsiniz.
Tamamlanan videolar korunur; yarım çıktılar silinmeden `failed_attempts` altına
taşınır. Veritabanı yazımı öncesinde kapanmış ama testi geçmiş çıktı yeniden
doğrulanır ve yeni AI çağrısı yapılmadan kullanılır.

Görevleri kapatmak için `pararadar_service\stop_windows.ps1` çalıştırın. Video
dosyaları korunur. Görevler Windows Görev Zamanlayıcı'dan yeniden etkinleştirilebilir.

## Video testleri

- 720×1280, 9:16, 25 FPS, H.264/yuv420p + AAC.
- Video ve ses süresi farkı <0,2 saniye; toplam süre 20–75 saniye.
- Tüm karelerde decode + siyah bölüm taraması.
- Başlangıç, orta, son örnek karelerinde parlaklık/kontrast.
- Ses seviyesinin sessiz olmadığının kontrolü ve tepe seviye ölçümü.
- ASS zaman çizelgesi ve gömülü altyazı bölgesinde metin piksellerinin kontrolü.

Bu testler konuşmanın anlamını, tüm altyazı metninin OCR doğruluğunu veya yatırım
yorumlarının doğruluğunu kanıtlamaz. Veri tarihleri ve kaynak özeti videoda açıkça
gösterilir; tarihsel grafik güncel fiyat gibi sunulmaz. Gerçek kaynaktan gelen verinin
CSV kopyası, URL'si, SHA256 özeti ve indirme zamanı kayıtlıdır.

## TikTok

Bu uygulama otomatik TikTok yüklemesi yapmaz. Windows kaynak projesinin yükleme
koduna erişim olmadığı için yeni bir yükleyici varsayılmadı. Kullanılabilir bulut
projesinde resmi OAuth/Content Posting entegrasyonu vardır; bu paket onu değiştirmez.
Mevcut ortamda `TIKTOK_CLIENT_KEY` ve kullanıcı `TIKTOK_ACCESS_TOKEN` bulunmuyor.
Yalnızca client secret yeterli değildir; kullanıcı yetkilendirmesi, `video.publish`
izni ve TikTok uygulama kısıtlarının karşılanması gerekir. Tarayıcı otomasyonu,
cookie/şifre ile giriş veya resmi olmayan upload kütüphanesi kullanılmaz.

## Test ortamı ile Windows ayrımı

Video üretimi ve kuyruk Linux bulut çalışma ortamında test edildi. Windows
kurulum betiği Windows makinesinde henüz çalıştırılmadı. Orijinal Windows
`main.py` ve `pararadar_hizli.py` bu oturuma bağlı olmadığı için incelenemedi;
orijinal karanlık video hatasının dosya içindeki nedeni teşhis edildiği iddia edilmez.
Yeni üretim yolu, görseli bir kez oluşturup FFmpeg ile kodlar; doğrulanamayan MP4
başarılı çıktı olarak kabul edilmez.

## Masaüstündeki NVIDIA anahtarını otomatik bulma

Kurucu proje klasörünü, Windows masaüstünü ve OneDrive Desktop/Masaüstü klasörlerinin doğrudan dosyalarını kontrol eder. `.env`, adında key/anahtar/nvidia bulunan TXT/JSON dosyaları ve main.py/pararadar_hizli.py içindeki sabit NVIDIA anahtarları desteklenir. Eski Python dosyaları çalıştırılmaz. Yalnızca nvapi- biçimindeki NVIDIA anahtarı seçilir; borsa anahtarları kullanılmaz. Birden fazla farklı anahtarda seçim yapılmadan kurulum durur. Anahtar sadece yerel pararadar_service/.env içine kaydedilir; ekrana, rapora veya GitHub’a aktarılmaz. Gerçek API erişimi ilk tam video üretiminde sınanır. Bu Windows adımı bulut ortamında çalıştırılmamıştır.
