# ParaRadar doğrulama raporu

2026-10-10 16:01 Türkiye saati

İlk tam video: 44,92 saniye; 720×1280, 25 FPS, H.264/yuv420p ve AAC. 10 kontrol geçti.

Yeni toplu üretim: **10/10 başarılı**. Her videoda 10 medya kontrolü.

| Video | Başlık | Süre (s) | Sonuç |
|---|---|---:|---|
| 01 | Uzun Vadeli Piyasa Bakışı | 47.160 | Geçti |
| 02 | Düzenli Yatırım ve Zamanlama | 51.240 | Geçti |
| 03 | Nominal ve Reel Getiri Nedir | 49.520 | Geçti |
| 04 | Düşüş Dönemlerinde Risk Yönetimi | 48.564 | Geçti |
| 05 | Çeşitlendirmenin Önemi | 48.080 | Geçti |
| 06 | Geçmiş Getiri ve Beklentiler | 50.320 | Geçti |
| 07 | Enflasyon ve Birikim Stratejisi | 49.363 | Geçti |
| 08 | Yatırım Süresi ve Riskler | 47.760 | Geçti |
| 09 | Bileşik Getiri ve Sabır | 48.291 | Geçti |
| 10 | Endeksleri Doğru Okumak | 46.760 | Geçti |

Üç regresyon testi geçti: siyah/sessiz/yanlış süreli videoyu reddetme; eşzamanlı kuyruk sınırı; devam ettirmede tamamlanan ve yarım dosyaları koruma.

Tüm MP4 dosyaları son kodla yeniden tarandı. Tüm kareler decode edildi; siyah bölüm bulunmadı. Ses seviyeleri ve AV süre farkı doğrulandı. Altyazı zaman çizelgesi ve üç örnek karede gömülü metin pikselleri doğrulandı.

Arka plan servisi Linux bulut ortamında çalışıyor. Windows kurucusu hedef Windows bilgisayarında henüz çalıştırılmadı. Mevcut Windows main.py/pararadar_hizli.py dosyalarına erişilemedi; dosya içindeki eski hatanın nedeni teşhis edilmiş değildir.

Türkçe ses eSpeak NG sentetik sesidir. Grafik gerçek aylık tarihsel S&P 500 verisidir; canlı fiyat değildir. Finans yorumları ve sesin anlaşılabilirliği insan incelemesi gerektirir.

TikTok yüklemesi yapılmadı. Eksik: TIKTOK_CLIENT_KEY ve TIKTOK_ACCESS_TOKEN. Pakette gizli anahtar bulunmaz.

GitHub paket aktarımına kullanıcı açıkça izin verdi. Paket ayrı bir dala aktarılır; hedef Windows bilgisayarındaki kurulum henüz çalıştırılmamıştır.
