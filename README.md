# Jarvis - Gemini Masaüstü Asistanı

Jarvis, **Google AI Studio Gemini API** veya yerel **Ollama** ile sohbet eder;
bilgisayar işlemlerini ise modelin sözlerine güvenmek yerine doğrulanabilir araçlarla yapar.

## Başlatma

`Jarvis Başlat.vbs` dosyasına çift tıklayın. Sol paneldeki **Yapay Zekâ Ayarları**
düğmesine basıp Google AI Studio'dan aldığınız API anahtarını girin. Varsayılan
model `gemini-3.8-flash`'tır. Seçili model hesabınızda yoksa Jarvis, Google'dan
kullanılabilir modelleri sorgulayıp uygun olanı kendisi seçer ve kaydeder.
Yapay zekâ motoru ayarlardan seçilir. Ollama için Ollama çalışır durumda olmalı
ve seçtiğin model önceden indirilmelidir; varsayılan adres `http://localhost:11434`,
varsayılan model `qwen2.5-coder:3b` olur.

API anahtarı proje klasörüne veya Git'e yazılmaz; Windows kullanıcı uygulama
ayarlarında saklanır.

## Yapabildikleri

- Streaming sohbet ve SQLite ile kalıcı bellek
- PDF, DOCX, XLSX, PPTX, ODT, EPUB, TXT, kod, CSV ve JSON yükleyip bunlarla ilgili soru sorma;
  değişmeyen belge tekrar yüklenince metin önbellekten anında verilir; EPUB içeriği OPF sırasına göre
  okunur, DRM/şifreli bölümler reddedilir
- Çok kaynaklı Türkçe haber taraması, TCMB döviz kurları, Wikipedia arama/özet ve anlık hava durumu araçları;
  Wikipedia'da `search` ile başlık bulunur, özet bulunamazsa arama önerisi otomatik denenir;
  hava durumu üç günlük tahmin, UV ve gün doğumu/batımı verir; `city` ile başka şehir sorulabilir
  (Open-Meteo geocoding, 24 saat önbellek); hava, haber, Wikipedia ve TCMB verileri
  disk önbelleğiyle saklanır (hava 5 dk, haber 10 dk, kur 30 dk), ağ kesilirse son veri "önbellek" olarak gösterilir;
  haberler ayrıca ETag/Last-Modified ile koşullu indirilir; `query` ile başlık/özet içinde Türkçe duyarsız
  anahtar kelime filtresi (tüm kelimeler eşleşmeli), `sources` ile kaynak adına göre filtre uygulanır;
  gündem, ekonomi, teknoloji, spor, dünya, bilim, kültür-sanat ve sağlık kategorileri taranır; 15 akış
  aynı anda çekilir, ulaşılamayan kaynaklar sonuçta ayrıca bildirilir;
  aynı olayı anlatan başlıklar kelime kümesi karşılaştırmasıyla tek seferde gruplanır;
  haber anlatılırken sağ üstteki rozet animasyonla genişler ve haber fotoğraflarını kaynak/yayın zamanı
  bilgisiyle geçişli slayt olarak gösterir. Görseller RSS `media:content`/`enclosure` veya sayfadaki
  `og:image`/`twitter:image` etiketinden alınır; yalnızca anlık gösterim içindir, diske yazılmaz,
  boyut/tür denetimi ve özel ağ adreslerine karşı koruma uygulanır
- GitHub genel depolarında arama, künye/README, dosya ağacı, metin dosyası, sürüm notu (`releases`), son commit'ler (`commits`),
  tek commit ayrıntısı (`commit`; istatistik, değişen dosyalar ve sınırlı diff satırları)
  ve iki dal/etiket/SHA arası karşılaştırma (`compare`; ileri/geri sayısı, commit listesi, dosya farkları),
  açık/kapalı issue ve pull request listeleme (`issues`, `pulls`; durum, etiket, yazar, yorum sayısı,
  PR dal/merge bilgisi) okuma;
  lisans uyarısı gösterilir, lisanssız kod kopyalanmaz. `GITHUB_TOKEN` tanımlarsan saatlik API sınırı yükselir (zorunlu değil)
- Kalıcı kullanıcı notları ve Agent Skills uyumlu yerel yetenekler; Ayarlar → **Yetenekler** sekmesinden görülebilir.
  Komut paletinden **Kalıcı hafıza** ile notları görüntüleyip ekleyebilir/silebilirsin
