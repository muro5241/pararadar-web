# ParaRadar Windows TikTok Direct Post

Bu entegrasyon `fix/pararadar-ascii-paths` dalındaki çalışan üretim sisteminin yanına eklenir.
`pararadar.py`, `install_windows.ps1`, üretim ortamı, mevcut MP4 dosyaları ve
`ParaRadar-Worker` / `ParaRadar-Daily10` görevleri değiştirilmez.

## Hızlı kurulum

1. TikTok Developer Portal'da uygulamanın **Login Kit / Desktop** platformunu açın.
   Dönüş adresini tam olarak `http://127.0.0.1:8766/auth/tiktok/callback` kaydedin.
   Content Posting API **Direct Post** ve `video.publish` izni onaylanmış olmalıdır.
   Uygulama onayı veya izin yokken gönderim kapalı kalır; OAuth girişi bunu aşmaz.
2. Bu dalı/kurulum ZIP'ini indirin. `pararadar_windows` klasöründe PowerShell açın:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install_tiktok.ps1
```

Başka proje klasörü için `-ProjectPath 'C:\proje'` ekleyin. Mevcut
`pararadar_service\.venv\Scripts\python.exe` varsa bu kurulumun yanına eklenir.
Yoksa sistemdeki Python 3.11+ ile `%LOCALAPPDATA%\ParaRadar\tiktok_service`
altına bağımsız kurulur; mevcut üretim servisi zorunlu değildir. TikTok bağımlılıkları
ayrı `.tiktok-venv` içine kurulur; üretim bağımlılıkları yükseltilmez.
Kurulum yalnızca `ParaRadar-TikTok` oturum açılış görevini ekler ve başlatır.

3. Client key'i girin. Client secret gizli giriş alanından alınır ve yalnızca
   mevcut Windows kullanıcısıyla açılabilen DPAPI dosyasına kaydedilir.
   Portal'da Direct Post ve kapsam onayı **gerçekten varsa** `APPROVED` yazın;
   aksi halde Enter ile devam edin, yayınlama kapalı kalır.
   Bu işaret yönetici beyanıdır; TikTok uygulama onayını otomatik sorgulayan
   bir API varmış gibi sunulmaz. TikTok API'si de gerçek yetkiyi kontrol eder.
4. `http://127.0.0.1:8766/` panelinde **TikTok hesabını bağla** bağlantısını açın.
   TikTok girişinde `video.publish` erişimini verin. Bağlantı kullanıcının Windows
   bilgisayarında çalışan servisten üretilir; bulutta oluşturulan localhost
   bağlantısı Windows servisine yetki kazandıramaz.
5. **Tek bir video** seçin: panel üretim sisteminin testten geçmiş
   `data/videos/<iş_id>/<no>/video.mp4` çıktılarını listeler. Listede yoksa dosya
   alanından bir MP4 seçebilirsiniz. Paket örneği: `ilk_test_videosu/video.mp4`.
   Videoyu önizleyin, başlığı düzenleyin, görünürlüğü elle **Sadece ben** seçin,
   etkileşim/ticari içerik/AI açıklamalarını kontrol edin ve açık gönderim iznini verin.
   AI açıklaması bu üretim sisteminde başlangıçta seçilidir; kullanıcı değiştirebilir.

İlk test bir tek gönderim işidir. Yeni anahtarla veya servis yeniden açılarak
ikinci video gönderilemez. Aynı idempotency anahtarı aynı iş kaydını döndürür.
MP4 boyutu/formatı ve TikTok'un güncel yaratıcı süre sınırı kontrol edilir.
Dosya TikTok'un HTTPS yükleme adresine Content-Range parçalarıyla gönderilir.
Orijinal video düzenlenmez, yeniden kodlanmaz ve üzerine yazılmaz.

## Tek testten sonra

Panel 6 saniyede bir durum sorgular. `PUBLISH_COMPLETE` TikTok'un başarı yanıtıdır;
`PROCESSING_UPLOAD` veya HTTP 202 tek başına yayın başarısı değildir.
Servis yeniden açılırsa **Durumları kontrol et** ile kayıtlı işin durumunu sorgulayın.
İlk gerçek test tamamlanınca daha fazla video için:

```powershell
.\pararadar_service\.tiktok-venv\Scripts\python.exe .\pararadar_service\tiktok_windows.py enable-after-test
Stop-ScheduledTask -TaskName ParaRadar-TikTok
Start-ScheduledTask -TaskName ParaRadar-TikTok
```

