# Windows TikTok entegrasyonu test sonuçları

Tarih: 10 Ekim 2026 (Europe/Istanbul).
Temel dal: `fix/pararadar-ascii-paths`, commit `d3f98c702683028416c1e6b040a9d23fac3ec9d1`.
Entegrasyon dalı: `feat/windows-tiktok-direct-post`.

## Yerel bulut ortamında tamamlanan doğrulama

- Python 3.12 / Linux: TikTok backend `pytest tests -q`: **86 geçti, 2 atlandı**.
  Chromium ile 3 gerçek tarayıcı testi dahil. Yalnızca TikTok upstream yanıtları simüle.
  Atlananlar: yetkisiz canlı kontrol ve yalnız Windows üzerinde çalışan ACL testi.
- Mevcut video üretimi: `python -m unittest test_regression test_key_discovery -v`:
  **6 test geçti**. Siyah/sessiz/geçersiz süreli video reddi, eşzamanlı kuyruk sınırı,
  tamamlanan dosyaları koruyan resume ve anahtar keşfi testleri.
- Ruff backend kontrolü ve Windows launcher sözdizimi/import kontrolü: geçti.
- JavaScript `node --check`: geçti. Python compileall: geçti.
- `git diff --check`: geçti.

## Tek mevcut MP4 ile doğrulama

`pararadar_windows/ilk_test_videosu/video.mp4` kullanıldı. Üretim kodunun `validate`
fonksiyonu dosyanın geçici kopyasında yeniden çalıştırıldı:

- 720×1280, 9:16, 25 FPS, H.264/yuv420p + AAC, 44.92 saniye.
- 10/10 doğrulama koşulu geçti: format, süre, ses/görüntü senkronu, siyah bölüm,
  görünür kareler, duyulur ses, gömülü altyazı ve zaman çizelgesi kontrolleri.
- Ortalama ses -17.2 dB; tepe -1.2 dB; 15 altyazı olayı.
- SHA256: `6b01804ee4418e1c0151f08b4f84c752affd598958d264ff176def481aaa198d`.
- Aynı MP4 Strict MockTransport üzerinden Direct Post init, FILE_UPLOAD parçaları,
  Content-Range ve status fetch yolundan geçirildi. Yüklenen byte'lar orijinalle aynı.
- Orijinal MP4 SHA256 değişmedi; mevcut üretim kodu ve kurucusu değiştirilmedi.

## Engeller ve hata testleri

- Uygulama onayı false: hiçbir init veya MP4 yükleme çağrısı yok.
- `video.publish` eksik: hiçbir init çağrısı yok.
- İlk test sadece SELF_ONLY; ikinci yeni iş servis yeniden başlasa da 409 ile reddedilir.
- Aynı idempotency key aynı işi döndürür; farklı dosya/ayar aynı key ile 409.
- İlk başarılı test olmadan single_test_mode kaldırılması gönderime izin vermez.
- Desktop PKCE challenge/verifier, tek kullanımlık state, hostile Host, origin/CSRF,
  şifreli token saklama, yenileme, hesap ayrımı ve süre sınırı kontrolleri geçti.
- Bağlantı/429/5xx için güvenli durum/yaratıcı sorgularında sınırlı retry doğrulandı.
- Init timeout bir kere çağrıldı; INIT_UNCERTAIN kaydedildi; yeniden init edilmedi.
- Upload kesilince publish_id kaybolmadı; yeniden gönderim yerine durum sorgulandı.
- Geçersiz TikTok yükleme hedefleri, format/MIME/boyut, etkileşim ve görünürlük
  kısıtları güvenli biçimde reddedildi. Ham sağlayıcı hata metni/token aktarılmadı.
- Yerel video listesi yalnızca test raporu geçen üretim çıktılarını gösterir;
  üretim klasörü dışındaki symlink ve rastgele dosya yolu reddedilir.

## Canlı/Windows kapsam sınırı

**Canlı TikTok yayınlaması yapılmadı.** Client key, callback, kullanıcı OAuth yetkisi
ve Direct Post uygulama onayı bu oturumda mevcut değil. Yalnızca client secret hazır.
Bu nedenle canlı test özellikle atlandı; simüle PUBLISH_COMPLETE canlı başarı değildir.

Kullanıcının Windows bilgisayarı bu oturuma bağlı değil. GitHub Actions Windows Server
2025 / Python 3.12.10 üzerinde **84 entegrasyon testi ve 6 üretim regresyon testi geçti**.
Gerçek DPAPI şifreleme/açma, şifreli yapılandırma ve yalnız mevcut kullanıcı/SYSTEM
SID'lerine izin veren klasör/dosya ACL kontrolü başarılı. PowerShell kurulum betiği
parse kontrolü başarılı; kullanıcının Görev Zamanlayıcı kurulumu henüz çalıştırılmadı.

Windows CI kanıtı: https://github.com/muro5241/pararadar-web/actions/runs/38074338909
İlk CI'deki POSIX chmod varsayımı Windows ACL testiyle değiştirildi. Yeni ACL testi,
önceden var olan açık izinlerin yalnız grant ekleyerek temizlenmediğini yakaladı;
Windows DACL'si artık güvenli SID'lerle tamamen değiştirilir. Son CI başarılıdır.
Bu işlemler test runner'ında sahte test anahtarlarıyla yapıldı; canlı TikTok tokenı yoktur.

FastAPI'nin yeni TestClient/httpx uyumluluğu için bir deprecation uyarısı var;
test başarısını etkilemiyor. Gerçek kullanıcı girişi ve Portal onayı tamamlandıktan
sonra TIKTOK_TR.md adımlarıyla tek gerçek SELF_ONLY videosu doğrulanmalıdır.