- Komut paletinden **Görev günlüğü**: son ajan görevlerini, araç izlerini, durum ve özetleri gösterir; tek görevin izi panoya kopyalanabilir
- **Görev oturumu** (palet veya sohbet çubuğundaki duraklat düğmesi): ajanın her araç adımı sırasıyla kaydedilir;
  görev **duraklatılır**, kullanıcı işlemi **devralır**, sonra **sürdürür**. Oturum panelinden iz
  `%LOCALAPPDATA%\Jarvis\data\trajectories` altına Markdown + JSON olarak dışa aktarılır; tamamlanmış bir izden
  **yetenek olarak kaydet** ile tekrar kullanılabilir yetenek üretilir. Duraklatma araç çağrıları arasında etkilidir;
  anında kesmek için **Durdur** düğmesi kullanılır
- Ekran bağlamı: üst çubuktaki **EKRAN** düğmesi açıkken her komuta güncel ekran görüntüsü eklenir.
  "Ekranımda ne var?", "ekranda ne yazıyor" gibi sorularda düğme kapalı olsa da tek seferlik görüntü alınır.
  Görüntü seçili model sağlayıcısına gönderilir; ekrandaki yazılar talimat olarak uygulanmaz.
- YouTube aramasını veya bağlantısını Jarvis'in kendi tarayıcısında açma; ayrıca API anahtarı
  olmadan bir kanalın herkese açık RSS akışından **son videoları** veya **en çok izlenenleri**
  listeleme (`latest`/`highlights`, isteğe bağlı Shorts filtresi, kanal adı/@handle/UC bağlantısı)
- Wikimedia Commons'ta yalnızca **özgür lisanslı** (CC0, CC BY, kamu malı) fotoğraf arama;
  lisans, fotoğrafçı atfı ve dosya sayfası verilir; lisansı belirsiz dosyalar listelenmez,
  görsel indirilmez
- **Proaktif eşlikçi**: yaklaşan takvim etkinliği veya hatırlatıcı, su molası ve uzun oturum
  molası için nazik bildirimler; sessiz saatler, bildirim aralığı ve sesli bildirim
  Ayarlar → **Eşlikçi** sekmesinden ayarlanır
- **Tanılama ve gizlilik raporu** (komut paleti): sürüm, Python/PySide6, bileşen durumu,
  veri klasörü boyutları ve son başlatma hatası; API anahtarı, token, parola ve ortam
  değişkenleri rapora yazılmaz
- Sesli yanıt: çok dilli nöral ses (varsayılan Jarvis: derin, canlı ve hızlı). Ayarlar →
  **Ses & Kamera** sekmesinden ses karakteri (Jarvis/doğal Türkçe/Ava/William), ses düzeyi
  ve hoparlör/kulaklık çıkışı seçilir
- Kesintisiz sesli sohbet: mikrofon (F4) açıkken Jarvis sürekli dinler, yanıtı cümle cümle seslendirir
  ve konuşması bitince kendiliğinden dinlemeye döner. Kulaklık modunda Jarvis konuşurken sözünü kesebilirsin;
  normal hoparlörde mikrofon yankıyı önlemek için konuşma sırasında bekler
- Mikrofon girişi, hoparlör/kulaklık ve kamera seçimi Ayarlar → **Ses & Kamera** sekmesindedir;
  kamera paneli açıkken yüz HUD'u seçilen kamerayı kullanır
