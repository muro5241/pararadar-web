# Gerçek Sandbox testi ve TikTok inceleme demosu

**Mevcut durum:** Gerçek TikTok anahtarları, izinli Sandbox hesabı ve dağıtılmış HTTPS backend sağlanmadı. Gerçek OAuth/video testi veya incelemeye gönderilecek gerçek ekran kaydı henüz yapılmış değildir. Otomatik testlerdeki simülasyonları inceleme demosu olarak kullanmayın.

## Hesap sahibinin tamamlaması gerekenler

- Ayrı Render HTTPS Web Service ve kalıcı disk oluşturun; gerçek anahtarları ve bir kez üretilmiş `TOKEN_ENCRYPTION_KEY` değerini güvenli sunucu ayarlarına girin. Mevcut statik siteyi değiştirmeyin.
- TikTok Developer Portal'da Login Kit callback adresini tam eşleşecek şekilde kaydedin. Content Posting API ve `user.info.basic`, `video.publish`, `video.upload` yetkilerini sağlayın; Sandbox erişimini ve test hesabını ekleyin.
- İncelenmemiş uygulama için TikTok'un geçerli özel hesap/SELF_ONLY koşullarını karşılayan bir test hesabı kullanın. Size ait, müzik hakları uygun kısa MP4 hazırlayın. Herkese açık gönderim için ayrıca hesap sahibinin açık onayı gerekir; varsayılan sunucu sınırı bunu engeller.

## Canlı doğrulama

1. Backend `/ready` yanıtının 200 olduğunu kontrol edin. Stüdyodan TikTok bağlantısını başlatın, gerçek izin ekranını ve callback sonrası oturumu doğrulayın.
2. Hesabın kullanıcı adı, güncel süre sınırı, görünürlük seçenekleri ve etkileşim kısıtlarının gerçek API'den geldiğini doğrulayın. İzni reddetme ve yeniden bağlanma senaryolarını da deneyin.
3. Önce **Gelen kutusuna taslak** seçeneğiyle videoyu yükleyin. `SEND_TO_USER_INBOX` durumunu ve gerçek TikTok bildirimini doğrulayın. Bu durum yayımlandı demek değildir.
4. Özel Direct Post testi için **Sadece ben** görünürlüğünü elle seçin, önizlemeyi ve müzik onayını kabul edin, gönderin. `PUBLISH_COMPLETE` sonucunu ve TikTok'ta özel videoyu doğrulayın. Sunucu izni olmadan başka görünürlük gönderilemez.
5. Token yenileme düğmesini, oturum kapatma/yeniden giriş ve bağlantı kaldırma işlemlerini test edin. Backend yeniden başladıktan sonra tokenların kaybolmadığını kontrol edin.

İsteğe bağlı otomatik canlı kontrol için, oturum çerezini paylaşmadan güvenli yerel bir dosyada (izin `0600`) saklayın. Dosya yalnızca `__Host-pararadar_session` değerini içermeli; Git dışında kalmalıdır. Anahtarları/çerezleri sohbete veya GitHub'a yapıştırmayın.

```sh
TIKTOK_LIVE_BASE_URL=https://GERCEK-BACKEND \
TIKTOK_LIVE_SESSION_FILE=/secure/private/session.txt \
python -m pytest tests/test_live_tiktok.py -q
```

Bu kontrol yalnızca hazır olma, oturum ve creator bilgisini okur; video göndermez. Gerçek uçtan uca taslak testi için açıkça onaylanmış dosyayla:

```sh
python scripts/sandbox_smoke.py \
  --base-url https://GERCEK-BACKEND \
  --session-file /secure/private/session.txt \
  --video /secure/private/test.mp4 \
  --request-key AYNI-ISTEK-ICIN-AYNI-BENZERSIZ-ANAHTAR \
  --mode inbox --confirm-upload
```

Özel Direct Post için `--mode private` kullanılır. Herkese açık paylaşım seçeneği bu araçta bulunmaz. Ağ hatasında yeni anahtarla körlemesine tekrar göndermeyin; kaydedilmiş işin durumunu kontrol edin.

## Gerçek demo kaydı

Canlı test başarılı olduktan sonra ekran kaydına alınacak akış:

1. ParaRadar'dan TikTok bağlantısı ve gerçek izin ekranı (parola/2FA/çerez/gizli anahtar gösterilmez).
2. Bağlanan hesabın görünür kullanıcı adı ve API'den alınan creator seçenekleri.
3. Dosya seçimi ve önizleme; düzenlenebilir başlık; elle seçilen görünürlük; etkileşim ve ticari içerik açıklaması.
4. Açık müzik/paylaşım onayı ve kullanıcı tarafından gönderim.
5. Gerçek API işlemi, durum takibi ve TikTok'ta özel video veya taslak bildirimi. İşleniyor durumunu başarı diye sunmayın.
6. Bağlantı kaldırma ve verilerin silinmesi; sunulan yetkilerin gerçek kullanımını açıklayın.

Masaüstü tarayıcısı olan güvenilir bir makinede mevcut oturumu kullanarak gerçek stüdyo akışını kaydetmek için:

```sh
python scripts/record_demo.py \
  --base-url https://GERCEK-BACKEND \
  --session-file /secure/private/session.txt \
  --output /secure/private/pararadar-review-video
```

Araç başlıksız otomasyon yapmaz; operatörün işlemlerini `.webm` olarak kaydeder. OAuth izin ekranının kaydı ayrıca, giriş bilgileri gösterilmeden alınmalıdır. Çıktı Git dışında, özel erişimde tutulur. Videoyu inceleyip hassas bilgiler varsa kırpın/maskeleyin, gerekirse `ffmpeg -i input.webm -c:v libx264 -pix_fmt yuv420p demo.mp4` ile dönüştürün. Portalın güncel biçim/süre koşullarını kontrol edin.

TikTok başvurusunu hesap sahibi portalda gerçek ürün açıklaması, doğru gizlilik/koşullar, doğrulanmış alan adı ve gerçek demo ile yapmalıdır. TikTok Direct Post kuralları yalnızca kişinin/kendi ekibinin hesaplarını yöneten dahili araçları inceleme için uygun kabul etmez. Ürünü olduğundan farklı tanıtan başvuru hazırlamayın; geniş creator kitlesine yönelik uygun ürün kapsamı ve TikTok onayı dış gereksinimlerdir.