Bu komut mevcut üretim servisi yanına kurulumda üretim projesinin ana klasöründe çalıştırılır.
Bağımsız kurulumda `%LOCALAPPDATA%\ParaRadar\tiktok_service` içindeki `.tiktok-venv`
Pythonunu ve `tiktok_windows.py` dosyasını kullanın. Başarılı test kaydı yoksa
kilit kaldırılmaz. Her TikTok hesabının kendi başarılı tek video kaydı gerekir.
Herkese açık paylaşım ayrıca audit/onay ve `PUBLIC` beyanı ister; varsayılan kapalıdır.
Her sonraki gönderi de önizleme, elle görünürlük seçimi ve kullanıcı onayı ister.
Üretimden çıkan tüm videolar sessizce otomatik yayımlanmaz.

## Güvenlik ve hata davranışı

- OAuth 2.0, 10 dakikalık tek kullanımlık CSRF state, Desktop PKCE S256 (TikTok'un
  istediği SHA256 hex), HttpOnly oturum cookie'si, origin + CSRF kontrolü.
- Servis yalnızca `127.0.0.1:8766` üzerinde tek süreçtir; başka Host reddedilir.
- Tokenlar Fernet ile şifreli SQLite'tadır. Fernet anahtarı ve client secret
  DPAPI ile `%LOCALAPPDATA%\ParaRadar\TikTok\settings.dpapi` içinde korunur.
  Klasör ACL'si mevcut kullanıcı ve SYSTEM ile sınırlandırılır. Şifrelenmemiş
  fallback yoktur. Bilgisayar/kullanıcı değiştirilince yeniden yetki gerekir.
- Access token bitmeden yenilenir; refresh token değişimi atomik kaydedilir.
  Token yenileme tek süreç kilidiyle korunur. Yetki/kapsam/refresh süresi sorununda
  yeniden bağlantı gerekir. Token ve secret yanıta veya loga yazılmaz.
- Güvenli yaratıcı/durum/profil sorguları geçici bağlantı/429/5xx hatalarında
  en fazla iki kez daha beklemeyle denenir; Retry-After en fazla 60 saniyedir.
  Publish init, OAuth code exchange ve refresh istekleri körlemesine tekrarlanmaz.
- Init sonucu belirsizse `INIT_UNCERTAIN`; upload kesilirse publish_id korunur ve
  `CHECK_STATUS` kaydedilir. Aynı iş yeniden init edilmez. Otomatik tekrarın
  çift gönderi üretebileceği yerde yeni gönderim kapalı kalır.
- Hatalar iş kaydına güvenli HTTP hata özetiyle yazılır. Log:
  `%LOCALAPPDATA%\ParaRadar\TikTok\tiktok.log`. OAuth kodu içeren access log kapalıdır.
- Tokenlar, `.env`, DPAPI dosyaları ve veritabanları Git'e dahil edilmez.

Yalnızca TikTok servisini durdurmak için:

```powershell
Stop-ScheduledTask -TaskName ParaRadar-TikTok
Disable-ScheduledTask -TaskName ParaRadar-TikTok
```

## Doğrulamanın sınırları

`TIKTOK_VALIDATION.md` test sonuçlarını gösterir. Simüle TikTok API testleri canlı
paylaşım kanıtı değildir. Bu oturuma Windows bilgisayarı veya yetkili TikTok kullanıcı
oturumu bağlı değildir. Client key, callback yapılandırması ve onay beyanı sağlanmadı;
client secret tek başına yeterli değildir. Canlı upload/init/publish yapılmadı.
Windows DPAPI ve ACL koruması Windows CI’da doğrulandı. Görev Zamanlayıcı’nın
kullanıcının bilgisayarındaki kurulumu ayrıca çalıştırılmalıdır.

TikTok, Direct Post için yaratıcı bilgisi, önizleme, görünürlük seçimi ve açık
kullanıcı izni ister. Kişisel/iç kullanım araçlarının uygulama incelemesinden geçmesi
garanti değildir. Mevcut videolardaki markalama da inceleme gereksinimleriyle
kontrol edilmelidir; çalışan üretim kodu bu entegrasyonda değiştirilmedi.

Resmî kaynaklar:
- https://developers.tiktok.com/docs/en/login-kit-desktop
- https://developers.tiktok.com/docs/en/content-sharing-guidelines
- https://developers.tiktok.com/docs/en/content-posting-api-reference-direct-post
- https://developers.tiktok.com/docs/en/content-posting-api-media-transfer-guide