- Kamera algısı: sahne analizi kutuları yerel hareket takibiyle sürekli güncellenir
  (görünürken 1,2 sn'de bir bulut tazeleme, harekette 20 Hz blok eşleştirme ve kutu kaydırma);
  panel kapalıyken algı sürer, kamera ~2 fps'e düşer. Görünürken 30 fps, 720p tercih edilir;
  sahne görüntüsü yükleme öncesi 640 piksele küçültülür (yerel nesne HUD'u nesneleri zaten
  anında işaretlediği için bulut turu daha küçük kareyle hızlanır). HUD kutuları yeni taramaya
  sıçramadan yumuşak geçer, ağ isteği takılırsa 25 sn zaman aşımıyla tarama kilidi açılır
  ve ANALİZ ET tıklaması sıradaki taramaya eklenir
- Kamera tam ekran HUD: panelde boşluk/çift tık veya `F11`; `Esc`/`F11` ile çıkış,
  isteğe bağlı 3B derinlik ızgarası, ANALİZ/PANEL düğmeleri; küçük panelde alt yazı alanı yok
- Yerel yüz HUD'u (MediaPipe BlazeFace): kamera algısı açıkken her yeni karede algılanır
  (15 ms eşik, model ~14 ms); en büyük yüz "KİŞİ · ODAK" çerçevesiyle, diğerleri "KİŞİ · DİĞER"
  etiketiyle işaretlenir. Kutular EMA (0,94) ve önizlemede hızlı yay ile yumuşatılır; HUD kafa
  hareketini ~25 fps izler. Yüzler yalnızca yerel iş parçacığında çözülür; görüntü cihazdan
  çıkmaz, diske yazılmaz. Kimlik, yaş, duygu veya biyometrik tanıma yapılmaz. İlk kullanımda
  ~225 KB `blaze_face_short_range.tflite` modeli `%LOCALAPPDATA%\Jarvis\models` klasörüne
  indirilir (MediaPipe, Apache-2.0; model Google'dan); MediaPipe yoksa yüz HUD'u sessizce kapalı
  kalır. Model yalnızca kamera paneli/tam ekran ilk açıldığında yüklenir; arka plan kamera algısı
  uygulama açılışını kilitlemez
- Yerel el kontrolü (MediaPipe): 21 noktalı el iskeleti, açık el/yumruk/çimdik/işaret/zafer/başparmak
  jestleri ve QPainter ile çizilen 3B efektler (iskelet, parmak ateşi, dönen mühür, çimdikle
  gezegen, iki işaret parmağı arasında şimşek, yumruktan açık ele patlama). Panel ve tam ekrandaki
  **ELLER** düğmesi, palet komutu veya "el kontrolünü aç" sesli komutuyla başlar. Başparmak 1 sn →
  komut paleti, zafer 0.9 sn → tam ekran, yumruk 1.8 sn → algıyı durdur. Her yeni kare işlenir
  (15 ms eşik; 320 px'e yumuşak ölçekle inen model ~48 ms); iş parçacığına yalnızca en güncel kare
  bırakılır, eski kare birikmez; el ~20 fps algılanır, iskelet 60 Hz öngörülü yumuşatmayla akıcı
  çizilir. Düşük güvenli veya geometrisi bozuk tespitler elenir; ilk yakalamada jest
  efektleri iki karede onaylanır, el oturmuşsa tek kare yeter; gezegen için 0,2 sn çimdik gerekir
  ve serbest kalan gezegen 4,5 sn sonra kaybolur. Kareler yalnızca yerel iş parçacığında çözülür;
  cihazdan çıkmaz, diske yazılmaz. İlk kullanımda ~7,5 MB `hand_landmarker.task` modeli
  `%LOCALAPPDATA%\Jarvis\models` klasörüne indirilir (MediaPipe, Apache-2.0; model Google'dan)
- Yerel nesne HUD'u (MediaPipe EfficientDet-Lite0): kamera algısı açıkken nesneler ~0,18 sn'de bir
  (yaklaşık 5,4 fps) yerelde algılanır ve Türkçe etiketlerle (KİTAP, BARDAK, TELEFON vb.)
  işaretlenir. El kontrolü açıkken tarama ~0,36 sn'ye seyreltilerek CPU el iskeletine ve yüze
  bırakılır. Kutular bulut sahne analizini beklemez; bulut yanıtındaki çakışan kutular yerel
  katmanla eşleştirilip tekrarlanmaz. Kişileri yüz HUD'u işaretlediği için "person" sınıfı
  çizilmez. Görüntü cihazdan çıkmaz, diske yazılmaz. İlk kullanımda ~4,6 MB
  `efficientdet_lite0.tflite` modeli `%LOCALAPPDATA%\Jarvis\models` klasörüne indirilir
  (MediaPipe, Apache-2.0; model Google'dan); model yüklenemezse yalnızca bu katman kapalı kalır
- Akıllı ev paneli (simülasyon): salon ve çalışma odası ışıkları (aç/kapat, parlaklık,
  sıcak/nötr/soğuk ton), Türk kahvesi (köpük/şeker) ve filtre kahve (1–4 fincan) makineleri.
  Süreler gerçek zamanlı ilerler (Türk kahvesi ~75 sn, filtre fincan başına ~40 sn) ve panel
  kapalıyken de durum korunur. Paletten **Akıllı ev** ile veya "ev panelini aç" diyerek açılır;
  "salonun ışığını aç", "çalışma odasını yüzde otuz yap soğuk beyaz", "filtre kahveyi iki fincan
  demle", "kahvem hazır mı?", "tüm ışıkları kapat" gibi komutlar buluta gitmeden yerel çalışır.
  Ayrıntılı veya çok adımlı isteklerde model aynı durumu yöneten `home` aracını çağırır; durum,
  ışık/parlaklık/ton, kahve demleme ve iptal işlemleri tek yerden yürür. Cihazlar henüz gerçek
  Home Assistant donanımına bağlı değildir; panelde ve araç yanıtlarında simülasyon olduğu yazar.
- Beş arayüz paleti (Siyah, Gece, Kor, Zümrüt, Beyaz): Ayarlar → Temalar; seçim yeniden
  başlatınca uygulanır, önizleme kartları tema adını ve vurgu rengini gösterir
- Açılışta kare çekirdek ekranı: dönen Jarvis orb'u, tarama çizgisi, ilerleme şeridi ve
  aşama yazılarıyla 1,5 sn'lik animasyon; arayüz hazır olunca yumuşakça kapanır. Uzun
  görevlerde (komut, indirme, kurulum) başlığın altında küçük orb'lu durum rozeti görünür;
  mesaj balonları belirerek, sekmeler/paletler karartma ve kart sönümüyle açılıp kapanır,
  kamera paneli ve tam ekran, ayarlar penceresi ve uygulama kapanışı da fade ile geçer
- Yüzen HUD kartı: yumuşak gölgeli cam yüzey, durum renginde ince gradyan çerçeve ve nefes alan
  orb; durum rozeti (Dinliyor/Düşünüyor/Konuşuyor), mikrofon/kamera çipleri, konuşma veya kamera
  sahnesi alt yazısı ve rafine dalga formu; konum sürükleyip kenara yapışır, çift tık Jarvis'i açar
- Kısa ve güvenli komutlar yerel hızlı yoldan çalışır: panel açma ("haritayı aç", "ayarları göster")
  ve tek adımlı uygulama/klasör/dosya işlemleri bulut turu beklemez; canlı kamera karesi yalnızca
  görsel sorularda ("ne görüyorsun", "ekranda ne yazıyor") eklenir
- Gemini fonksiyon çağırma ile gerçek bilgisayar işlemleri: genel araçları çağırır,
  izin denetiminden sonra çalıştırır ve sonucu tam dosya yoluyla gösterir
- Uygulama adlarını önceden tanımlı bir katalogdan değil, Başlat Menüsü'ndeki
  kısayolları istek anında bularak açar
- Ekran görüntüsünü görsel modele göndererek UIA öğesi bulunmayan uygulamalarda
  koordinatla tıklama, sürükleme, kaydırma ve klavye kullanma
- Numaralı Set-of-Mark ekran haritası: UIA + Windows OCR hedefleri, 30 saniyelik tazelik denetimi,
  parola alanı reddi; hızlı modda UIA zenginse OCR atlanır, `fast=false` tam tarama yapar ve
  `timings_ms` her aşamanın süresini gösterir; ekran görüntüsü tek kez çözülür, pencere zaten öndeyse
  odaklanma beklemesi atlanır ve OCR motoru dil başına önbelleğe alınır
- `screen_wait`: ekran görüntüsü/OCR açmadan yalnızca UIA ile pencere başlığı veya arayüz metni bekleme;
  eylemden önce yeni `screen_map` alınması önerilir
- `window_control`: açık pencereleri listeler; öne getirme, küçültme, büyütme, geri yükleme,
  taşıma/boyutlandırma ve WM_CLOSE ile nazik kapatma; her işlem sonrası durum yeniden okunur,
  kritik süreçler ve UAC/güvenli masaüstü reddedilir
- Zengin UIA eylemleri: değer atama, temizleme, işaretleme/açma-kapama, genişletme, seçme,
  görünür alana kaydırma, fare tekerleği/üzerine gelme ve `sN:eM` hedefine sürükleme; sonuç
  arayüzden geri okunarak doğrulanır
- Pano aracı okuma/yazmanın yanında `clear` ile temizleme yapar; eylem sonrası ekran
  değişmediyse model adımına deterministik "görsel yansıma" uyarısı eklenir ve kör tekrar engellenir
- Proje klasörlerinde dosya ağacı, kod okuma/arama, unified diff önizlemesi,
  tek eşleşmeli güvenli düzenleme, unittest/pytest/npm/TypeScript çalıştırma ve zip üretme
- Testi geçen değişiklikleri ayrı bir çalışma kopyasından Jarvis'in kendisine uygulama;
  doğrudan öz-kod yazımı yoktur, her uygulama ayrıca onay ister
- SSRF korumalı `read_page` ve kaynaklı haber özeti için RSS/Atom okuma; başarılı okumalar
  2 dakika boyunca bellek önbelleğinden verilir ve önbellek yaşı belirtilir; web PDF bağlantıları
  pypdf ile metne çevrilir (ilk 40 sayfa, 40 bin karakter sınırı; taranmış/şifreli PDF reddedilir)
- Eksik Python paketini veya kullanıcı kapsamlı winget uygulamasını denetleyip onayla kurma
- İşlem isteklerinde modelin tarif verip bırakmasını engelleyen zorunlu araç adımı:
  ilk adım araç çağrısına zorlanır, "kendin yap" teslimiyeti yakalanıp işlem sürdürülür
- Steam Atölye sayfalarını `steam://openurl/...` ile açma; sonrasında arayüzü inceleyip
  abone olma/etkinleştirme adımlarını uygulama

Proje klasörlerini **Yapay Zekâ Ayarları → Ek proje klasörleri** bölümünden eklersen
Jarvis o klasörlerde coding agent araçlarını kullanabilir. Öz-geliştirme için asıl koda
dokunmadan `Belgeler\Jarvis_Projeler\Jarvis_Gelistirme_*` kopyası oluşturulur; uygulama
adımı başarılı ve güncel bir test kaydı olmadan çalışmaz.

Örnek çok adımlı istek:

```text
mehmet adında bir klasör oluştur. ve içine "akif" adında bir metin belgesi oluştur.
belgenin içeriğinde 1 kere "adana" yaz
```

Bu istek aşağıdakini oluşturur:

```text
Masaüstü\mehmet\akif.txt
```

Dosyanın içeriği `adana` olur. Dosya ve klasör işlemleri varsayılan olarak
Masaüstüne yapılır. Silme işlemi kalıcı silme yerine Geri Dönüşüm Kutusuna taşır.

## Güvenlik

Jarvis yalnızca izinli araçları kullanır; PowerShell/Python komutları ve uygulama
içi klavye/düğme işlemleri riskli seviyededir. Her işlem bir risk seviyesine ayrılır ve **Gemini Ayarları**
penceresindeki **Erişim modu** belirler ne olacağını:

| İşlem | Onaylı (varsayılan) | Adım adım | Tam erişim |
|---|---|---|---|
| Oluşturma (klasör, yeni dosya, uygulama açma) | otomatik | önce sorar | otomatik |
| Değiştirme (var olan dosyanın üzerine yazma, ekleme, taşıma, geri alma) | önce sorar | önce sorar | sormadan yapar |
| Silme, komut/test çalıştırma, kurulum, fare/uygulama içi işlem | önce sorar | önce sorar | sormadan yapar |
| Web okuduktan sonra değiştirme veya riskli işlem (ayar açıksa) | önce sorar | önce sorar | önce sorar |
| Jarvis'in kendi kodunu uygulama | önce sorar ve diff gösterir | önce sorar ve diff gösterir | **önce sorar ve diff gösterir** |
| Yasak işlemler | yapılmaz | yapılmaz | **yine yapılmaz** |

Tüm modlarda şunlar açık kalır:

- **Klasör sınırı:** yalnızca Masaüstü ve Belgeler (ve sonradan eklenen proje klasörleri). `..`,
  sembolik link, ağ yolu ve `:` ile klasör dışına çıkma denemeleri engellenir.
- **Yasaklar:** Jarvis'in kendi klasörü ve ayarları, Windows/Program Files/AppData, `.env`, `*.pem`,
  `*.key`, `id_rsa` gibi anahtar dosyaları, `.ssh` benzeri klasörler, `.exe/.bat/.ps1/.py` gibi
  çalıştırılabilir dosyaların açılması, Masaüstü/Belgeler klasörlerinin kendisini silme veya taşıma.
- **Geri alma:** değiştirilen dosyalar önceden yedeklenir. Sohbete `geri al` yazarsan son
  değişiklik geri döner (silinenler Geri Dönüşüm Kutusu'ndan geri yüklenir).
- **Günlük:** her işlem `%LOCALAPPDATA%\Jarvis\logs\audit.jsonl` dosyasına yazılır (dosya içeriği ve
  API anahtarı yazılmaz).
- **Duraklat / devral / sürdür:** çok adımlı görev araç çağrıları arasında duraklatılabilir; kullanıcı
  işlemi devraldığında ajan yeni araç çağırmaz. Onay, güvenlik ve günlük bu sırada da çalışmaya devam eder.
- **Proje talimatları:** çalışma klasöründeki `AGENTS.md`, `CLAUDE.md` ve `.qoder/rules/*.md` dosyaları
  düşük öncelikli not olarak sistem istemine eklenir; güvenlik kurallarını, onayları veya araç sınırlarını
  değiştiremez, bu sınırları kaldırmayı isteyen maddeler yok sayılır.
- **Durdur düğmesi:** çok adımlı bir işlemi bir sonraki adımda keser.
