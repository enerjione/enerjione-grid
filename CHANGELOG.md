# Değişiklik Günlüğü

Bu dosya **yayınlanan sürümleri** özetler. Biçim [Keep a Changelog](https://keepachangelog.com/tr/1.1.0/)
esaslıdır; sürümleme [SemVer](https://semver.org/lang/tr/).

Kayıt tutma kuralı: her `v*` tag'inden önce `[Yayınlanmamış]` başlığı altındaki
maddeler yeni sürüm başlığına taşınır. GitHub Release notları commit listesini
zaten otomatik üretir — buraya **kullanıcıyı etkileyen** değişiklikler yazılır,
her commit değil.

Türler: `Eklendi`, `Değişti`, `Düzeltildi`, `Kaldırıldı`, `Güvenlik`.

## [Yayınlanmamış]

---

## [2.63.0] — 2026-08-10

**2.62.0'daki kritik bir gerilemeyi giderir.** Ayrıca Pole Master Kit setleri
gerçek birer sanal cihaz gibi yönetiliyor ve alarm kuralları cihaz türüne göre
kurgulanabiliyor.

### Düzeltildi

- **Sinyal kataloğu uçları 500 dönüyordu; sonuçta hiçbir cihaz için alarm
  üretilmiyordu.** `SignalCatalogRead.source` alanı `master/sat01/sat02` ile
  sınırlıydı; Pole Master Kit'in `sat03`–`sat09` satırları yanıt doğrulamasını
  düşürdü. Sinyal seed'i açılışta koşulsuz çalıştığı için ortada hiç kit cihazı
  olmayan kurulumlar da etkilendi. Zincir:
  `/signals` + `/internal/signals` 500 → alarm-service kural önbelleğini hiç
  dolduramıyor (**alarm yok**) → tag-engine kataloğu boş kalıyor (her sinyal
  öncelikli hatta) → arayüzde Sinyaller sayfası, canlı değer sayaçları ve alarm
  kuralı sinyal seçici aynı anda boşalıyor. Telemetri yazımı etkilenmedi.
- **Alt cihazın herhangi bir ayarını güncellemek 409 veriyordu** — uç nokta
  çakışma kontrolü seti kendi kitiyle karşılaştırıyordu (muafiyet oluşturmada
  vardı, güncellemede yoktu).
- **`sat04`–`sat09` etiketleri ham görünüyordu** (alarm kuralları, cihaz özeti,
  harita ipucu, bildirim çanı). Etiket beş ayrı dosyada elle yazılmış sözlüklerden
  geliyordu; `Record<string, …>` oldukları için derleyici eksikliği yakalamıyordu.
  Artık tek kaynaktan, desenden üretiliyor.
- **Cihazı olan gateway hiç silinemiyordu** (silme özetinde "bilinmiyor" değeri
  toplanmaya çalışılıyor, istek 500 dönüyordu).
- Arıza şematiğinde: arızalı parça hiçbir iletkenin geçmediği merkez çizgiye ve
  faz çizgisinden daha kalın çiziliyordu; branşman girişindeki cihaz çizimden
  düşüp **aktif arıza kartı arızasız bir hat gösteriyordu**; branşman kolları
  çizim alanından taşıyordu; tekerlekle yakınlaşırken sayfa da kayıyordu.
- Alarm e-postaları Ölçüm, Eşik, Kaynak, Hat ve Bölge alanlarını sessizce
  kaybediyordu; alarm fırtınasında araya giren tek bir alarm eşleşmeyi bozuyordu.

### Eklendi

- **Alarm kurallarına cihaz türü kapsamı.** Listede "Cihaz Türü" sütunu; yeni
  kural akışında model seçimi sinyal seçiminden **önce** geliyor ve sinyal
  listesi o modele daralıyor. Sinyaller modeller arasında ortak değildir —
  kapsam olmadan, o modelde hiç tetiklenmeyecek kurallar yazılabiliyordu.
- **Setlerin uydu ataması düzenlenebilir.** Varsayılan 1‑2‑3 / 4‑5‑6 / 7‑8‑9 ama
  uyduları kelepçeyi takan kişi bağlar ve sıra kite göre değil direğe göre
  oluşur; her ünite için 1–9 seçilebilir. Aynı uydu iki sete atanamaz — atama
  bire bir olmazsa ikinci setin o ünitesi hiç veri almaz ve arayüzde set
  sağlıklı görünürdü.

### Değişti

- **Kit setleri cihaz listesinde kitin altında, daraltılabilir bir grup.**
  Haberleşme noktası ve IP gösterilmiyor (setin öyle bir ayarı yok); yerine
  uydu ataması yazıyor. Haberleşme sekmesi alt cihazda gizli.
- **Setlerin haberleşme durumu kitten devralınıyor.** Tek fiziksel bağlantı
  koptuğunda yalnızca telemetriyi en son alan set offline görünüyor, diğerleri
  saatlerce "online" kalıyordu.
- Cihaz detayında ünite listesi modele göre: kit setinde üç uydu (Satellite 03
  dahil), SN 2.0'da master + iki uydu. Önceden sette boş bir "Master" kartı
  çiziliyor, gerçek üçüncü ünite hiç görünmüyordu (pil rozeti ve seri no dahil).
- Alt cihaz görseli uydu fotoğrafı.

## [2.62.0] — 2026-08-10

Yeni cihaz modeli **Horstmann Pole Master Kit**, bileşen tabanlı e-posta
şablonları ve Arıza Analizi'ne ısı haritası + sağlık sekmeleri.

### Eklendi

- **Horstmann Pole Master Kit desteği.** Kit tek bir DNP3 outstation'dır ama
  üzerindeki 9 uydu üçerli setler hâlinde sahada birbirinden bağımsız
  noktalara kelepçelenir. Her set artık **ayrı bir cihaz** olarak eklenir:
  hatta ayrı yerleştirilir, arızası kendine düşer, kendi detay sayfası ve
  kendi IEC 104 / Modbus adresi olur. Cihaz eklerken "bağlı set sayısı"
  (1–3) sorulur; sayı sonradan artırılıp azaltılabilir — azaltma veri
  sildiği için silinecek setler adıyla listelenip onay istenir.
  Veri kaynağı tek: bölme telemetri hattında (tag-engine) yapılır, gateway
  yine tek cihaz görür.
- **"Pole Master" sekmesi.** Kit seviyesindeki ölçümler (modem, GPS, şebeke,
  solar/AC besleme, cihaz sıcaklığı) üç setin ortak varlığıdır; her setin
  ayrı bir sekmesinde gösterilir. Setin kendi ölçümleriyle aynı listede
  olsalardı hangi değerin sete hangisinin kite ait olduğu karışırdı.
- **Alarm kurallarına cihaz modeli kapsamı.** Sinyaller modeller arasında
  ortak değil: kitte `solar_power`/`ac_power` var, SN 2.0'da yok; SN 2.0'da
  `nominal_voltage` ve GPS alanları var, kitte yok. Bir model seçilirse kural
  yalnızca o modelde değerlendirilir ve sinyal listesi de o modele daralır.
  Boş bırakılan kurallar eskisi gibi tüm cihazlarda çalışır.
- **Arıza Analizi: Harita & Akış, Sistem Sağlığı, Cihaz Sağlığı sekmeleri.**
  Isı haritası, Bölge → Hat → Faz akış şeması (Sankey), en çok tetikleyen
  kurallar, haberleşmesi en çok kopan ve bataryası en hızlı tükenen cihazlar,
  gün içi sinyal profili (yerel saate çevrilmiş). Sekmeler yalnızca düzen
  değil performans kararı: harita ve sağlık sorguları ancak o sekme açılınca
  çalışır.
- **E-posta önizleme betiği** (`scripts/preview_emails.py`): yedi temsilî
  varyantı SMTP kurmadan tarayıcıda gösterir.

### Değişti

- **E-posta şablonları bileşen tabanlı yeniden yazıldı.** Ortak primitifler
  ve tek renk/ölçü token seti (`email_components.py`); şablonlar artık ham
  HTML yazmıyor. Görsel olarak: alarmın sayısı tablo satırı yerine sol renkli
  şeritli vurgu panelinde, gelen kutusu önizlemesinde "cihaz – ölçüm – zaman",
  koyu tema ve mobil yığılma desteği, Outlook'ta bozulmayan buton.
- **Bir Pole Master Kit lisans kotasından tek cihaz sayılır.** Setler kotadan
  düşmez — üç ayrı satır olmalarının nedeni lisanslama değil topolojidir.
- Sinyal kataloğunda anahtar tekilliği **model bazına** taşındı. Her Horstmann
  modelinin bir `master` ünitesi ve aynı adı taşıyan ama başka DNP3 adresine
  oturan sinyalleri var; global tekillik ikinci modelin bunları tanımlamasını
  imkânsız kılıyordu. Yan fayda: `sat01.overcurrent_tripped` için yazılmış bir
  alarm kuralı hem SN 2.0'da hem kit setinde çalışır.

### Düzeltildi

- **Alarm e-postaları Ölçüm, Eşik, Kaynak, Hat ve Bölge alanlarını sessizce
  kaybediyordu.** Zenginleştirilmiş veri "en son yayın bildirimi" satırından
  çekiliyordu; araya başka bir alarm girdiğinde eşleşme bozuluyor ve mailde
  yalnızca cihaz adı ile zaman kalıyordu. Tek bir arızanın üç faz alarmında
  üç mailin ikisi sakat gidiyordu. Sorgu artık alarmın kendisine çapalı.
- **Arızalı parça yanlış yerde çiziliyordu.** Tek-tel döneminden kalan ikinci
  bir çizim, arızalı parçayı hiçbir iletkenin geçmediği merkez çizgiye ve
  gerçek faz çizgisinden daha kalın koyuyordu; arızanın hangi fazda olduğu
  bilgisi görsel olarak siliniyordu.
- **Branşman girişindeki cihaz çizimden düşüyordu.** Kolun giriş segmenti
  "komşu direkler arası değil" sayılıp eleniyor, dolayısıyla "gördüm" diyen
  cihaz bulunamıyor ve **aktif bir arıza kartı tertemiz, arızasız bir hat
  gösteriyordu.** Cihaz artık dallanma direğine çiziliyor; gördüğü arıza kolun
  aşağısında olduğu için ana hatta kırmızı parça tanımlamıyor.
- **Branşman kolları çizim alanının dışına taşıyordu** — kırpılıyor ve
  kaydırarak dahi görülemiyordu. Ayrıca arıza bölgesinde iki kol varsa
  iletkenleri üst üste binip tek kol gibi okunuyordu; kollar artık çakışmadan
  kendi satırlarına yerleşiyor.
- **Şemada tekerlekle yakınlaşırken sayfa da kayıyordu.** React 18 `wheel`
  dinleyicisini passive bağladığı için `preventDefault()` etkisizdi.
- Cihaz eklerken Pole Master Kit seçilince artık **doğru cihaz görseli**
  gösteriliyor (eskiden her modelde Smart Navigator 2.0 fotoğrafı çıkıyordu).
- Arıza Analizi'nin yeni sekmeleri açılırken **çeviri yerine ham anahtar**
  görünüyordu; ısı haritası ipucu İngilizce'de "1 faults" diyordu; gün içi
  profil grafiğinin seri adları İngilizce arayüzde Türkçe kalıyordu.
- Isı haritası renk skalasının metin karşılığı ekran okuyuculardan gizliydi.
- **Cihazı olan bir gateway hiç silinemiyordu**: silme özeti toplanırken
  arşiv sayısı "bilinmiyor" değeriyle toplanmaya çalışılıyor ve istek 500
  dönüyordu. Tekil cihaz silme yolu bu toplamayı yapmadığı için fark
  edilmemişti.

## [2.61.0] — 2026-08-10

Hat arızası şematik çizimi baştan tasarlandı.

### Değişti

- **Kafes direk silueti.** Önceki çizim tek dikey çizgi ve düz bir traversti;
  şema değil taslak gibi duruyordu. Artık aşağı açılan iki ana ayak, X çapraz
  kafes dolgular, yatay kuşaklar, iki seviyeli travers ve travers uçlarında
  takviye çaprazları var. İzolatörler boncuklu zincir olarak çiziliyor.
- **Üç renkli iletken.** Her faz kendi izolatör noktasından geçiyor:
  L1 mavi, L2 yeşil, L3 turuncu — cihaz ekranındaki kaynak renkleriyle aynı.
  Üstte ince gri toprak teli.
  Kazanç görsel değil işlevsel: **arızalı parça artık hangi fazın telindeyse
  orada çiziliyor.** Alarmın faz bilgisi varsa yalnızca o iletken kırmızı
  yanıyor, diğerleri soluyor; faz bilinmiyorsa üçü birden vurgulanıyor. Tek
  gri çizgide bu bilgi görselden siliniyordu.
- **Branşman kolu çapraz iniyor.** Ana direğin traversinden ayrılıp dal
  katına eğik bir doğruyla iniyor — kesikli değil düz, çünkü iletken
  gerçekten oradan geçiyor. Arızayla ilgili kollar tam çiziliyor, ilgisizler
  soluk kalıyor.
- **Ölçü şeridi üste taşındı.** Aranacak hat kesimi operatörün ilk okuduğu
  sayı; altta dururken direk adlarıyla branşman etiketleri arasında
  kayboluyordu. Uzantı çizgileri traverse kadar iniyor.
- Şematik yüksekliği 248 → 300 birime, direk aralığı 116 → 132'ye çıktı.

### Düzeltildi

- **Çizim alanı sonsuza doğru aşağı kayıyordu.** viewBox yüksekliği
  kapsayıcının en-boy oranından türetiliyordu; kart uzadıkça görünüm
  penceresi de uzuyor, çizim yukarıda küçük kalıyor ve altında ucu bucağı
  görünmeyen boş bir alan açılıyordu. Pencere artık içeriğe bağlı,
  kapsayıcıya değil; kaydırma sınırları da tam kapatıldı — çizim dışına
  çıkılamıyor.
- **Cihaz seçilince harita uzaklaşıyordu**; hat ekrana sığacak şekilde
  çerçeveleniyor.

## [2.60.0] — 2026-08-10

Arıza Analizi ekranı çalışır hale geldi ve grafiklerle yeniden yazıldı.

### Düzeltildi

- **Arıza Analizi sayfası tamamen boş açılıyordu** ve üstte "Doğrulama hatası
  (fault_id)" yazıyordu. Sebep bir yol çakışmasıydı: `/faults/analytics` ucu
  `/faults/{fault_id}` deseninden **sonra** tanımlıydı. FastAPI yolları sırayla
  eşleştirdiği için istek parametreli uca düşüyor, `"analytics"` tam sayıya
  çevrilmeye çalışılıyordu. Uç hiçbir zaman çalışmamıştı.
- **`/faults/causes` ucu kimlik doğrulaması istemiyordu** — halka açıktı.
  Fark edilmemesinin sebebi yukarıdaki hataydı: istek hiç oraya ulaşmıyor,
  parametreli ucun yetki kontrolüne takılıyordu. Bir hata diğerini
  maskeliyordu. Yol sırası düzeltilince ortaya çıktı ve kapatıldı.

### Eklendi

- **Bölge dağılımı** kartı. Backend bu veriyi zaten üretiyordu ama ekran
  göstermiyordu — hesaplanıp atılıyordu. Hat sıralaması "hangi hat" der,
  bölge sıralaması "hangi ekibin sahası"; bakım planlamasında ayrı sorular.
- **Arıza Analizi üst menüde.** Mühendislik ağacında aramak yerine Anasayfa /
  Alarmlar / Hat Arızaları / Olaylar yanında. Operatör rolünde görünmez.
- **Branşman kolları şematik çizimde alt kat olarak.** Önceden kısa bir
  kesikli çizgi ve nokta idi; iki kol yan yana gelince etiketleri üst üste
  biniyor, kolun kendi direkleri hiç görünmüyordu. Artık kol kendi direkleri,
  teli ve adıyla çiziliyor; kolda arıza varsa kırmızı.

### Değişti

- **Grafikler echarts ile.** Sayfa elle çizilmiş SVG kullanıyordu; hover ve
  ipucu yoktu. Palet renk körlüğü (CVD) ayrım kontrolünden geçirildi —
  ilk denenen sırada turuncu ile yeşil komşu düşüp deuteranopia'da ayırt
  edilemiyordu (ΔE 7.3). Sıra testle kilitlendi.
- **Aylık eğilim en üstte, tam genişlikte** — önce zaman bağlamı, sıralamalar
  onun içinde okunuyor.
- **Cihaz ayarlarında faz eşleştirmesi L1/L2/L3 varsayılanıyla geliyor.**
  Üç alan da boş ("proje ayarını kullan") başlıyordu; pratikte doldurulmadığı
  için arızanın hangi fazda olduğu bilinmiyordu.
- Şematik çizim kapsayıcının tümünü dolduruyor; sürüklerken metin seçilmiyor.

## [2.59.0] — 2026-08-10

Hat Arızaları ekranının şematik görünümü. Arıza artık metinle anlatılmıyor,
**çiziliyor**: hangi açıklık, kaç metre, hangi faz, hangi branşman kolu.

### Eklendi

- **Arıza sekmeleri.** Birden fazla aktif arıza varken kartlar yatay
  kaydırılıyordu; kaydırma sırasında iki arıza aynı anda yarım görünüyor,
  hangisine bakıldığı belirsizleşiyordu. Artık her arıza kendi sekmesinde ve
  aynı anda yalnızca biri görünüyor. Kart sayfanın dibine kadar uzuyor.
- **Yakınlaştırılabilir şematik.** Tekerlek ile zoom (imlecin altındaki nokta
  sabit kalır), sürükleyerek gezinme, çift tık ya da "sığdır" ile sıfırlama.
  Uzun hatlar tek ekrana sığmıyordu; sabit ölçekte kaydırmak yerine odağı
  kullanıcı seçiyor.
- **Branşman kolları çizimde.** Dallanma direğinden inen kesikli dal, ucunda
  kolun adı. Kol arıza aralığının içindeyse kırmızı yanıyor; üzerine gelince
  kol adı, rolü ve direk sayısı görünüyor.
- **Derinlikli çizim.** Zemin düzlemi ufka doğru açılıyor, direklerin yan yüzü
  ve zemin gölgesi var, arka iletkenler daha soluk.

### Değişti

- **Şematik ölçek sabitlendi.** Çizim kapsayıcıya yayıldığı için ölçek hattın
  direk sayısına göre değişiyordu: 6 direkli hat devasa, 17 direkli hat
  minicik görünüyor, iki arıza kartı karşılaştırılamıyordu. Artık bir direk
  aralığı her hatta aynı genişlikte.
- **Direk etiketleri** sıra numarası yerine **direk adını** gösteriyor; saha
  ekibi direkleri adıyla tanıyor.
- "Arıza akımını GÖRDÜ" → "**Arıza GÖRÜLDÜ**" (FCI bir koruma rölesi değil),
  "belirsizlik aralığı" → "**Aranacak hat kesimi**".

### Düzeltildi

- **Arıza pini çizimin sol üst köşesine sıçrıyordu.** Pin `transform`
  attribute'u ile konumlanıyor, CSS animasyonu ise `transform` özelliğini
  yazıyordu; CSS özelliği presentation attribute'unu ezdiği için işaret her
  render'da (0,0)'a gidiyordu. Pin kaldırıldı — arızanın yeri kırmızı tel ve
  ölçü şeridiyle zaten işaretli.
- **Cihaz ipucu kart kenarından taşıp kırpılıyordu**; artık aşağı açılıyor.

## [2.58.0] — 2026-08-10

Arıza analiz katmanı. v2.56/2.57'de veri birikmeye başlamıştı; bu sürüm onu
**okunabilir** yapıyor.

### Eklendi

- **Arıza Analizi sayfası** (Mühendislik → İzleme). En çok arıza çıkaran
  hatlar ve bölgeler, ortalama çözüm süresi, tekrarlayan açıklıklar, sebep
  dağılımı, faz dağılımı ve aylık eğilim. Dönem seçilebilir (30 gün – 3 yıl).
- **Tekrarlayan açıklıklar** — aynı iki direk arasında birden fazla arıza.
  Bakım önceliklendirmesinin en doğrudan girdisi: bir açıklık yılda beş kez
  arıza yapıyorsa oradaki sorun kalıcıdır, tek tek müdahale yerine o
  açıklığı elden geçirmek gerekir.
- **Kural isabeti.** Cihaz verisinden türetilen sebep önerisi, sahanın
  girdiği etiketle ne kadar örtüşüyor — ve en sık hangi çiftte yanılıyor.
  Bir öğrenme katmanı eklemeden önce bilinmesi gereken sayı budur: düşükse
  önce kuralları düzeltmek gerekir, model eklemek isabetsizliği gizlemekten
  başka bir şey yapmaz.
- **Arıza bildirimi WhatsApp'a harita görseliyle düşüyor.** Metin tek başına
  "nerede" sorusunu tam cevaplamıyordu; koordinat linkini tıklayıp uygulama
  değiştirmek gerekiyordu. Ekip artık sohbetten çıkmadan konumu görüyor.
- **Arıza bölgesindeki branşman kolları gösteriliyor.** Hat tek bir zincir
  değil: dallanma direğine bağlı kollar ayrı birer hattır. Ana hattaki arıza
  aralığı bir dallanma direğini kapsıyorsa o kol da enerjisiz kalır — ama
  arızada hiçbir yerde görünmüyordu, yani o koldaki aboneler "etkilenmemiş"
  sanılıyordu.
- Arıza detayındaki harita seçilen odağa göre çerçeveleniyor.

### Not — sayıların dürüstlüğü

Bu ekran bakım bütçesini yönlendireceği için iki yerde bilinçli davranıldı:

- **Ortalama çözüm süresi yalnızca kapanmış arızalardan** hesaplanıyor.
  Devam eden bir arızayı "0 sürdü" saymak ortalamayı sistematik olarak aşağı
  çeker ve tabloyu olduğundan iyi gösterirdi.
- **Sebebi girilmiş arıza oranı gizlenmiyor.** Oran düşükse ekran uyarı
  basıyor: kayıtların yalnızca %5'i etiketliyken sebep dağılımına bakıp
  "en sık sebep ağaç teması" demek uydurma bir bulgudur. Etiketsiz kayıtlar
  ayrıca "bilinmiyor" dilimi yapılmıyor — veri eksikliğini bir bulgu gibi
  göstermek olurdu.

Analiz operatör için sorumluluk alanıyla sınırlıdır; görmediği hatların
arızaları toplam sayılara da girmez.

---

## [2.57.0] — 2026-08-10

v2.56.0 arıza analizinin veri temelini kurmuştu ama girilecek bir yer yoktu.
Bu sürüm onu kullanılabilir yapıyor.

### Eklendi

- **Saha ekibi arıza sebebini girebiliyor.** Arıza detayında katalogdan seçim
  (aileye göre gruplu: dış etken / ekipman / hava / işletme / bilinmiyor) +
  serbest ayrıntı alanı. Sebep **durumdan bağımsız** girilir: ekip arızayı
  kapatırken sebebi bilmeyebilir ya da kapattıktan sonra öğrenebilir.
  Yanlış seçilmişse boşa çekilerek geri alınabilir.
- **Kural önerisi gösteriliyor ama seçili gelmiyor.** Cihaz verisinden
  türetilen öneri ("dI/dt tetiklendi, aşırı akım yok → tipik ağaç teması")
  bir tık uzakta duruyor. Operatör onaylamadan bir etiket "girilmiş"
  sayılsaydı istatistik, kimsenin bakmadığı bir tahminle dolardı. İnsan
  etiketi ile kural önerisi ayrı kolonlarda — ikisini karşılaştırmak
  kuralların isabetini ölçen tek şey.
- **Ünite → faz eşleme formları.** Cihaz Yönetimi'nde istisna cihazlar için,
  Proje Ayarları'nda kurulumun genel konvansiyonu için. Boş seçim = "üst
  katmanı kullan"; zincir cihaz → proje → varsayılan. v2.56.0'da eşleme
  şemaya girmişti ama değiştirilecek yer yoktu; varsayılan dışında kurulmuş
  bir sahada faz etiketleri sessizce yanlış birikiyordu.

### Düzeltildi

- **Dışarı çıkan mesajlarda saat yanlıştı.** Saat 11:00'de oluşan bir alarmın
  WhatsApp mesajında 08:00 yazıyordu. Sistem zamanı her yerde UTC saklıyor ve
  bu doğru (sıralama, SLA ölçümü ve tekrar önleme buna dayanır); ama dışarı
  giden metni sahadaki insan okuyor ve duvar saatine bakıyor. Yerel saate
  çevirme tek kaynağa alındı.
- **Branşman kolunun ilk segmentine cihaz bağlanamıyordu.** Segment
  doğrulaması "iki direk de aynı hatta olmalı" diyordu; branşmanın ilk
  segmenti doğası gereği ana hattaki dallanma direği ile kolun ilk direğini,
  yani iki farklı hattın direklerini birleştirir.
- **Uydu katmanında çok yaklaşınca karoların üzerinde "Map data not yet
  available" yazıyordu.** Sağlayıcı, veri olmayan bir zoom için hata
  döndürmüyor — o metnin yazılı olduğu geçerli bir görseli başarılı yanıt
  olarak veriyor. Sağlayıcı zoom sınırı uygulandı.

### Güvenlik

- Faz eşlemesi **kimlik doğrulamalı** bir uçtan servis ediliyor.
  `GET /project-settings` bilinçli olarak halka açık (login ekranı logoyu
  oturum yokken çeker) ve oraya eklenen her alan anonim çağırana açılır. Faz
  eşlemesi marka değil şebeke yapılandırmasıdır; login ekranının ona ihtiyacı
  yok. Okuma şekli (public) ile yazma şekli ayrı tutuldu ve alanın public
  uçta olmadığı testle kilitlendi.

---

## [2.56.0] — 2026-08-10

Arıza analiz katmanının **veri temeli**. Bu sürümden sonra açılan her arıza,
kendi kanıtıyla birlikte kaydedilir — ileride kurulacak analiz ekranı ve
çıkarım katmanı bu birikimin üzerine oturacak.

> **Veri toplama bu sürümle başlıyor.** Sebep girişi ve faz eşleme formu bir
> sonraki sürümde gelecek; ama alarm imzası, faz, kural önerisi ve ölçüm
> anlık görüntüsü **şimdiden otomatik** kaydediliyor. Bugün başlamazsa geçen
> süre kalıcı olarak kayıptır: veri o an yazılmadıysa bir daha üretilemez.

### Eklendi

- **Arıza kaydına yapılandırılmış sebep alanı.** Katalogdan seçilir (19 kod,
  5 aile: dış etken / ekipman / hava / işletme / bilinmiyor). Serbest metin
  `note` ve yorumlar aynen kalıyor — bu alan onların *yanına* geliyor.
  Serbest metinden istatistik çıkmıyordu: aynı olay "ağaç değdi", "dal
  teması", "ağaçtan kaynaklı" diye on farklı yazılıyor.
- **Cihazın alarm imzası arıza kaydına yazılıyor.** Sebep çıkarımının asıl
  kaynağı bu: saha ekibinin yazdığı metin gecikmeli ve öznel, cihazın arıza
  anında hangi bayrakları kaldırdığı ise ölçülmüş veri.
- **Kural tabanlı sebep önerisi** (dil modeli gerektirmez):
  akım kaybı + aşırı akım yok → iletken kopması · sıcaklık alarmı + aşırı
  akım yok → aşırı yük · kurcalama → üçüncü şahıs · dI/dt + aşırı akım yok →
  yüksek empedans (tipik ağaç teması). Aşırı akımda sebep **üretilmiyor**:
  yıldırım, hayvan, izolatör, üçüncü şahıs hepsi aynı sonucu verir, birini
  seçmek analiz katmanını yanlış eğitirdi. Her öneri gerekçesini ve
  katkıda bulunan sinyalleri taşır.
- **Arıza kalıcılığı artık sahaya sorulmuyor** — cihaz zaten söylüyor
  (`permanent_fault` / `momentary_fault`).
- **Faz çıkarımı.** `master` / `sat01` / `sat02` üç ayrı faza kelepçelenir,
  yani kaynak öneki "hangi ünite" değil **hangi faz** demek. Tek ünite
  gördüyse tek faz-toprak (çoğunlukla dış etken), üçü birden gördüyse üç faz
  (ekipman ya da aşırı yük). Bu ayrım sebep çıkarımının belirleyici girdisi.
- **Ünite → faz eşlemesi hem cihaz hem proje düzeyinde ayarlanabiliyor.**
  Çözüm zinciri: cihaz → proje → kod varsayılanı (`a`/`b`/`c`). Kelepçeyi
  hangi faza takacağına sahadaki kişi karar verir ve bu cihazdan cihaza
  değişebilir; proje katmanı kurulumun genel konvansiyonunu, cihaz katmanı
  istisnaları taşır. Kısmi doldurma destekli.
- **Ölçüm anlık görüntüsü.** Arıza akımı (üç fazın en büyüğü), yük akımı,
  iletken sıcaklığı ve arıza sayaçları kaydın kendisine yazılıyor. Bilinçli
  denormalizasyon: ham telemetri 90 günde düşüyor, saatlik özetten belirli
  bir arızanın tepe akımını geri çıkarmak kayıplı.
- **Arızayı doğuran alarmlar arayüzde** — "bu arıza neden açıldı"
  sorusunun cevabı arıza kartından görülebiliyor.
- Bildirim metinleri faz ve hat bağlamını içeriyor.

### Değişti

- Arıza bölgesi çizimi hangi fazın arızalı olduğunu gösteriyor.

### Not

Kural çıktısı (`auto_cause_code`) ile insanın gireceği etiket (`cause_code`)
**ayrı** tutuluyor. İkisini karşılaştırmak kuralların isabetini ölçer; bir
öğrenme katmanı eklemeden önce bilinmesi gereken şey tam olarak budur.

Faz eşlemesi varsayılandan farklı kurulmuş bir sahada geçmişe dönük
düzeltilebilir: `trigger_signals` ham sinyal anahtarlarını (kaynak öneki
dahil) sakladığı için faz, eşleme düzeltildikten sonra yeniden hesaplanabilir.

---

## [2.55.0] — 2026-08-10

> ### ⚠️ Yükseltmeden önce: SCADA'sı Modbus'tan besleniyorsa okuyun
>
> Akım ve açı sinyallerinin **Modbus ölçek katsayısı değişti** (`0.001 → 0.1`).
> Bu bir düzeltme: eski katsayıyla int16 tavanı **32.767 A** idi, yani bir
> dağıtım fiderinin normal yükü bile tavana kilitleniyor ve SCADA sonsuza dek
> aynı sayıyı okuyordu (arıza akımları her zaman 32767).
>
> **Yapılması gereken:** Dış Sistemler → Modbus hedefi → *Adres Planı*'nı açın.
> `⤴` işaretli satırlar ölçeği değişen sinyallerdir; SCADA tarafındaki
> katsayıyı tablodaki (veya CSV'deki) yeni değerle güncelleyin. Tam çözünürlük
> istiyorsanız hedefi `float32` formatına alın — o modda ölçek yoktur,
> mühendislik birimi doğrudan okunur.
>
> Etkilenmeyen kurulumlar: Modbus hedefi tanımlı olmayanlar ve zaten `float32`
> kullananlar.

### Düzeltildi

- **DNP3 Master Adres'i boş gönderilen cihazlarda haberleşme kesiliyordu.**
  Gateway yapılandırması ham `dnp3_extended` sözlüğünü okuyordu; v2.54.1
  penceresinde diske `null` yazılmış cihazlarda alan boş gidiyor, gateway
  kendi varsayılanına (`DNP3_LOCAL_ADDRESS=1`) düşüyor ve cihaz 100
  beklediği için çerçeveleri sessizce atıyordu. Artık eksik/`null` kayıtlar
  varsayılana (100) iyileşiyor.
- **Cihaz silinemiyordu.** Silme tek transaction içinde o cihaza ait tüm
  `telemetry_history` satırlarını temizliyordu — 90 günlük pencerede cihaz
  başına ~4M satır. İstek dakikalarca açık kalıyor, arayüz zaman aşımına
  uğruyordu. Silme iki faza ayrıldı: senkron kısım (ilişkili küçük kayıtlar +
  cihaz satırı) milisaniyeler içinde biter ve cihaz arayüzden hemen kaybolur;
  arşiv temizliği arka plan kuyruğunda yapılır.
- **Hat arızası bildirimi hiçbir kanaldan gitmiyordu.** Arıza açıldı, e-posta
  ve WhatsApp grubu açıktı, hiçbiri gelmedi. Tek bir hata değil, zincir
  kopukluğu: (1) arıza motoru gönderim yapmaz — satır içi gönderim
  `notification_inline_dispatch_enabled` bayrağına bağlı ve bayrak
  production'da **varsayılan kapalı** (SMTP'yi arıza motorunun içinde
  koştururken arıza kaydı commit edilmeden asılı kalıyordu); (2) gönderimi
  tetikleyen tek yer notification-worker'ın **alarm** yoluydu; (3) arıza
  kaydını açan `recompute_faults_debounced` hesaplamayı sonraki tetiğe
  bırakabiliyor. Sonuç: alarmın dispatch'i arıza satırı **henüz yokken**
  koşuyor, mesaj "işlenmiş" damgalanıyor, debounce arızayı sonra açıyor ve
  onu gönderecek kimse kalmıyordu — **tekil arızada bildirim hiç
  gitmiyordu.** Artık alarm akışından bağımsız bir süpürücü bekleyen
  arızaları tarıyor; ayrıca tekrarlanan alarm mesajı ve alarm gönderim
  hatası da arıza gönderimini engellemiyor.
- **Kullanıcı şifresini değiştiremiyordu.** Profil modalı tek "Kaydet" ile
  önce profili, sonra şifreyi kaydediyordu; profil çağrısı patlarsa (geçersiz
  e-posta → 422, başkasında kayıtlı → 409) şifre çağrısı **hiç
  yapılmıyordu**. Ayrıca yalnızca "yeni şifre" doldurulduğunda hiçbir çağrı
  yapılmıyor, modal kaydedilmiş gibi kapanıyordu — kullanıcı şifresini
  değiştirdiğini sanıyordu. API yardımcıları da sabit metin fırlattığı için
  backend'in söylediği sebep ("Mevcut şifre yanlış", "Yeni şifre eskisiyle
  aynı olamaz", hız sınırı) ekrana hiç ulaşmıyordu.

- **Modbus'ta akım değerleri 32.767 A'de kilitleniyordu.** Register ölçeği
  olarak sinyal kataloğundaki katsayı kullanılıyordu; ama o katsayı DNP3
  **çözme** katsayısıdır ve cihazın ham birimini anlatır (akımlar için mA).
  Kodlayıcı tersini uygulayınca register'a mA yazılıyor ve `0.001` ölçeğinin
  int16 tavanı **32.767 A** oluyordu. Bir dağıtım fideri rahatça 100–600 A
  taşır, arıza akımı kA mertebesindedir — bu tavanın üstündeki her değer
  32767'ye kilitleniyor, SCADA sonsuza dek aynı sayıyı okuyordu. Belirti
  sinsiydi: değer "makul" görünür, sadece **hiç değişmez**. Ölçek artık
  int16 tavanına sığacak şekilde ondalık basamak atlanarak genişletiliyor
  (akım `0.001 → 0.1`, tavan 3276.7 A). Adres planı ekranı genişletilen
  sinyalleri `⤴` ile işaretliyor. **SCADA tarafında bu sinyaller için
  katsayının tablodaki yeni değerle güncellenmesi gerekir.**
- **Kapasiteye sığmayan cihazların uyarısı hiçbir yere ulaşmıyordu.**
  Adres planı servisi "N cihaz plana alınmadı" bilgisini üretiyordu ama
  API şemasında karşılık gelen alan yoktu; pydantic fazla anahtarları
  sessizce düşürdüğü için uyarı ne arayüze ne worker'a gidiyordu. Arayüz
  `remaining: 0` değerini "tam" diye okuyor, o cihazlar SCADA'ya hiç
  yayınlanmadığı halde operatör onları "sakin" sanıyordu.
- **`unit` modunda Modbus bit adresleri cihaz başına kayıyordu.** Her cihaz
  kendi slave id'sinde olduğu halde register'lar 0'dan, bitler
  `cihaz_sırası × 100`'den başlıyordu; SCADA'da bit eşlemesi tutmuyordu.
- **MQTT topic'i ilk turdan sonra susuyordu.** Yayıncı "değer değişmediyse
  gönderme" yapıyor; saha sinyallerinin çoğu sabit olduğu için (seri no,
  firmware, eşik değerleri, normal durumdaki arıza bayrakları) servis
  açıldıktan sonra her şey bir kez yayınlanıyor, ardından topic sessizleşiyordu.
  `retain` de varsayılan kapalı olduğundan **sonradan abone olan istemci
  hiçbir şey görmüyordu.** Değişim anında yayın korunuyor; ek olarak her
  sinyal en geç 5 dakikada bir tazeleniyor.
- **Operatörün girdiği MQTT topic'i sessizce yok sayılıyordu.** Çözücü,
  belgelenmiş olmasına rağmen `topic` alanını hiç okumuyor, yayını şablondan
  üretilen başka bir topic'e gönderiyordu. "Otomatik Topic'ler" önizlemesi de
  artık gerçekte yayınlanan topic'i gösteriyor.
- **MQTT "Bağlı" rozeti broker düştükten sonra da bağlı gösteriyordu.**
  Durum yalnızca ilk bağlantıda yazılıyordu; artık bağlan/kop geri
  çağrılarıyla izleniyor.
- **Olay kayıtlarının yarısı ham (çoğu İngilizce) metinle görünüyordu.**
  188 `record_event` çağrısının 90'ında Türkçe şablon anahtarı yoktu; Olaylar
  ekranı ve PDF/Excel dışa aktarımı "Alarm … acknowledged", "Signal updated",
  "API key created", "Gateway … batch processed" gibi satırlar basıyordu.
  Alarm yaşam döngüsü, sinyal kataloğu, API anahtarları, lisans, yedekleme ve
  davet olaylarına Türkçe/İngilizce şablon eklendi. Ayrıca `ftp`, `backup` ve
  `license` kategorileri eşlemede olmadığı için "Ftp/Backup/License" diye
  görünüyordu.
- **Bildirimler "Horstman" adıyla gidiyordu.** Horstmann izlenen **cihazın**
  üreticisi, bu yazılımın adı değil; müşteri test e-postasını yanlış
  göndericiden gelmiş sanıyordu. SMTP/SMS/Telegram testleri ve alarm bildirimi
  konusu artık kurulumun kendi adını (Proje Ayarları), o da boşsa "EnerjiOne
  Grid" kullanıyor. Giden e-postalarda **görünen gönderen adı hiç yoktu**
  (yalnızca `noreply@…` adresi) — eklendi.

### Değişti

- **Hat Arızaları sayfasındaki arıza bölgesi çizimi yenilendi.** Önceden
  direkler dizilip aralarına düz bir taban çizgisi çekiliyordu ve **cihazlar
  çizimde hiç yoktu** — "arıza şu iki cihaz arasında" bilgisi yalnızca
  metinle anlatılıyordu. Artık gerçek bir havai hat kesiti var: direkler
  travers + izolatörlü siluet, iletkenler direkler arasında **sarkarak**
  (katener) geçiyor, cihazlar **telin üzerinde** segmentteki gerçek
  konumlarında duruyor ve **arızalı tel parçası** son "gördüm" diyen cihaz
  ile ilk "görmedim" diyen cihaz arasında kırmızı çiziliyor. Yeşil cihaz
  yoksa arıza hat ucuna kadar sürer. Geometri React'ten ayrıldı ve testlerle
  korunuyor.
- **Profil ayarları artık modal değil, ayrı bir sayfa.** Modal sekme
  sisteminde yer almadığı için sayfa yenilenince kayboluyor ve geri tuşu
  çalışmıyordu. Sayfa üç bağımsız kart: kimlik bilgileri, şifre ve bildirim
  tercihleri — her birinin kendi kaydet düğmesi var, birinin hatası diğerini
  bloklamıyor. Şifre bölümünde "yeni şifre (tekrar)" alanı eklendi.
- **Yedek ve Uzaktan Erişim sayfalarının üst şeridi** diğer mühendislik
  sekmeleriyle aynı dile getirildi (Ağ Ayarları / Güvenlik Duvarı'ndaki tek
  satırlık durum şeridi). Uzaktan Erişim'deki başlık + açıklama paragrafı +
  madde listesinden oluşan büyük blok ve Yedek'te normal durumda bile duran
  dolu renkli uyarı bandı kaldırıldı; bilgi kaybı yok, "ne olur" açıklaması
  zaten alttaki kartta. Renkli bant artık yalnızca gerçek sorunu bildiriyor.
- Proje Ayarları'ndaki proje adı / müşteri adı / sekme başlığı
  örneklerinden müşteri adı çıkarıldı.

---

## [2.54.3] — 2026-08-07

### Düzeltildi

- **v2.54.1'de Master Adres varsayılanını yanlışlıkla kaldırmıştım — geri
  alındı.** Cihazın (Horstmann SN2) kendi ayarında `Master Address = 100`
  yazıyor; yani sistemin gönderdiği 100 **doğruydu**. Alanı boş bırakmak
  gateway'in kendi varsayılanını (1) kullanmasına yol açar ve cihaz 100
  beklediği için haberleşmeyi **keser**. Form yeniden 100 ile geliyor —
  amaç: cihazda hiçbir ayar yapmadan IP + port + Outstation ID girip cihaz
  eklenebilmesi. (Şemada alanın opsiyonel kalması ayrı bir konu: kullanıcının
  hiç göndermediği alanı kayıt sırasında uydurup diske sabitlememek için —
  bu davranış v2.54.1'den beri korunuyor.)

---

## [2.54.2] — 2026-08-07

### Düzeltildi

- **v2.54.1'deki imaj etiketi düzeltmesi yarım kalmıştı.** `image` güncelleme
  parametreleri kabul listesine eklenmişti ama doğrulama fonksiyonu onu çıktıya
  **kopyalamıyordu**; değer sessizce düşüyor, ajan yine compose'daki eski
  etiketi geri yazıyordu. Yani sabit etikete (`:1.5.0`) kilitlenmiş kurulum
  hâlâ düzelmiyordu. Artık gerçekten uygulanıyor.
- **Zaman bombası test**: `test_device_event_time` sabit bir tarih (2026-07-31)
  kullanıyordu ve saat değerlendirmesi 7 günden eski damgayı "invalid" saydığı
  için test, o tarihten tam 7 gün sonra (bugün 12:00 UTC) hiçbir kod
  değişmeden kendiliğinden kırıldı. Damga artık "şimdi"ye göre üretiliyor.

---

## [2.54.1] — 2026-08-07

### Düzeltildi

- **ASIL ARIZA — `master_address` varsayılanı haberleşmeyi kesiyordu.** Bu alanın
  varsayılanı 100'dü ve kayıt akışı **tüm** DNP3 ek ayarlarını somutlaştırdığı
  için, operatör ilgisiz bir alanı (örn. TCP portu) değiştirip kaydettiğinde
  master_address diske 100 olarak yazılıyor, gateway o cihaza artık 100
  adresiyle konuşuyordu (gateway'in kendi varsayılanı 1). DNP3 cihazları
  beklemedikleri master adresinden gelen isteği **sessizce atar**: TCP bağlanır,
  cihaz hiç cevap vermez — sahada `link_open → 15sn fresh frame yok → lost →
  forced_relink` döngüsü olarak görüldü. Simülatör master adresini
  doğrulamadığı için bu hata simülasyon testlerinde **görünmüyordu**.
  Alan artık opsiyonel (boş = gateway kendi varsayılanını kullanır) ve
  **yalnızca operatörün açıkça girdiği alanlar diske yazılıyor** — aynı sessiz
  yazma riski `unsolicited_*`, `validate_source_address`, `session_timeout_*`
  için de kapatıldı. Arayüze uyarı metni eklendi.
- **Güncelleme gateway ayarlarını siliyordu.** `DNP3_EVENT_SCAN_INTERVAL_SEC` ve
  `INSTALL_MODE` compose şablonlarında yoktu; ajan güncellemede compose'u kendi
  şablonundan yeniden ürettiği için bu ayarlar her güncellemede siliniyordu.
  Scan aralığı poll aralığına (1 sn) düşüyor, cihaz başına saniyede bir DNP3
  isteği üretiliyordu (401 cihazda CPU %108) — yani bir güncelleme, çözülmüş
  bir performans sorununu geri getiriyordu.
- **Sabit imaj etiketi güncellemeyi kalıcı kilitliyordu.** Compose'a bir kez
  sabit etiket (`:1.5.0`) yazıldığında "Güncelle" butonu onu bir daha
  değiştiremiyordu; ekran kalıcı olarak "Güncel" diyor, yeni sürümler hiç
  görünmüyordu. Güncelleme artık etiketi `:latest`e normalleştiriyor ve
  compose her kalkışta imajı yeniden çekiyor (`pull_policy: always`).
- **Kurulum çıktısı yanlış şifre gösteriyordu.** Kurulum her seferinde rastgele
  şifre üretiyor ama özet ekranı sabit `ChangeMe123!` yazıyordu; kurulumcu
  giriş yapamıyordu. Artık gerçek üretilen şifre gösteriliyor (hesap zaten
  varsa "parolanız değiştirilmedi" denir, uydurulmaz). `seed_installer`
  içindeki "bu parola sabittir" uyarısı da güncellendi.

### Değişti

- Poll havuzu (`MAX_PARALLEL_DEVICES`) cihaz sayısına göre ölçekleniyor
  (taban 50, tavan 1000, %20 pay). Sabit 500'de cihaz sayısı bu değere eşit
  olduğunda pay kalmıyor ve yavaş cihazlar slotu tutunca diğerlerine o turda
  hiç istek gidemiyordu.

---

## [2.54.0] — 2026-08-07

### Kaldırıldı

- **Otomatik seri numarası eşleştirme kaldırıldı.** Cihaz bağlandığında
  `master.serial_number` telemetrisinden `serial_number` alanını otomatik
  güncelleyen mekanizma (`_seri_ve_kod_senkronu`) tamamen çıkarıldı. Bugünkü
  olaylar zincirinden (v2.53.31'deki kod otomatik değişimi) sonra karar
  verildi: cihaz kimliğiyle ilgili hiçbir alan artık otomatik/telemetri
  kaynaklı değişmeyecek — Seri No, Cihaz Kodu gibi, yeniden tamamen elle
  girilen/düzenlenen bir alan. Cihaz ekleme formunda ayrı bir "Seri No"
  alanı geri geldi; düzenleme ekranında da yine serbestçe değiştirilebilir.

---

## [2.53.36] — 2026-08-07

### Eklendi

- **Cihaz haberleşme kaybı artık bildirim üretiyor.** Denetim sırasında
  ortaya çıktı: bir cihaz haberleşmeyi kestiğinde (gateway `comm_lost`
  kalitesi basınca) CRITICAL seviyede "Haberleşme arızası" alarmı üretecek
  kod (`_QualityState`, `_build_quality_alarm`) önceden yazılmıştı ama
  hiçbir yerden çağrılmıyordu — sistem sessizce hiçbir SMS/Telegram/e-posta
  göndermiyordu. Artık ilk kötü kaliteli okumada bu alarm otomatik açılıyor
  (mevcut bildirim hattı üzerinden — kim aboneyse ulaşır), cihaz aynı
  anda birden çok mesajla haberleşmeyi kesse de tek alarm üretilir (spam
  yok), haberleşme geri gelince otomatik "normale döndü" olarak kapanır.

---

## [2.53.35] — 2026-08-07

### Düzeltildi

- Yeni Cihaz Ekle formunda Cihaz Kodu altındaki açıklama satırı kaldırıldı —
  form sadeleşti, gereksiz metin kalabalığı gitti.

---

## [2.53.34] — 2026-08-07

### Değişti

- **Cihaz Kodu ve Seri No yeniden bağımsız iki alan** (v2.53.31'in "tek
  kimlik alanı" birleştirmesi geri alındı). Cihaz Kodu operatörün serbestçe
  seçtiği, sistemin yönlendirme anahtarıdır (ingest/gateway/outbound hep
  bununla çalışır) — cihaz eklerken elle girilir, sonradan değişmez. Seri
  No artık **cihaz eklerken hiç girilmez** — yalnızca cihaz bağlandığında
  telemetriden otomatik okunur ve düzenleme ekranında salt okunur gösterilir.
  Kod ile serinin farklı olması artık normal kabul edilir; bu yüzden
  "uyuşmazlık" uyarısı da kaldırıldı (kod hiçbir zaman otomatik değişmediği
  için gerek kalmadı — bkz. v2.53.32).

---

## [2.53.33] — 2026-08-07

### Düzeltildi

- **Modbus yayınında değerler görünmüyordu — kalite filtresi yanlış tasarlanmıştı.**
  Modbus protokolünde kalite biti diye bir kavram yok; buna rağmen worker
  "kötü kaliteli" işaretlenen ölçümleri register'a hiç yazmıyordu. Bir
  sinyal hiç "iyi kaliteli" gelmezse (sahada görüldü: 10K+ mesajın 6K+'si
  sürekli kötü kaliteliydi) register sonsuza dek varsayılan 0'da kalıyor,
  SCADA "ölçüm yok" görüyordu — Canlı Değerler ekranı aynı anda doğru
  değeri gösteriyor olsa bile. Artık kalite ne olursa olsun **o an gelen
  değer yazılır**, Canlı Değerler ile birebir aynı davranış; kalite yalnızca
  ayrı bir sayaçla izlenir, yazmayı engellemez.
- **İkinci, sessiz bir hata daha vardı**: bazı sinyallerde sayısal değer
  `value` alanında değil `value_string`'te geliyordu; worker yalnızca
  `value`'ya baktığı için bu ölçümler hiçbir zaman register'a dönmüyordu
  (ve hiçbir sayaçta görünmüyordu). Artık `value` boşsa `value_string`'e
  bakılıyor; ayrıca eşleşme/kalite sorunu olmadığı hâlde sayıya çevrilemeyen
  ölçümler için yeni bir "çevrilemeyen ölçüm" sayacı eklendi (Modbus Yayın
  Durumu penceresinde görünür) — bundan sonra bu durum SESSİZ kalmayacak.

---

## [2.53.32] — 2026-08-07 — ACİL DÜZELTME

### Düzeltildi

- **v2.53.31'deki otomatik "kod düzeltme" gerçek bir cihazın haberleşmesini
  kesti — geri alındı.** Cihaz bağlandığında `device.code`'u otomatik olarak
  gerçek seriye çeken özellik, sahada canlı bir cihazı "haberleşmiyor"
  durumuna soktu. Sebep: telemetri işleyici cihazı her toplu işte `code`
  üzerinden buluyor; kod DB'de değiştiği anda gateway hâlâ ESKİ kodla
  yayın yapmaya devam ediyor (kendi config'ini ne zaman yenileyeceği
  garanti değil) ve o aradaki paketler "bilinmeyen cihaz" sayılıp sessizce
  düşüyor — cihaz fiziksel olarak konuşuyor ama sistem görmüyor.
  **Kod artık asla otomatik değiştirilmiyor.** Seri numarası senkronu
  (config dosya adı için) kalıyor; kod uyuşmazlığı yalnızca bir kez bilgi
  amaçlı uyarı olayı + cihaz kartında görünür uyarı olarak işaretleniyor.
  Etkilenen cihaz için: gateway servisini yeniden başlatmak (config'i
  sıfırdan çekmesini sağlar) haberleşmeyi geri getirmeli.

---

## [2.53.31] — 2026-08-07

### Değişti

- **Cihaz kimliği = Seri No**: cihaz eklerken tek kimlik alanı kaldı — cihazın
  fabrika seri numarası girilir ve sistem kimliği (cihaz kodu) olarak
  kaydedilir; formdaki ikinci "Seri No" alanı kaldırıldı. Yanlış girilirse
  cihaz bağlandığında bildirdiği gerçek seri ile **kod otomatik düzeltilir**
  (gateway ~1 sn'de yeni kodu çeker; telemetri geçmişi cihaz id'siyle
  anahtarlı olduğundan kopmaz). Aynı kodda başka cihaz varsa dokunulmaz,
  bir kez uyarı olayı düşülür ve cihaz kartında uyuşmazlık uyarısı görünür.

---

## [2.53.30] — 2026-08-07

### Düzeltildi

- **Cihaz formunda çift "Seri No"**: cihaz kodu alanının etiketi yanlışlıkla
  "Seri No" kalmıştı; gerçek seri numarası alanı eklenince form iki "Seri No"
  gösteriyordu. Kod alanı yeniden "Cihaz Kodu" oldu — üstteki sistemin
  değişmez cihaz anahtarı, alttaki cihazın bildirdiği gerçek seri numarası.

---

## [2.53.29] — 2026-08-07

### Eklendi

- **Modbus yayın durumu paneli**: SCADA Çıkışları tablosunda Modbus hedefleri
  artık canlı durum rozeti gösterir (çalışıyor / veri akışı yok / kapalı) ve
  rozete tıklayınca sayaçlarla teşhis penceresi açılır: NATS'tan işlenen
  telemetri, register'lara yazılan güncelleme, adres planıyla eşleşmeyen
  telemetri, cevaplanan SCADA isteği. Pencere sayaçlardan tek cümlelik teşhis
  üretir — "değer neden görünmüyor" sorusunun cevabı artık ekranda
  (worker kapalı / telemetri yok / plan eşleşmiyor / SCADA okumuyor ayrımı).

---

## [2.53.28] — 2026-08-07

### Düzeltildi

- **Olay export'u 500 veriyordu** (v2.53.22–v2.53.27): export endpoint'i
  gövdede `offset/limit` kullanıyor ama imzada parametreler eksikti —
  her CSV/JSON/XLSX/PDF indirme NameError ile düşüyordu. Parametreler
  imzaya eklendi; PDF "yalnızca görünen sayfa" davranışı artık gerçekten
  çalışır.

---

## [2.53.27] — 2026-08-06

### Düzeltildi

- **Komut olayları Türkçe**: "reset_all_fcis (50984)" gibi satırlar artık
  "Tüm Göstergeleri Sıfırla (50984)" şeklinde görünür — komut parametresi
  makine anahtarı olarak saklanır, ekran sinyal sözlüğünden, PDF/Excel/CSV
  export aynı sözlüğün sunucu kopyasından çevirir (yeni kayıtlar; eski ham
  satırlar olduğu gibi kalır).

---

## [2.53.26] — 2026-08-06

### Düzeltildi

- **Gateway'de yeni sürüm çıktığı halde "Güncel" yazıyordu.** Güncelleme
  kontrolü, container'ın yaratıldığı andaki *çözülmüş* imaj referansına
  (digest'e sabitlenmiş ya da imaj ID'sine dönmüş olabilen) bakıyordu; bu
  referansa sorulan kayıt defteri sorgusu kendi digest'ini geri döndürdüğü
  için karşılaştırma **daima eşit** çıkıyor ve sonuç kalıcı olarak "Güncel"
  oluyordu. Kayıt defterinde `:latest` 1.6.1'e taşınmışken cihaz 1.5.0
  koşuyor ve güncelleme seçeneği hiç görünmüyordu. Kontrol artık compose
  dosyasındaki `image:` satırına — yani operatörün gerçekten izlediği
  etikete — göre yapılıyor; digest'e sabitlenmiş referansta `@sha256:…`
  kısmı atılır.
- **"Güncelle" düğmesi artık her durumda erişilebilir.** Önceden yalnızca
  güncelleme *tespit edilebildiğinde* çiziliyordu; `docker buildx` kurulu
  değilse veya kayıt defterine ulaşılamıyorsa durum "bilinmiyor" kalıyor ve
  operatör yeni sürüm yayınlanmış olsa bile **güncelleme yapamıyordu**.
  Tespit edilemeyen bir şey artık eylemi kilitlemiyor ("Yine de Güncelle").

### Eklendi

- Sürüm rozetinin ipucunda **hangi etiketin izlendiği** yazıyor; sürüme
  sabitlenmiş bir kurulumda "neden güncelleme çıkmıyor" sorusu cihaza
  girmeden cevaplanabiliyor. Ajan ayrıca `docker buildx` var mı bilgisini
  raporluyor (tespitin ön koşulu).

---

## [2.53.25] — 2026-08-06

### Değişti

- **Hat Sihirbazı topolojiyi artık kendisi çıkarıyor** (makine önerir,
  insan onaylar): koordinatlar sıra/tekrar beklenmeden yapıştırılır;
  direkler en yakın komşusuna bağlanır (minimum örten ağaç), en uzun yol
  ana hat, ayrılan kollar branşman olur — iç içe dallar dahil. Yeni
  "Topoloji" adımında öneri haritada gösterilir ve tek soru sorulur:
  **hat hangi uçtan başlıyor?** Numaralar/BAŞ-SON seçime göre dizilir.
  Eski "geri dönüş koordinatı" numarasına gerek kalmadı; dallar `-BR1`,
  `-BR1-1`… kodlarıyla ayrı hat olarak kurulup branşman noktasına bağlanır.

---

## [2.53.24] — 2026-08-06

### Değişti

- Ana sayfa haritasında direk seçim kartı sağ üstten **sağ alta** taşındı
  (katman düğmesiyle çakışıyordu).

---

## [2.53.23] — 2026-08-06

### Düzeltildi

- Hat Sihirbazı: **dışarı tıklayınca kapanmıyor** (yapıştırılan veriler
  kaza ile kaybolmasın) — kapatma sağ üstteki X ile; **tek "Geri"** kavramı
  (üstteki ikinci geri kalktı, geri yönü alttaki düğmede).
- Sihirbaz önizlemesinde **çatal direği branşman noktası olarak** işaretli
  (turkuaz); branşman öneri kutusunun bozuk dizilimi ve metni düzeltildi
  (işaretli/işaretsiz ne olacağı açıkça yazıyor).

---

## [2.53.22] — 2026-08-06

### Düzeltildi

- **Olay dışa aktarımında Cihaz kolonu** artık boş kalmıyor: olay kaydında
  cihaz kodu yoksa metadata'dan geri kazanılır (eski alarm kayıtları).
- Tüm export biçimlerine **Seri No** kolonu eklendi (cihaz adının yanında).

### Değişti

- **PDF export yalnızca görünen sayfayı** içerir — 1.8M kayıtlık tabloda
  "tüm olaylar" PDF'i anlamsızdı; modal bunu açıkça söyler. CSV/Excel veri
  dökümü olarak filtre kapsamında kalır (20k tavan).

---

## [2.53.21] — 2026-08-06

### Eklendi

- **Olay dışa aktarımı ekranla birebir**: PDF/Excel/CSV çıktıları arayüzde
  görünen Türkçe olay metinleriyle üretilir (`event_labels` + parite testi);
  PDF'lerde ortak üstbilgi/altbilgi düzeni ve Türkçe karakter desteği
  (`report_layout`). Olay durum rozetleri sadeleşti.
- **Gateway ajanı güncellemeleri** (`e1-gwd`): gateway API ve düzenleme
  modalında yeni alanlar.
- Sinyal arşiv sayfası ve yedekler panelinde iyileştirmeler; sistem durumu
  zaman alanları UTC-aware.

---

## [2.53.20] — 2026-08-06

### Değişti

- **Direk sınıflandırması rol modeline geçti** (ekipman envanteri değil,
  işlev): her direkte iki bağımsız alan — **topolojik görev** (hat
  başlangıcı / geçiş / branşman noktası / hat sonu / kablo geçişi) ve
  **enerji görevi** (yok / üretim / tüketim / çift yön). Kesici, ayırıcı,
  sigorta, trafo gibi ekipman tipleri sınıflandırmadan kaldırıldı; mevcut
  veriler migration ile dönüştürüldü (trafo→tüketim, kaynak→üretim,
  branşman hedefleri→branşman, hat uçları→başlangıç/son). Haritada ana
  ikon topolojik rolden; enerji rolü köşede renkli rozet (↑ yeşil üretim,
  ↓ mavi tüketim, ⇅ mor çift yön). Direk formunda iki ayrı rol seçimi.
  Excel: Direk_Tipi artık topolojik rol alır (eski değerler kabul edilip
  dönüştürülür), yeni Enerji_Rolu kolonu eklendi; branşman hedef listesi
  "branşman" rollü direkleri önceler. Eski dosyalar çalışmaya devam eder.

---

## [2.53.19] — 2026-08-06

### Eklendi

- Hat Sihirbazı'nda **akıllı dal ayıklama**: yapıştırılan listede yinelenen
  koordinat "geri dönüş" sayılır — aradaki direkler otomatik olarak ayrı bir
  **branşman hattı** olur (KOD-BR1, KOD-BR2…), ana hattın ilgili direğine
  branşman bağlantısıyla bağlanır. Haritada dallar kesikli mavi çizilir;
  özet ve bilgi satırı kaç dal/direk ayıklandığını söyler. Tek direklik
  dallara izin verildi (kısa trafo çıkışları).

---

## [2.53.18] — 2026-08-06

### Değişti

- Excel şablonu artık **Hizli_Yapistir sayfasıyla açılıyor** (13 kolonlu
  Topoloji sayfası ayrıntılı düzenleme için ikinci sırada) ve içinde
  gri/italik 4 satırlık örnek blok var — örnek satırlar içe aktarımda
  tamamen yok sayılır, kullanıcının verisine karışmaz. Sihirbazdaki adım
  metinleri hızlı sayfayı anlatıyor.

---

## [2.53.17] — 2026-08-06

### Eklendi

- Hat Sihirbazı'nda **canlı harita önizlemesi**: koordinatlar yapıştırılırken
  ve özet adımında çizilecek hat haritada görünür — yeni hat turuncu, mevcut
  topoloji soluk gri, branşman adayı kesikli mavi halka; harita direklere
  otomatik sığar.

---

## [2.53.16] — 2026-08-06

### Eklendi

- **Soru-cevap Hat Sihirbazı** artık aktif: bölge → hat (kod addan otomatik
  önerilir) → koordinatlar (tek kutuya "enlem, boylam" listesi yapıştırılır;
  Google Maps kopyası doğrudan çalışır, ters sıra otomatik düzeltilir,
  canlı sayaç/hata gösterimi) → özet → tek istekle oluştur. Segmentler
  otomatik; hat mevcutsa direkler sonuna eklenir; direk adları önekten
  otomatik üretilir.
- **Branşman tahmini** (sadece tahmin): yeni hattın ilk direği mevcut bir
  hattın direğine ≤200 m ise sihirbaz "bu hattan dallanıyor olabilir" diye
  önerir; ≤60 m'de öneri işaretli gelir, karar kullanıcının.
- **Excel şablonuna "Hizli_Yapistir" sayfası**: Bölge/Hat bir kez yazılır,
  Koordinat kolonuna "enlem, boylam" listesi komple yapıştırılır — sıra
  numarası ve segmentler otomatik, hat mevcutsa sonuna ekler; tek hücrede
  virgül/boşluk/ondalık virgül ve ters koordinat desteklenir.

### Düzeltildi

- İçe aktarmada branşman hedefi sayfada/planda olmayan MEVCUT bir hatta da
  bağlanabiliyor (DB araması eklendi).

---

## [2.53.15] — 2026-08-06

### Değişti

- Güvenlik Duvarı: kural/yönlendirme ekleme formu listenin ÜSTÜNE alındı;
  başlıktaki Duvarı Aç/Kapat ve Yenile düğmeleri eş boy; kilitlenme
  koruması üst şeritten kartın altındaki sade nota taşındı.

---

## [2.53.14] — 2026-08-06

### Değişti

- Uzaktan Bakım: üst durum bloğu daha basık — "AÇIK" rozeti kaldırıldı
  (başlık zaten söylüyor), geri sayım ve "Erişimi şimdi kapat" tek yatay
  satırda.

---

## [2.53.13] — 2026-08-06

### Değişti

- Güvenlik Duvarı: "Nasıl çalışır" kartı kaldırıldı; sekmeli kurallar kartı
  ile "Son işlemler" yan yana kutular. Uzayan içerik kartın içinde kayar.

---

## [2.53.12] — 2026-08-06

### Eklendi

- **Güvenlik Duvarı sayfası** (Mühendislik > Cihaz Ayarları): sistemin host
  güvenlik duvarı artık arayüzden yönetiliyor — açma/kapama, izin/engel
  kuralları (port, protokol, kaynak ağı) ve port yönlendirme (DNAT).
  Docker'ın yayınladığı SCADA portları (Modbus 502, IEC 104 2404-2406,
  NATS 4222, RabbitMQ 5672, FTP) `DOCKER-USER` zinciri üzerinden filtrelenir;
  "Hazır servisler" düğmeleri doğru portları tek tıkla ekler. Kilitlenme
  koruması cihaz tarafında sabittir: web arayüzü (80/443), SSH (22) ve
  uzaktan bakım tüneli hiçbir kuralla kapatılamaz. Uygulama host'ta root
  ile çalışan yeni `e1-fwd` ajanı üzerinden yapılır (e1-rad ile aynı
  dosya-IPC deseni); yapılandırma yeniden başlatmada 60 sn'lik zorlayıcı
  timer ile geri kurulur. Duvar VARSAYILAN KAPALI — mevcut kurulumların
  davranışı güncellemeyle değişmez. Görüntüleme: engineer/installer/
  ops_manager; değiştirme: engineer/installer. Tüm değişiklikler güvenlik
  kategorisinde denetim kaydına yazılır. Kurallar ve Port Yönlendirme aynı
  kartın iki sekmesi; duvar durumu ve aç/kapat düğmesi kart başlığında.
- **Saha Araçları güncellemeleri**: ping ile cihaz erişim testi (backend'in
  koştuğu makineden), DNS çözümleme ve cihaz tarama; çıktı ayrıştırma
  yerelden bağımsız, hedef doğrulaması enjeksiyona kapalı.

### Değişti

- Uzaktan Bakım: üst durum bloğu basıklaştırıldı; "neden" alanı kartın
  dibine kadar uzayarak ölü boşluğu dolduruyor.

---

## [2.53.11] — 2026-08-06

### Eklendi

- **Sinyal adları i18n**: 95 Horstmann sinyali Türkçe adlandırıldı (Mevcut
  Durum, Canlı Değerler, grafikler ve komut listesi). Sinyal anahtarları
  sabittir; çeviri cihaz önekinden arındırılmış sonek üzerinden bulunur,
  sözlükte olmayan sinyal katalog adıyla görünmeye devam eder.

### Değişti

- **Uzaktan Bakım sayfası uygulamanın ortak görsel diline taşındı**: şalter
  grafiği, degrade ve hale efektleri kaldırıldı; kartlar diğer sayfalarla
  aynı ölçü/renkte. Kart yerleşimi sabit — uzayan içerik kartın içinde
  kayar. Tailnet bilgileri durum bloğunda küçük çipler halinde; "İzin
  verdiğinizde ne olur" sağ sütuna kompakt kart olarak alındı; geri sayım
  gösterge gibi (monospace) ve izin düğmesi tam genişlik.

---

## [2.53.10] — 2026-08-06

### Değişti

- Cihaz Ayarları: bekleyen değişiklik varken **Kaydet (N)** düğmesi başlık
  satırında da görünür — uzun ayar listesinde en alta inmeye gerek kalmaz.

---

## [2.53.9] — 2026-08-06

### Eklendi

- **Ayar açıklamaları** Horstmann SN2.0 kullanma kılavuzundan (104101-2038V4)
  işlendi: cihaz ayarları ve şablon düzenleyicide 73 ayarın üzerine gelince
  ne işe yaradığı Türkçe açıklanır (arıza algılama, sıfırlama, gerilim/akım
  kaybı, inrush bastırma, DNP3/raporlama, ağ/FTP grupları). Açıklamalar
  aramaya da dahildir.

---

## [2.53.8] — 2026-08-06

### Değişti

- Cihaz Ayarları'nda ayar arama kutusu başlık satırına, düğmelerin yanına
  taşındı; durum satırı yalnızca gösterecek bilgi olduğunda basılır.

---

## [2.53.7] — 2026-08-06

### Eklendi

- **FTP'den otomatik sorgu.** Yapılandırması olmayan cihazda sistem önce
  FTP'de `<seri>_Configuration.csv` var mı diye kendisi bakar; bulursa
  sürüme çevirir. Elle "FTP'den sorgula" düğmesi de var. "Cihazdan çek"
  DNP3 komutu, cihazdaki update-CSV akışı doğrulanana kadar gizlendi.
- **Ayar arama** ve **ayar açıklama tooltip'i** (açıklama içerikleri
  Horstmann manüelinden doldurulacak; altyapı hazır). Dosya adı başlık
  satırına taşındı.
- **Cihazın bildirdiği son güncelleme damgası** (Last Configuration Update)
  kartta gösterilir — komut sonrası değiştiyse güncellemenin cihazda
  gerçekten uygulandığı doğrulanır.
- **Seçili cihazlara sırayla `config_update` komutu** (Cihaz Yapılandırma
  araç çubuğu).
- **Şablon düzenleyici**: şablon değerleri cihaz kartıyla aynı ızgarada
  düzenlenir (yerinde; geçmiş cihaz sürümleri etkilenmez). Şablon listesi
  satır kartlarına geçti.

### Düzeltildi

- **Fabrika şablonu CI/imajda bozuk çıkıyordu**: git, CRLF satır sonları
  sağlama toplamına dahil olan dosyayı metin sanıp LF'e çeviriyordu. Dosya
  ikili işaretlendi; v2.53.6 imajındaki seed bu yüzden çalışmıyordu.

---

## [2.53.6] — 2026-08-06

### Eklendi

- **Fabrika config şablonu depoyla geliyor.** SN2 için hiç şablon
  tanımlanmamış kurulumda, gerçek cihazdan alınmış doğrulanmış dosya
  açılışta varsayılan şablon olarak yüklenir. Yapılandırması olmayan cihazda
  kart artık yönlendirir: "Cihazdan çek", "Dosya yükle" veya tek tıkla
  **"Şablondan oluştur"** (oluşan dosya FTP'ye de yazılır).

### Değişti

- **Cihaz Ayarları kartı sadeleşti.** Tek satır başlık: solda ad + sürüm,
  sağda işlemler (Cihazdan çek / Cihaza uygula + indir-yükle-geçmiş ikonları).
  FTP mekaniği anlatan uzun ipucu metinleri kaldırıldı; sürüm geçmişi sayfa
  içi liste yerine popup'ta.

### Düzeltildi

- **Serisi çözülemeyen cihazda kart hiç açılmıyordu** ("Yapılandırma
  alınamadı"): dosya adı üretilemeyince istek patlıyordu. Kart artık açılır,
  "seri numarası yok" rozetiyle durumu söyler; cihaza gönderme, seri
  girilene kadar kapalı kalır.

---

## [2.53.5] — 2026-08-06

### Eklendi

- **Kayıtlı WiFi ağları** (telefonlardaki gibi). Bir kez bağlanılan ağın
  parolası cihazda saklanır; aynı ağa dönerken parola SORULMAZ. Ağ Ayarları
  sayfasında "Kayıtlı ağlar" listesi: tek tıkla bağlan, tekil "unut"
  (aktif bağlantıya ve cihazın kendi ağına dokunmaz). Tarama listesinde
  kayıtlı ağlar "Kayıtlı" rozetiyle işaretlenir.

### Düzeltildi

- **Türkçe/Unicode adlı WiFi ağlarına bağlanılamıyordu** ("Fikret Şafak
  iPhone'u" → `Fikret ?afak iPhone?u` aranıyordu): ağ ajanı nmcli'yi ASCII
  karakter kümesiyle (C yereli) çalıştırıyordu; C.UTF-8'e geçildi (durum
  metinleri İngilizce kalır, ayrıştırma bozulmaz).
- **Ağ değiştirirken "Connection 'e1-grid-wifi' exists but properties don't
  match" hatası**: önceki ağın profili dururken yeni SSID'ye bağlanılamıyordu.
  Bayat profil bağlanmadan önce temizlenir; kimlik zaten kayıtlı ağlarda.
- **Ağ sayfasında uyarı belirince kartlar aşağı kayıyordu.** Uyarı yuvası
  artık sabit yer tutar; internet İYİYKEN de aynı yuvada tek satırlık durum
  ("İnternet bağlantısı var — X üzerinden") gösterilir, yerleşim oynamaz.

---

## [2.53.4] — 2026-08-05

### Eklendi

- **Seri numarası artık cihaz kaydında** (`devices.serial_number`). Kurulumda
  cihaz formundan girilir; cihaz bağlanınca `master.serial_number`
  telemetrisinden OTOMATİK güncellenir (değişim olay kaydına yazılır: eski →
  yeni). Config dosya adı ve FTP eşleştirmesi artık önce kayıttan gider;
  telemetri anlık sıfır/yanlış okusa bile akış kilitlenmez. Mevcut cihazlar
  migration ile doldurulur (telemetrideki geçerli seri; yoksa salt-rakam
  cihaz kodu).

### Düzeltildi

- **`0_Configuration.csv` üretilebiliyordu.** Cihaz bir an seri=0 gönderince
  dosya adı sıfırdan türetiliyordu — o adı hiçbir cihaz okumaz. Sıfır seri
  artık hiçbir kaynaktan kabul edilmez.
- **"Harici FTP'ye yazılamadı:" boş sebeple bitiyordu.** Metinsiz istisnalar
  (bağlantının sunucu tarafından kapatılması gibi) artık istisna türüyle
  raporlanır.
- **Harici FTP'ye yazma sadeleşti.** Dosya, dizin taraması yapılmadan doğrudan
  ayarlardaki dizine yazılır — tarama WAN üzerinde yavaştı ve bağlantıyı
  erken kapatan sunucularda yazmayı düşürüyordu.

---

## [2.53.3] — 2026-08-05

### Düzeltildi

- **Parola ayarlanmamışken PASV adresi uygulanmıyordu.** Dahili sunucu
  kimliği henüz arayüzden kaydedilmemişse (env parolasıyla çalışırken)
  kimlik yoklaması adres bilgisini de yok sayıyordu; PASV, adres kaydedilmiş
  olsa bile konteyner IP'sini bildirmeye devam ediyordu. Adres artık
  paroladan bağımsız uygulanır.

---

## [2.53.2] — 2026-08-05

### Düzeltildi

- **Harici FTP kimliği dahili sunucuya sızıyordu.** Tek kimlik seti varken
  harici mod yapılandırılınca müşteri sunucusunun kullanıcı/parolası dahili
  sunucuya da geçiyor, cihazlar ve kullanıcı bir anda eski kimlikle giremez
  oluyordu (sahada yaşandı). Dahili ve harici kimlikler artık AYRI saklanır;
  mod değiştirmek diğerinin kimliğine dokunmaz (migration 0043).
- **Dahili FTP dizinlerine backend yazamıyordu.** ftp-server yalnızca kök
  dizini paylaşıma açıyordu; cihazın (ya da sunucunun) açtığı alt dizinler
  root'ta kalıyor ve "Cihaza uygula" izin hatasıyla düşüyordu — arayüzde
  yalnızca "gönderilemedi" görünüyordu. İzin düzeltme artık özyineli ve her
  dosya alımında ilgili zincire uygulanıyor; hata olursa gerçek sebep artık
  arayüzde görünür.

### Değişti

- **Ekranda görülen dosya = FTP'deki dosya.** Kaydet / dosya yükle / geri al
  işlemleri yeni sürümü FTP'deki `<seri>_Configuration.csv` dosyasına da
  yazar (dahili veya harici, mod fark etmez). Yazma başarısız olursa sürüm
  kaydedilir ama kullanıcı uyarılır ve olay loglarda görünür. "Cihaza
  uygula" artık yalnızca güncelleme komutunu gönderir (dosyayı da tazeler).
- **Dahili modda IP girme kalktı.** PASV/cihaz ekranı adresi, arayüzün
  eriştiği adresten otomatik alınır; form yalnızca kullanıcı adı, parola ve
  dizin sorar. Cihaz ekranına girilecek değerler tek satırda gösterilir.
- "Gömülü sunucu" arayüzde "Dahili sunucu" olarak adlandırıldı.
- Bağlantı Durumu logları olay tipine göre ikonlu başlık + kısa ayrıntı
  olarak gösterilir; ham denetim metni yerine okunur satırlar.

---

## [2.53.1] — 2026-08-05

### Düzeltildi

- **Gömülü FTP'de pasif mod (PASV) dışarıdan çalışmıyordu.** Sunucu pasif mod
  yanıtında Docker'ın iç IP'sini (172.18.x.x) bildiriyordu; LAN'daki cihaz/
  istemci veri bağlantısı kuramıyor, her dosya listeleme/transfer zaman
  aşımına düşüyordu. FTP ayarlarındaki "Sunucu adresi" artık pasif mod
  yanıtında cihazlara bildirilen adres olarak kullanılıyor ve değişiklik
  yeniden başlatmasız (~30 sn) uygulanıyor. Adres boşsa ftp-server log'a
  açık uyarı yazar.
- **Harici sunucu sınaması gerçek sebebi göstermiyordu.** Sunucudan gelen
  bazı hata yanıtları yakalanmayıp genel "Bağlantı sınanamadı" mesajına
  dönüşüyordu. Sınama artık hiçbir koşulda patlamaz; kimlik reddi, bağlantı
  zaman aşımı ve "veri kanalı kurulamadı (pasif mod portları kapalı
  olabilir)" ayrımıyla raporlar. Sınama yalnızca taban dizini listeler —
  derin tarama WAN üzerinde isteği zaman aşımına sürüklüyordu.

### Değişti

- **Varsayılan dizin Smart Navigator 2.0 standardına çekildi:** `/SN20/FOTA/`.
  ftp-server açılışta bu dizini otomatik oluşturur; ayarlarda seçilen dizin
  de kayıtta oluşturulur (cihaz var olmayan dizine girmeye çalışıp 550
  almasın). Eski varsayılanda (`/`) duran mevcut kayıt migration ile
  güncellenir; elle seçilmiş farklı dizinlere dokunulmaz.
- **FTP ayarları popup'ı yeniden düzenlendi.** Sabit boyutlu iki sütun:
  solda bağlantı durumu + kendi içinde kayan hareket listesi, sağda tek
  sütun ayar formu (port, sunucu adresinin yanında) — loglar geldikçe popup
  artık uzamıyor. Açıklama metinleri kısaltıldı; sınırlar alanların kendi
  `maxLength`'iyle uygulanıyor, ekranda gereksiz bilgi yok.
- **Harici modda parola normal parola alanı gibi girilir** (maskeli, "Üret"
  düğmesi yok) — o parola müşterinin sunucusuna aittir. Açık gösterim ve
  okunabilir parola üretici yalnızca gömülü modda (cihaz ekranına elle
  girilecek kimlik) kalır.

---

## [2.53.0] — 2026-08-05

### Eklendi

- **"Cihaz Yapılandırma" mühendislik sayfası** (Kurulum grubunda, engineer+installer).
  Solda aranabilir cihaz listesi — her satırda güncel yapılandırma rozeti
  (v3 · cihazdan çekildi); sağda seçili cihazın config kartı. Cihazlar arası
  tek tıkla gezilir; FTP ayarları, şablonlar ve toplu uygulama popup'tadır.
- **FTP sunucu ayarları arayüzden yönetiliyor** (gömülü/harici mod, sunucu,
  port, kullanıcı, parola, dizin). Parola değişikliği için yeniden başlatma
  GEREKMEZ: gömülü sunucu kimliği backend'den ~30 saniyede bir çeker.
  "Üret" düğmesi cihaz ekranına elle girilebilir, karışan karakter (0/O,
  1/l/I) içermeyen parola önerir; alan sınırları cihaz ekranıyla aynı
  (kullanıcı ≤29, parola ≤19).
- **Harici FTP sunucu desteği.** Cihazlar ve yazılım müşterinin FTP
  sunucusunu kullanabilir: config oraya yazılır, cihazın yazdığı dosyalar
  belirlenen aralıkla yoklanıp sürüme çevrilir. "Bağlantıyı sına" düğmesi
  sunucu/kimlik/dizini doğrular.
- **"Cihaza uygula" zinciri kapandı.** Dosya artık FTP'ye otomatik yazılır,
  ardından `config_update` komutu kuyruğa alınır ve sürümün "cihaza
  gönderildi" zamanı işlenir. FTP yazımı başarısızsa komut gönderilmez —
  cihaza eski dosya okutulmaz.
- **Yapılandırma şablonu yükleme ve toplu uygulama.** Bilinen-iyi dosya
  şablon olarak yüklenir, varsayılan işaretlenir; yeni eklenen cihaz ilk
  yapılandırmasını varsayılan şablondan alır. Toplu uygulamada onay ekranı
  kaç cihaz / hangi şablon / cihaz başına hangi sürümden hangisine bilgisini
  ve modeli uymadığı için atlanacakları gösterir.
- **Bağlantı durumu paneli.** Gömülü sunucunun sağlığı, şu an kabul ettiği
  kimlik (kimlik değişimi yansıyana kadar "senkron bekleniyor" uyarısı) ve
  son FTP hareketleri (girişler, dosya transferleri, yoklama hataları).

### Değişti

- Gömülü FTP sunucusunun kimliği artık `.env` yerine veritabanından yönetilir;
  `.env`'deki `FTP_USER`/`FTP_PASSWORD` yalnızca backend'e erişilemeyen ilk
  açılışta yedek olarak kullanılır. Kimlik değişimi aktif dosya transferlerini
  düşürmez.

---

## [2.52.1] — 2026-08-05

### Düzeltildi

- **"Cihaza uygula" butonunda ikon yerine `CLOUD_UPLOAD` yazıyordu.** İkon fontu
  kodda gerçekten kullanılan ~220 ikonluk bir alt küme; yeni ikon eklendiğinde
  font yeniden üretilmezse ikon yerine **adı** görünür. Font yeniden üretildi
  (222 ikon, 156 kB).

### Değiştirildi

- **Ayarlar iki sütunlu ızgaraya geçti.** Tabloda "Birim" en sağda ayrı bir
  sütundu; göz değer ile birimi eşleştirmek için satırı baştan sona tarıyordu.
  Artık birim değerin hemen yanında ve iki ayar yan yana sığıyor (dar ekranda
  kendiliğinden tek sütuna düşer).
- **Değiştirilemeyen satırlar gösterilmiyor.** Metin alanları sabit genişlikte
  olduğu için düzenlenemiyordu; `[not configured]` gibi kayıtlar ekranı
  doldurup asıl ayarları görmeyi zorlaştırıyordu.

---

## [2.52.0] — 2026-08-05

### Eklendi

- **Ayarların adları görünüyor.** Cihazdan gelen CSV yalnızca `GROUP,INDEX`
  taşır, adları taşımaz; ekran bu yüzden "381101 = 0" gibi anlamsız satırlar
  gösteriyordu — hangi ayarı değiştirdiğinizi bilmeden düzenleme yapmak,
  yanlış ayar değiştirmenin en kolay yoluydu. Smart Navigator Explorer'ın
  ürettiği katalogdan 144 girdilik bir anlam tablosu çıkarılıp uygulamaya
  gömüldü; gerçek cihaz dosyasında 60 ayarın 56'sı artık adı ve birimiyle
  görünüyor ("Dial-In Interval — 1440 min").
- **Cihaz komutları Ayarlar ekranına taşındı.** "Cihazdan çek"
  (`start_csv_file_upload`) ve "Cihaza uygula" (`config_update`) artık aynı
  ekranda; Komutlar sekmesine gitmek gerekmiyor. Yapılandırması olmayan
  cihazda da "Cihazdan çek" görünüyor — tam da o durumda ihtiyaç duyulan
  işlem o.

### Değiştirildi

- **Ayar listesi tam boy uzuyor.** Tablo 28 rem'de kesilip kendi kaydırma
  çubuğunu çıkarıyordu; 60 ayarlık bir listede bu, küçük bir pencereden
  bakmaya zorluyordu. Artık liste tam boy, başlık satırı üstte sabit kalıyor.

---

## [2.51.1] — 2026-08-05

### Düzeltildi

- **v2.51.0 derlenmiyordu.** `api.ts` içindeki yapılandırma tipleri import
  edilmemişti; CI `tsc -b` ile bunu yakaladı.
- **Tip kontrolü komutu yanıltıcıydı.** `CLAUDE.md` `npx tsc --noEmit`
  diyordu; kök `tsconfig.json` solution-style (`files: []`, yalnızca
  `references`) olduğu için bu komut **hiçbir dosyayı kontrol etmeden her
  zaman başarılı** dönüyor. Doğrulama diye çalıştırılan ama hiçbir şeyi
  doğrulamayan bir komuttu ve yukarıdaki hatanın yerelde fark edilmemesinin
  sebebi buydu. Kılavuz `npx tsc -b` olarak düzeltildi.

---

## [2.51.0] — 2026-08-05

### Eklendi

- **Cihaz Ayarları ekranı.** Cihaz sayfasındaki "Yapılandırma" sekmesi artık
  gerçek: cihazın `Configuration.csv` dosyası anlamlı adlarla listeleniyor,
  sayısal ayarlar satır içinde düzenlenebiliyor, sürüm geçmişi görülüp eski
  sürüme dönülebiliyor, dosya indirilip yüklenebiliyor. Kaydetmek **göndermek
  değildir** — ekranda bu açıkça yazıyor; dosyanın cihaza ulaşması için FTP'ye
  konup DNP3 komutuyla tetiklenmesi gerekir.
- **FTP hareketlerinin izlenmesi.** `ftp-server` artık bağlantı ve dosya
  olaylarını backend'e bildiriyor (kim bağlandı, hangi dosyayı yazdı/aldı,
  yarım kalan yüklemeler dahil). Bildirim ayrı bir thread'de: pyftpdlib tek
  thread'de çalışır ve callback içinde HTTP isteği yapmak tüm FTP sunucusunu
  bloklardı.
- **Cihazdan gelen config otomatik sürüme dönüşüyor.** Cihaz
  `start_csv_upload` komutuyla kendi yapılandırmasını FTP'ye yazdığında, dosya
  adındaki seri numarası cihazı tanımlar ve içerik "cihazdan çekildi" kaynaklı
  yeni sürüm olarak kaydedilir. Aynı içerik tekrar gelirse sürüm üretilmez.
- **Toplu yapılandırma ucu.** Bir şablon seçili cihazlara tek işlemde
  uygulanabiliyor. Başarısızlar sessizce atlanmıyor; hangi cihaz neden alamadı
  dönüyor ve model uyuşmazlığı engelleniyor.
- **Yeni cihaz eklendiğinde** modelin varsayılan şablonundan ilk sürüm
  otomatik üretiliyor.

### Düzeltildi

- **Yapılandırma dosyası yüklenemiyordu** ("required field"). `FormData`
  gönderilirken araya `Content-Type: application/json` giriyor ve multipart
  sınırlayıcısını eziyordu; sunucu gövdeyi ayrıştıramayıp dosyayı eksik alan
  sayıyordu.
- **FTP kök dizini backend tarafından yazılamıyordu.** Volume `root` sahipli
  oluşuyor, backend ise `uid 10001` ile koşuyor. Harita karolarında yaşanan
  arızanın aynısı; `ftp-server` açılışta dizini paylaşılabilir hale getiriyor.

### Değiştirildi

- Cihaz sayfasındaki DNP3 özeti kaldırıldı (aynı bilgi iki ekranda daha var) ve
  başlık "FTP Config Yönetimi" yerine **"Cihaz Ayarları"** oldu — dosyanın FTP
  ile taşınması bir taşıma detayı, başlıkta yer alması gereksiz kavram yüküydü.
- **Yanlış cihaza dosya yüklemeye karşı uyarı.** Dosya adındaki seri cihazın
  serisiyle uyuşmuyorsa onay isteniyor ama engellenmiyor (başka bir cihazın
  dosyasını şablon olarak kullanmak meşru). Uyuşmazlık her hâlükârda denetim
  kaydına yazılıyor.

---

## [2.50.0] — 2026-08-05

### Eklendi

- **Horstmann `Configuration.csv` okuyucu/yazıcı.** Cihazın yapılandırma
  dosyasının ikili biçimi çözüldü ve gerçek cihaz dosyasıyla doğrulandı
  (60 girdinin 60'ı okundu, gidiş-dönüş bayt bayt aynı). Biçim
  `GROUP(4hex),INDEX(2hex),UZUNLUK(2hex),DEĞER(little-endian hex)`; dosya
  sonunda `<checksum: 2 bayt LE> FF FF` ve `checksum = (-sum(gövde)) & 0xFFFF`.
  **Satır sonları CRLF'tir ve checksum'a dahildir** — LF'e "normalize" etmek
  toplamı değiştirir ve cihaz dosyayı reddeder. Explorer'ın ürettiği
  `<seri>.xml` kataloğu da okunuyor; böylece ham girdiler anlamlı adlarla
  gösterilebiliyor.
- **Cihaz yapılandırma şablonları ve sürüm geçmişi.** Şablonlar cihaz tipine
  göre; sürümler cihaz başına artar ve **append-only**'dir: "geri al" eskiyi
  geri yazmaz, eski baytlarla yeni sürüm yaratır — böylece "o gün cihazda ne
  vardı" sorusunun cevabı hep doğru kalır. Yeni cihaz eklendiğinde varsayılan
  şablondan ilk sürüm otomatik üretilir. Dosya adındaki seri numarası
  `master.serial_number` telemetri sinyalinden gelir; seri yoksa işlem açık
  hatayla durur (sessizce başka bir ada düşmek, cihazın hiç görmeyeceği bir
  dosya üretirdi).

### Değiştirildi

- **WiFi görev değişiminde ilerleme penceresi.** AP ↔ istemci geçişi anında
  sonuçlanmıyor — kart düşürülüp yeni göreviyle açılıyor. Eskiden ekranda tek
  satırlık bir metin vardı; kullanıcı bir şey olup olmadığını anlamadığı için
  aynı butona tekrar basıyordu. Artık dönen gösterge, geçen süre ve üç adımlı
  ilerleme gösteren bir pencere açılıyor. Pencere istek gönderilmeden **önce**
  açılıyor, çünkü işlem kendi bağlantımızı düşürebilir ve sonrasına
  dönemeyebiliriz.
- **WiFi ayarları penceresi sadeleştirildi.** "Tek WiFi kartı var, ikisi aynı
  anda olamaz" bilgisi ekranda üç ayrı yerde tekrarlanıyordu; artık bir kez
  veriliyor.

---

## [2.49.1] — 2026-08-05

### Değişti — üretilen gateway compose/env dosyası production-temiz

- **Geliştirme notları çıktıdan çıkarıldı**: üretilen dosyada artık çok
  satırlı gerekçe yorumu, ölçüm/tarih anlatısı yok — yalnızca 2 satırlık
  kimlik başlığı + tek satırlık bölüm başlıkları. Gerekçeler şablon kaynak
  koduna ve `docs/APPLIANCE.md` bölüm 8'e taşındı; sihirbazdaki indirme
  adımına kısa yardım metni eklendi.
- **`GATEWAY_INSECURE_ALLOW_PLAINTEXT` artık koşullu**: backend adresi
  `https://` ise `false`, değilse bilinçli opt-out olarak `true` üretilir.
  Güvenlik opt-out'u her dosyada açık gelmiyor.
- **Env değişkenleri mantıksal sırada**: kimlik → ortam → backend →
  telemetri → sağlık/polling → DNP3 → log.

---

## [2.49.0] — 2026-08-05

### Düzeltildi

- **Çevrimdışı harita indirmesi hiç çalışmıyordu.** "577 karo indirilemedi
  (internet kesintisi olabilir)" hatasının sebebi internet değildi: karolar
  başarıyla iniyor ama diske **yazılamıyordu**. Backend `e1` (uid 10001)
  kullanıcısıyla koşuyor, harita önbelleği için oluşan volume ise `root`
  sahipliydi. Docker, imajda **bulunmayan** bir volume hedefini `root:root`
  yaratır; harita özelliği eklenirken `docker-compose.yml`'a volume eklenmiş
  ama Dockerfile'daki `mkdir`/`chown` satırına eklenmemişti. Teşhis zordu
  çünkü her şey sağlıklı görünüyordu — DNS çözülüyor, karo çekme çalışıyor,
  `online: True`; yalnızca yazma düşüyordu ve hata "internet yok" diye
  raporlanıyordu. Artık `/var/lib/e1-map-tiles` de imajda oluşturulup `e1`
  kullanıcısına veriliyor.

### Güvenlik

- **Sabit kurulum parolası kaldırıldı.** `installer` hesabı artık her kurulumda
  **rastgele** parola ile yaratılıyor; parola yalnızca kurulum çıktısında
  görünür, kaynak kodda yazılı değildir. Önceki `ChangeMe123!` sabiti,
  deponun özel olmasına dayanan bilinçli bir kabuldü; depo 2026-08-05'te
  herkese açık hale gelince o dayanak ortadan kalktı. Üretilen parola belirsiz
  karakterler (0/O, 1/l/I) içermez — kurulum çıktısından elle okunacağı için.
  Toplu saha kurulumlarında `E1_INSTALLER_PASSWORD` ile merkezi parola
  verilebilir.

### Eklendi

- **Horstmann SN2.0 güncelleme dosyası adlandırması** (HH-EW-25-019 Rev 1.0).
  Firmware / yapılandırma / DNP3 nokta listesi dosyalarının adı artık tek
  yerden üretiliyor: tekil güncellemede seri numarasından
  (`49904_Firmware.utf`), toplu güncellemede firmware sürümünden
  (`V2_338_55_Firmware.utf`). Dosya adı ile tetikleyici DNP3 komutu **birlikte**
  döndürülüyor — ayrı seçilebilselerdi "toplu ad + tekil komut" gibi bir
  eşleşmezlik mümkün olurdu. Bu alanda en olası arıza biçimi "hata" değil
  **sessizlik**: adı bir karakter tutmayan dosyayı cihaz hiç görmez, log da
  üretmez. Seri numarası olmayan cihazda tekil güncelleme reddediliyor
  (sessizce topluya düşseydi aynı sürümdeki tüm cihazlar güncellenirdi).

---

## [2.48.0] — 2026-08-05

### Düzeltildi

- **"Güncelleme sonrası giremiyorum" (502) kalıcı olarak çözüldü.** Backend
  container'ı yeniden yaratıldığında yeni bir IP alıyor; nginx ise adresi
  yalnızca başlangıçta çözüp sakladığı için ölü adrese gitmeye devam ediyor ve
  arayüz 502 veriyordu. Teşhisi zordu, çünkü her şey sağlıklı görünüyor:
  backend Up (healthy), `/health` container içinden 200, loglarda tek hata yok
  — yalnızca nginx üzerinden geçen istek 502. v2.46.0'da `update.sh`'a frontend
  tazeleme eklenmişti ama o yalnızca güncelleme akışını koruyordu; elle
  `docker compose up -d` çalıştırınca aynı hata geri geliyordu (iki kez
  yaşandı). nginx artık backend adresini **her istekte** çözüyor.
- **Postgres checkpoint'lerinin %95'i zorlanmıştı.** `max_wal_size` 2 GB'a o
  kadar hızlı doluyordu ki Postgres sürekli acil checkpoint yapıyor, her
  seferinde yazma duruyordu; arşiv yazma hızı 3.355 ile 1.666 satır/sn
  arasında testere dişi gibi dalgalanıyordu. Tavan 16 GB'a çıkarıldı.

### Eklendi

- `synchronous_commit` artık `E1_PG_SYNC_COMMIT` ile ayarlanabiliyor.
  **Varsayılan `on` (güvenli).** `off` yazma hızını artırır ama ani güç
  kesintisinde son ~200 ms - 1 saniyelik ölçümler kalıcı olarak kaybolur —
  tüketici commit başarılı dönünce mesajı onayladığı için yeniden teslim
  kurtarmaz. Yük testinde denemek için açılabilir; sahada açmadan önce karar
  yazılı olmalı.

## [2.47.0] — 2026-08-05

### Düzeltildi

- **Tekilleştirme defteri sınırsız büyüyordu ve tüm sistemi yavaşlatıyordu.**
  `processed_messages` 2 saatlik pencerede tutulmalıydı; 500 cihazlık yük
  testinde 74 milyon satır / 20 GB / 10,5 saatlik veri birikmişti. Sebep:
  silme kapasitesi (1.666 satır/sn) üretimin (~2.900 satır/sn) altındaydı,
  yani tablo hiçbir zaman kararlı duruma gelemezdi. Tablo büyüdükçe
  tekilleştirme sorgusu önbelleğe sığmayıp diskten okumaya başlıyor, yazma
  hızı %46 düşüyor ve kuyruk şişiyordu. Temizlik aralığı 600 → 60 saniyeye
  indirildi; kapasite artık üretimin ~5,7 katı.
- **Cihaz satırında kilit çekişmesi.** Her ölçüm `devices.last_update_at`
  alanına yazıyordu ve 500 cihazda öncelikli/toplu hatlar aynı satırda
  çakışıyordu. Yazma sıklığı 5 → 30 saniyeye indirildi (saniyede ~100 yerine
  ~17 güncelleme). Arayüzdeki "Son veri" göstergesinde fark edilmez; cihazın
  çevrimiçi olup olmadığı bu alandan değil, anında yazılan
  `communication_status` alanından belirleniyor.
- **Sıkıştırma hiç devreye girmiyordu.** Eşik 7 gündü ve 46 chunk'ın sıfırı
  sıkıştırılmıştı. Günde ~192 GB büyüyen bir arşivde bu, 1,3 TB sıkıştırılmamış
  veri demekti — 456 GB diske sığmaz. Eşik 1 güne indirildi.

### Eklendi

- Bu üç arıza da aynı sınıftan: bir ayar "çalışıyor" görünürken üretim hızını
  ya da disk bütçesini aşıyor. Her biri için kuralı sayısal olarak kilitleyen
  test eklendi (temizlik kapasitesi ≥ üretim × 3, sıkıştırma eşiği ≤ 2 gün).

## [2.46.0] — 2026-08-05

### Düzeltildi

- **NATS akış tavanları yükseltildi — veri kaybı yaşanıyordu.** 500 cihazlık yük
  testinde `TELEMETRY_NORMALIZED` akışı 3 GiB tavanına dayandı (600 bayt kalmıştı)
  ve `discard: old` politikası gereği en eski ölçümleri sessizce atmaya başladı.
  Dolu bir akışa yazmak kat kat pahalı olduğu için zincirin tamamı kilitlendi:
  tag-engine yayınlayamadığı için aldığı mesajları onaylayamıyor, yenisini
  çekemiyor ve ham akış 2,1 milyona şişiyordu. Tavanlar RAW 6→24 GiB,
  NORMALIZED 3→12 GiB, DLQ 1→2 GiB, hesap 12→48 GiB yapıldı; birikim 90 saniyede
  3,09M'den 1,56M'e düştü.
- **Güncelleme sonrası arayüze girilemiyordu (502).** Backend yeniden
  yaratıldığında yeni bir IP alıyor, ancak frontend'in nginx'i adresi yalnızca
  başlangıçta çözdüğü için ölü adrese gitmeye devam ediyordu. Her şey sağlıklı
  görünürken yalnızca arayüz 502 veriyordu. Güncelleme artık backend yeniden
  yaratıldığında frontend'i de tazeliyor.

### Değişti

- **Historian varsayılanı açık listeye çevrildi.** Artık bir sinyalin arşive
  girmesi bilinçli karar gerektiriyor; katalog büyüdükçe yeni sinyaller sessizce
  arşive sızmıyor. Liste hat analizi ihtiyacından türetildi: yük akımı, arıza
  akımı ve süresi, gerilim, iletken ve cihaz sıcaklığı, batarya, sinyal gücü,
  konum, faz ve eğim açısı — artı sayaçlar. Ayar parametreleri (nominal gerilim,
  açma eşiği, alarm eşiği), statik metadata (seri no, firmware, donanım sürümü),
  ikili sinyaller ve durum metinleri arşiv dışında.
  Sahada ölçüldü: 193 → 60 arşivlenen sinyal, disk büyümesi ~14 GB/saat
  seviyesinden ~3 GB/saat'e indi. Mevcut kurulumların ayarı değişmez.

## [2.45.5] — 2026-08-04

### Düzeltildi — performans: alarm "drift clear" seli kökten kaldırıldı

- **Alarm servisi artık boşa clear POST üretmiyor**: hiç aktif olmamış her
  (kural × cihaz) çifti için atılan periyodik idempotent clear'lar
  O(kural × cihaz) ölçeğinde büyüyor, 401 cihazda gönderim kuyruğunu
  sınırsız şişiriyor ve Postgres'i no-op sorgularla meşgul ediyordu.
  Aynı işi backend'deki reconcile worker'ı 30 sn'de bir tek sorguyla
  zaten yapıyor; alarm servisi artık yalnızca gerçek geçiş (aktif → normal)
  clear'ı gönderiyor. `ALARM_DRIFT_CLEAR_INTERVAL_SEC` ayarı kaldırıldı.

### Düzeltildi — gateway compose şablonu (saha bildirimi)

- **`ulimits: nofile 65536` şablona eklendi**: her DNP3 cihazı bir TCP
  soketi tutar; Docker'ın 1024 varsayılanı 500 cihaz hedefinde yetersiz ve
  limit dolunca hata "cihaz kopuk" gibi görünüyordu. Elle eklenen ayar her
  render'da siliniyordu; artık kalıcı.
- **Proje adı hizalandı**: şablon `name: e1-gateway-*` üretiyor ama agent
  `-p e1-gw-*` ile kuruyordu; `-p`'siz her `docker compose up -d`
  "container name already in use" veriyordu. Şablon artık `name: e1-gw-*`.

---

## [2.45.4] — 2026-08-04

### Düzeltildi — alarm değerlendirme hattı artık backend'i beklemiyor

- **Alarm prio kuyruğu birikiyordu** (401 cihaz testinde ~1.166 mesaj/sn):
  backend'e giden senkron HTTP çağrıları (alarm kaldırma + temizleme)
  mesaj işleme döngüsünün içinde bloklayarak koşuyordu. Gönderim ayrı bir
  thread'e ve sınırlı kuyruğa taşındı; kural değerlendirmesi artık yalnızca
  bellek içi çalışıyor. ("Önce backend POST → alarm_id → RabbitMQ" sırası
  korunur; sağlık ucunda `notify_bekleyen` alanıyla izlenir.)
- **Drift temizlik seli**: alarm hiç aktif olmamış (kural × cihaz)
  anahtarları için 60 sn'de bir atılan idempotent clear POST'ları Postgres'i
  no-op sorgularla boğuyordu. Aralık 600 sn'ye çıkarıldı ve
  `ALARM_DRIFT_CLEAR_INTERVAL_SEC` ile ayarlanabilir; gerçek alarm geçişleri
  bu aralıktan bağımsız anında gönderilir.

---

## [2.45.3] — 2026-08-04

### Düzeltildi — haberleşme durumu telemetri kuyruğundan bağımsızlaştı

- **Sağlık kanalından sayı-bazlı güvenli çıkarım**: gateway "tüm cihazlar
  koptu" diyorsa (devices_online=0) cihazlar en geç bir tarama periyodunda
  OFFLINE'a çekilir — telemetri kuyruğu tıkalı/purge edilmiş olsa bile.
  (Sahada iki kez yaşandı: comm_lost olayları kuyrukla birlikte kaybolunca
  cihazlar ONLINE takılı kalıyordu.)
- **Filo alarmı hiç çalışmamıştı**: var olmayan User.is_active kolonuna
  bakıp her turda hata fırlatıyordu; düzeltildi.

---

## [2.45.2] — 2026-08-04

### Düzeltildi

- **Boru hattı panelinde ham kuyruk "—" gösteriyordu**: aşama görünümü
  eski durable adını arıyordu; queue-group'lu yeni ad (…-q1) da okunur,
  geçiş anında ikisi toplanır.

### Eklendi

- tag-engine ayar düğmeleri env'den: TAG_PUBLISH_PARALLEL ve
  TAG_MAX_ACK_PENDING (büyük replay'lerde hız ayarı).

---

## [2.45.1] — 2026-08-04

### Düzeltildi

- **2.45.0'da tag-engine replikaları hiç başlayamıyordu** (nats-py kuralı:
  queue aboneliğinde queue adı durable adıyla aynı olmalı; farklı
  verildiğinde kütüphane consumer'ı yaratmadan hata fırlatıyor ve iki
  replika da döngüye giriyordu — normalize akışı durdu). Ayrıca yeni
  durable artık stream'de birikmiş ne varsa işler (DeliverPolicy.ALL):
  kesinti sırasında biriken ham ölçümler atlanmaz, güncellemeden sonra
  otomatik yeniden işlenir.

---

## [2.45.0] — 2026-08-04

### Değişti — 400-500 cihaz ölçek paketi

- **Kalıcılaştırma artık çok süreçli**: telemetri tüketicisi leader
  kilidinden ayrıldı, worker container'ı varsayılan 4 süreçle çalışır
  (E1_WORKER_PROCESSES) — persist kapasitesi süreç sayısıyla çarpılır.
- **tag-engine yatay ölçeklenir**: queue-group'lu durable'a kayıpsız geçiş
  + ikinci replika (tag-engine-b). İki kopya mesajları bölüşür.
- **Kaynak bütçesi yeniden dağıtıldı** (ölçüme göre): NATS 4 CPU/3G,
  backend 4 CPU, alarm 2 CPU, Postgres 6G→4G (ayarları birlikte indirildi).
- **Arşivde birebir tekrar bastırma**: ikili/sayaç sinyallerde değer VE
  kalite aynı olan tekrarlar yazılmaz; her değişim ve her kalite geçişi
  aynen arşivlenir (kurallar korunur).

### Kaldırıldı

- alarm-service'in artık işlevsiz legacy hattı (4-token eski konu); durable
  startup'ta silinir — 7,9M'lık hayalet birikim stream diskini baskılıyordu.

---

## [2.44.2] — 2026-08-04

### Düzeltildi

- **Ağ sayfası görsel düzeltmeleri**: WiFi ayarları penceresindeki çift
  kapatma düğmesi kaldırıldı (tek desen: sağ üstte X); tüm modallarda
  başlık/aksiyon hizası sabitlendi (aksiyonlar sağda). Kablolu ağ kartına
  profesyonel alt bölüm (ayırıcı + uyarı notu + sağda buton; kart yandaki
  WiFi kartıyla aynı yükseklikte biter). WiFi listesinde bağlı ağ her
  zaman en üstte, kalanlar sinyal gücüne göre.

---

## [2.44.1] — 2026-08-04

### Düzeltildi

- **`gateway_health` tablosunun migration'ı hiç üretilmemişti**: gateway
  sağlık başlığı gönderince `/pending` (SCADA komut kanalı) 500 veriyor,
  gateway başlığı 10 dakika bırakıyordu — gateway sağlığı ve cihaz-link
  durumu panele hiç ulaşmıyordu. Migration eklendi (0039).
- **Sağlık yazımı artık kendi transaction'ında**: yazım hatası komut
  kanalının transaction'ını zehirleyemez — asıl kusur buydu; tablo olsa
  bile herhangi bir DB hatası aynı şekilde 500 üretirdi.

---

## [2.44.0] — 2026-08-04

### Eklendi

- **Sistem Durumu'nda aşama-aşama boru hattı görünümü**: ham kuyruk
  (normalize bekleyen) → işlenmiş kuyruk (öncelikli/toplu ayrı) → arşiv;
  oklarda tag-engine ve kalıcılaştırma hızları. Tek "bekleyen" sayısı,
  üst kuyruk alt kuyruğa boşalırken "kuyruk kendi kendine artıyor"
  yanılgısı yaratıyordu. Veri NATS monitor'den (NATS_MONITOR_URL,
  fail-soft: ulaşılamazsa panel eski görünüme düşer).

### Düzeltildi

- **tag-engine artık sinyal kataloğunu sınırlı süre bekleyip öyle başlıyor**
  (KATALOG_BEKLE_SEC, varsayılan 20 sn): katalog yüklü değilken büyük bir
  birikim boşaltılırsa tüm analog sel "bilinmeyen → öncelikli" kuralıyla
  öncelikli hatta yığılıyordu (sahada 3M boşaltmanın 1,58M'i).

---

## [2.43.2] — 2026-08-04

### Düzeltildi

- **tag-engine ~1.000 msj/sn'de tıkanıyordu** (300 cihaz testinde görüldü):
  her mesajda yayın onayı sırayla bekleniyordu. Yayın artık sınırlı
  eşzamanlılıkla paralel (TAG_PUBLISH_PARALLEL, varsayılan 512); teslim
  güvencesi (at-least-once) ve DLQ davranışı değişmedi.

---

## [2.43.1] — 2026-08-04

### Değişti

- Arşiv yönetimi penceresi yeniden tasarlandı: büyük arşiv sayısı +
  yazma yükü ölçüm çubuğu, etiketli bölümler (görünüm filtresi / toplu
  işlem), birleşik ölü bant girdi grubu ve açıklayıcı alt başlık.

---

## [2.43.0] — 2026-08-04

### Değişti

- **Gateway güç işlemleri popup menüye taşındı**: başlat/durdur/yeniden
  başlat düğmeleri satırda değil, tek "güç" düğmesinin açtığı menüde
  (kazara tıklamaya uzak; yalnızca installer görür).
- **Sinyaller sayfasında arşiv yönetimi popup'a taşındı**: özet, filtreler
  ve toplu işlemler "Arşiv yönetimi" düğmesinin açtığı pencerede; sayfada
  kompakt özet kalır.

---

## [2.42.1] — 2026-08-04

### Düzeltildi

- **2.42.0'da kalıcılaştırma işçisi ilk dolu partide çöküyordu** (yarım
  kalmış eski kod bloğu tanımsız isim kullanıyordu; canlı ekran akmaya devam
  ettiği için sorun panelde "Akış yok" uyarısıyla görünüyordu, veri NATS'ta
  birikip bekliyordu — kayıp yok). COPY toplu yazım entegrasyonu tamamlandı;
  arşiv/canlı/dedup satırları artık gerçekten tek geçişte yazılıyor.

---

## [2.42.0] — 2026-08-04

### Değişti

- **Arşiv yazımı toplulaştı (COPY)**: kalıcılaştırma ölçüm başına ayrı
  gidiş-dönüş yerine partiyi tek geçişte dört tabloya yazar (COPY +
  tek-ifade upsert). Bozuk satır partiyi düşürmez; ikiye bölünerek yalnızca
  gerçekten bozuk satır karantinaya alınır.
- **Dijital/analog hat ayrımı**: arıza/durum sinyalleri ve kalite geçişleri
  öncelikli hattan işlenir — analog ölçüm seli durum değişimlerini
  geciktirmez. Arıza/ikili sinyaller her değişimde arşivlenir; ölü bant
  yalnızca analog tipte uygulanır.

### Eklendi

- **Sinyaller sayfasında arşiv/ölü bant yönetimi**: hangi sinyalin
  arşivleneceği ve ölü bant eşiği panelden yönetilir.
- **Gateway başlat/durdur/yeniden başlat** (panelden, onaylı): durdurma
  onayı sonucu açıkça söyler (veri akışı duracak) ve olay kaydına yazılır.

### Düzeltildi

- Arka plan lider kilidinin bağlantısı süresiz "idle in transaction"
  bekliyordu; bu, tüm veritabanının VACUUM ufkunu sabitleyip yüksek devirli
  tabloları (canlı değerler, dedup defteri) şişiriyordu. Kilit artık
  transaction açık bırakmadan tutulur.

---

## [2.41.0] — 2026-08-04

### Değişti

- **Kalıcılaştırma backend API'den ayrıldı**: backend-api artık telemetri
  tüketmiyor; kalıcılaştırma ayrı worker sürecinde ve tag-engine çıkışından
  (NORMALIZED) besleniyor. Arşivdeki değer ile alarm/IEC104/Modbus'un gördüğü
  değer aynı normalizasyondan geçer; API süreci telemetri yükünden etkilenmez.
- **Gateway şablonları tek gateway'de 500 cihaza göre güncellendi**
  (`MAX_PARALLEL_DEVICES=500`; gateway imajı 1.2.0 ile birlikte). Panel
  "Güncelle" akışı mevcut kurulumların compose'unu yeniden üretince yeni
  değer sahaya iner.

---

## [2.40.0] — 2026-08-04

### Değişti

- **Gateway telemetrisi için NATS-direkt rota artık standart.** Paneldeki
  "Güncelle" düğmesi gateway compose'unu güncel NATS adresiyle yeniden üretir;
  NATS öncesi kurulan (veya anonim NATS adresli) gateway'ler HTTP yedek
  yolundan çıkıp telemetriyi doğrudan JetStream'e basar. Kurulumda seçilen
  imaj/port/adres değerleri korunur; imaj çekilemezse çalışan kuruluma
  dokunulmaz.

### Eklendi

- Telemetriyi HTTP yedek yolundan basmaya devam eden gateway için 10 dakikada
  bir uyarı loglanır — standart dışı çalışma (ve backend'e binen gereksiz yük)
  görünmez kalmaz.

---

## [2.39.0] — 2026-08-04

### Eklendi

- **Toast bildirimleri artık ayarlanabilir** (Proje Ayarları, kurulum geneli):
  konum seçimi ve kendiliğinden gelen bildirimleri susturma. Kullanıcının kendi
  işleminin sonucu (kaydedildi, hata, yetki, oturum) susturma açıkken de
  görünür — o mesajların başka kanalı yok.
- **Outbox dead-letter**: tekrar tekrar başarısız olan kayıt işaretlenip
  sıradan çıkıyor. Önceden tek bir "zehirli" kayıt tüm kuyruğu kilitliyordu.
- Arka plan işleri (telemetri tüketicisi, outbox yayıncısı) API sürecinden
  ayrıldı; API artık güvenle çoğaltılabilir.

### Düzeltildi

- **Veri kaybı: ölü bant, kalite ve arıza bayrağı geçişlerini yutuyordu.**
  Değer ölü bandın içinde kalırken `good → invalid / comm_lost / forced`
  geçişleri arşive hiç girmiyordu; ham kopyanın penceresi 30 dakika olduğu
  için kayıp kalıcı hale geliyordu. Ayrıca ölü bant ikili sinyallere de
  uygulanabiliyordu. Ölü bant artık yalnızca analog ölçümlerde ve karşılaştırma
  değer + kalite ikilisi üzerinden. Normal akışta ek yazım maliyeti sıfır.
- **Outbox temizliği üretimin gerisinde kalıyordu** (silme 1.000/sn, üretim
  1.074/sn) — tablo hiçbir zaman kararlı duruma gelmiyordu. Yeni kapasite
  üretimin ~15 katı.
- Sayfa kaydırma çubuğu: konumlanmamış ata yüzünden ekran okuyucu etiketleri
  sütun kırpmasından kaçıp belge yüksekliğini büyütüyordu.
- Hat segment kartı: "Cihaz Ekle" listenin üstüne alındı, liste kendi içinde
  kayıyor, kart açıldığı noktaya göre sınırlanıyor.

### Bilinen durum

- Telemetri boru hattındaki birikmenin kök nedeni **henüz bulunamadı**. Backend
  saniyede ~2.145 mesaj işliyor; sorun kendisine gelen mesaj sayısının beklenenin
  çok üzerinde olması. İnceleme sürüyor.

## [2.38.13] — 2026-08-03

### Düzeltildi

- **Arayüzün gövde yazı tipi kuralı hiç uygulanmıyordu — asıl sebep bulundu.**
  `styles.css` bir BOM (görünmez U+FEFF karakteri) ile başlıyordu ve `:root`
  dosyanın ilk kuralıydı; BOM dosya başındayken zararsızdır. Sonradan dosyanın
  üstüne CSS eklenince bu karakter dosyanın ortasına, `:root`un hemen önüne
  düştü. Satır ortasındaki U+FEFF artık BOM sayılmaz; selektöre yapışıp kuralı
  hiçbir elemana uymayan bir tip selektörüne çevirir. Sonuç: gövde yazı tipi,
  metin rengi ve arka plan rengi birlikte düşüyor ve tarayıcı varsayılanına
  (Chrome/Windows'ta Times New Roman — serif) geçiliyordu. Üst sekme çubuğu
  dahil tüm metinlerin yazı tipi bu yüzden değişmişti.
- Bu hata sınıfı için davranış testi eklendi: `styles.css` projenin kendi
  paketleyicisiyle derlenip `:root` kuralının çıktıda gerçekten canlı kaldığı
  doğrulanıyor. Kaynakta desen aramıyor — bu arıza tam olarak "kaynak doğru
  görünüyor ama tarayıcıda ölü" biçiminde ortaya çıktı.

## [2.38.12] — 2026-08-03

### Değişti

- **Ana sayfadaki cihaz listesi artık sayfa başına 20 cihaz gösteriyor** (önceki
  varsayılan 50). Sayfa boyutu seçenekleri: 20 / 50 / 100 / 200.
- **Sayfalama kontrolü yenilendi.** Düğmeler ayrı kutular yerine bitişik tek bir
  grup halinde; aktif sayfa marka rengiyle (turuncu) işaretleniyor — önceki mor
  vurgu arayüzün geri kalanıyla uyumsuzdu. Sayfa boyutu seçici de özel ok
  simgesiyle sadeleştirildi. Sayılar tablo rakamlarıyla dizildiği için sayfa
  değiştikçe genişlik oynamıyor.

## [2.38.11] — 2026-08-03

### Değişti

- **Sistem Durumu KPI kartlarının tipografisi v2.25.0 değerlerine döndürüldü**
  (etiket 11px/0.06em, değer 1.6rem/-0.02em, kesir 1.05rem). Arayüzün tamamı
  artık v2.25.0 ile aynı yazı tipi ölçülerine sahip.

## [2.38.10] — 2026-08-03

### Değişti

- **Arayüz tipografisi v2.30.0 ile birebir aynı hale getirildi.** Eski sürüm
  incelendi: o sürümde gövde için tek bir tanım vardı (`Arial, sans-serif`),
  gömülü bir yazı tipi ya da dış font bağlantısı yoktu. `:root` bloğu artık
  v2.30.0 ile karakter karakter aynı; Manrope paketten çıkarıldı.

## [2.38.9] — 2026-08-03

### Eklendi

- **Kiosk açılış ekranı artık dinamik.** Müşteri logosu (varsa) ve müşteri adı
  ortada, EnerjiOne Grid kimliği sol altta (giriş ekranıyla aynı dil), müşteri
  ve sürüm bilgisi sağ altta gösteriliyor. Değerler her açılışta çözülür;
  dosyaya gömülmediği için sürüm/port değişince bayatlamaz.
- Müşteri logosu uygulama ayağa kalktıktan sonra arka planda diske
  önbelleklenir; ilk açılışta henüz yoktur, sonraki her açılışta görünür.
  (Logo veritabanında tutulduğu için açılış ekranı anında erişemez.)

### Değişti

- "İlk kurulum uzun sürebilir" ara mesajı kaldırıldı.

## [2.38.8] — 2026-08-03

### Değişti

- **Arayüz yazı tipi 2.30'daki haline döndürüldü.** Gövde fontu yeniden Arial
  (Ubuntu'da Liberation Sans) tabanlı.
- **Kiosk açılış ekranı** artık açık renkli E1 logosunu kullanıyor ve metinler
  düzgün Türkçe karakterlerle yazılıyor ("Sistem başlatılıyor…").

### Düzeltildi

- **Aynı sayfada karışık yazı tipi.** Yedek zincirinde `Helvetica Neue` vardı;
  bu ad Ubuntu'da karşılıksızdır ve URW/Nimbus paketleri kurulu değilse
  fontconfig onu serif bir yüze eşleştirebiliyor. Belirtisi, bazı başlıkların
  serif, gövdenin sans görünmesiydi. Zincirde artık yalnızca hedef sistemde
  karşılığı olan adlar var.

## [2.38.7] — 2026-08-03

### Düzeltildi

- **Temiz kurulumda arşiv tablosu hypertable'a çevrilmiyor, saklama süresi
  politikası kurulmuyordu.** 2.38.4'te temiz kurulum şemayı modellerden tek
  adımda kuracak şekilde değiştirilmişti; bu, kurulumu çökerten sorunu çözdü
  ancak `create_all` yalnızca düz tabloları oluşturur. Hypertable'a çevirme,
  90 günlük saklama, sıkıştırma ve özet katmanları yalnızca migration
  gövdesinde yaşadığı için sessizce atlanıyordu — Sistem Durumu sayfasındaki
  "tablo sınırsız büyüyor" uyarısı bunun belirtisiydi. Depolama kurulumu artık
  şemadan ayrı, idempotent bir adım olarak **her açılışta** çalışıyor; eksik
  olanı tamamlıyor, kurulu olana dokunmuyor. Mevcut kurulumlarda da kendini
  onarır; elle müdahale gerekmez.

## [2.38.6] — 2026-08-03

### Düzeltildi

- **Kiosk açılış ekranından uygulamaya geçilmiyordu.** Açılış ekranı uygulamayı
  yoklayıp hazır olunca kendiliğinden yönleniyor; ancak yoklanan adres
  `http://localhost/` olarak sabitti. Arayüzün yayınlandığı port `.env`
  içindeki `FRONTEND_HTTP_PORT` ile değişebiliyor (host'un 80 portu
  host-nginx'teyse kurulum bunu 8080 yapar) ve o durumda yoklama hiçbir zaman
  başarılı olmuyor, operatör açılış ekranında süresiz bekliyordu. Adres artık
  her oturumda yapılandırmadan okunuyor; kurulumda adres açıkça verildiyse ona
  dokunulmuyor.

## [2.38.5] — 2026-08-03

### Düzeltildi

- **Arayüz yazı tipi sistem fontuna düşüyordu.** Manrope pakete gömülüydü ve
  dosyalar doğru yayınlanıyordu, ancak `@font-face` kuralında standart dışı bir
  format değeri (`woff2-variations`) kullanılmıştı. Tarayıcı tanımadığı formatta
  kaynağı atlar, font hiç indirilmez ve sessizce yedek yazı tipine düşülür —
  konsolda hata, ağ sekmesinde başarısız istek görünmez. Arayüz artık her
  sayfada Manrope ile açılıyor.
- **Kiosk açılış ekranı "File not found" veriyordu.** Geçiş ekranı
  `/usr/local/share` altında tutuluyordu; Ubuntu'da Firefox bir snap paketi
  olduğu için sandbox bu dizini göremiyor. Dosya diskte duruyor olmasına rağmen
  operatör ekranında tarayıcı hata sayfası çıkıyordu. Açılış ekranı artık
  oturum başında kullanıcının ev dizinine kopyalanıp oradan açılıyor; snap
  olmayan tarayıcılarda eski konum yedek olarak korunuyor.

## [2.38.4] — 2026-08-03

### Düzeltildi
- **Temiz kurulum tamamlanamıyordu — asıl sebep bulundu.** Backend, boş bir
  veritabanında şemayı güncel hâliyle bir kerede kuruyor; ancak ardından
  geçmiş şema adımlarını da baştan uygulamaya çalışıyordu. Şema zaten
  eksiksiz olduğu için ilk alan ekleyen adım çakışıp hata veriyor, backend
  açılamıyor ve kurulum *"backend-api is unhealthy"* diyerek duruyordu.

  2.38.3'te bu adımlardan biri düzeltilmişti; ancak aynı riski taşıyan
  sekiz adım daha vardı, yani sorun bir sonraki adımda tekrarlayacaktı.
  Bu sürümde kaynak düzeltildi: boş veritabanında geçmiş adımlar artık
  hiç tekrarlanmıyor.

  Mevcut kurulumlar etkilenmez; onlarda şema adımları eskisi gibi
  sırayla uygulanmaya devam eder.

---

## [2.38.3] — 2026-08-03

### Düzeltildi
- **Önceki bir kurulum denemesinden veri kalmış cihazlarda kurulum
  tamamlanamıyordu.** Backend açılışta veritabanı şemasını güncelliyor,
  ardından geçmiş şema adımlarını sırayla uyguluyor. Bir adım, zaten var
  olan bir alanı yeniden eklemeye çalışıp hata veriyor; backend açılamıyor
  ve kurulum *"backend-api is unhealthy"* diyerek duruyordu.

  Cihaz kalıcı olarak kilitleniyordu: her yeniden deneme aynı noktada
  patlıyordu. Temiz veritabanında görülmediği için "bir sunucuda oluyor,
  diğerinde olmuyor" şeklinde ortaya çıkıyordu.

  İlgili adım artık alanın zaten var olduğunu görünce atlıyor.

---

## [2.38.2] — 2026-08-03

### Düzeltildi
- **Sürüm yayınlama akışı tamamlandı.** 2.38.1'deki düzeltme yetersizdi:
  kesme işlemi başka bir komuta taşınmıştı ama liste yine erken
  kapatılıyordu, bu kez paketi okuyan araç hata veriyordu. Artık liste
  sonuna kadar okunuyor, yalnızca gösterim sınırlanıyor.

  Servis imajları 2.38.0'dan beri zaten doğru yayınlanıyordu; eksik olan
  kurulum paketi ve sürüm kaydıydı.

---

## [2.38.1] — 2026-08-03

### Düzeltildi
- **Sürüm yayınlama akışı tamamlanamıyordu.** 2.38.0'da servis imajlarının
  tümü başarıyla yayınlandı, ancak kurulum paketini üreten adım hata verdiği
  için sürüm kaydı oluşmadı.

  Sebep, paket içeriğini özetleyen bir satırdı: liste ilk 25 kalemden sonra
  kesiliyor, kesilen tarafta kalan komut yazamayıp hata döndürüyor ve bu
  tüm adımı düşürüyordu. Hata **zamanlamaya bağlı** olduğu için bazen
  görünüyor bazen görünmüyordu; bu yüzden geliştirme makinesinde tekrar
  edilemiyordu.

  Aynı tuzağın bulunduğu iki yer daha düzeltildi: kaldırma betiğinin yardım
  ekranı ve kurulum sırasında geçersiz sürüm adı girildiğinde gösterilen
  sürüm listesi (ikincisi, hata anında ikinci bir hata üretiyordu).

---

## [2.38.0] — 2026-08-03

### Eklendi
- **Kurulum yarıda kalırsa yaptıklarını geri alıyor.** Önceden bir adımda
  düşünce cihazda yarım bir kurulum kalıyordu: container'lar ayakta, ayar
  dosyası üretilmiş, ama sistem çalışmıyor. Tekrar denendiğinde hangi
  parçanın eski hangisinin yeni olduğu belli olmuyordu.

  > **Mevcut veriler korunur.** Var olan bir kurulumun üzerine yapılan
  > denemede telemetri, olay kayıtları ve yedekler **silinmez**; yalnızca o
  > koşumun oluşturdukları geri alınır.

- **Kurulum hata verdiğinde sebebi ekranda görünüyor.** Önceden yalnızca
  "logları inceleyin" deniyor ve komut veriliyordu; artık sorunlu servisin
  son satırları doğrudan basılıyor.

### Değişti
- **Arayüz yazı tipi (Manrope) uygulamaya gömüldü.** Önceden internetten
  indiriliyor sanılıyordu; aslında hiç yüklenmiyor ve sistem yazı tipine
  düşülüyordu. Artık cihazın internet erişimi olmasa da — erişim noktası
  modunda doğrudan cihaza bağlanıldığında da — arayüz doğru görünür.

- **Ağ Ayarları ve Uzaktan Bakım sayfalarında bildirimler artık ekranın
  köşesinde beliriyor.** Önceden sayfanın ortasına satır olarak ekleniyor,
  her göründüğünde alttaki kartlar aşağı kayıyordu.

  Kalıcı durumlar (örneğin cihaza ulaşılamaması) kaybolmuyor: üst şeritte
  görünmeye devam ediyor, çünkü geçici bir bildirim kaybolduktan sonra
  sayfa "her şey yolunda" gibi görünürdü.

- **Uzaktan Bakım sayfası sadeleştirildi.** İşlem sürerken aynı bilgiyi iki
  yerde birden yazan mükerrer satır kaldırıldı; süre seçimi ve durum
  gösterimi yenilendi.

### Düzeltildi
- **WiFi kartı, sistemde ne varsa ona göre algılanıyor.** Bazı cihazlarda
  kart takılı olduğu hâlde "WiFi kartı yok" deniyor ve erişim noktası /
  ağa bağlanma seçimi kilitli kalıyordu. Özellikle USB WiFi adaptörlerinde
  görülüyordu.

  Kart bulunup da ağ yöneticisi tarafından tanınmadığı durum artık ayrıca
  belirtiliyor ve ne yapılacağı yazıyor — "kart yok" demek yerine.

- Gateway ayarlarında DNP3 kalite bayrağı seçeneğinin kutusu başlığın
  üstünde tek başına duruyordu; artık başlığın solunda.

---

## [2.37.0] — 2026-08-03

### Güvenlik
- **Uzaktan erişim izni yokken cihaz artık uzaktan erişim ağına bağlı
  kalmıyor.** Önceden cihaz ağda duruyor, yalnızca gelen bağlantıları
  reddediyordu. Teknik olarak güvenliydi ama erişimi engelleyen tek şey
  yazılımın kendi kararıydı; müşteri "girilmiyor" sözüne güvenmek zorundaydı.

  Artık izin verilmediği sürece cihaz **ağdan çıkıyor**. Müşteri bunu kendi
  güvenlik duvarında "hiç trafik yok" diye doğrulayabilir.

  İzin verildiğinde cihaz ağa yeniden bağlanır, ağdaki diğer cihazlardan
  erişilebilir olur ve (seçilmişse) SSH açılır. Süre dolduğunda bağlantı
  düşer ve açık oturumlar kopar. Cihazın ağ kaydı hiçbir zaman silinmez;
  izin verilince sahaya gitmeden geri gelir.

  > Bunun bir bedeli var: erişim kapalıyken cihaz konsolda çevrimdışı görünür
  > ve "elektrik yok", "internet yok", "cihaz arızalı", "izin verilmemiş"
  > birbirinden ayırt edilemez. Canlılık bilgisinin şart olduğu kurulumlar
  > eski davranışa dönebilir.

- **İzin verildiği hâlde bağlanılamadığında ekran artık bunu söylüyor.**
  Cihaz izni alıp da tünele bağlanamazsa (en sık sebebi internet erişiminin
  olmaması) sayfa nedeni açıklıyor. Önceden bu durum sessizce geçiliyordu:
  "İzin ver"e basılıyor, hiçbir şey olmuyordu.

### Değişti
- **Ağ Ayarları sayfasındaki WiFi bölümü sadeleşti.** Kart aç/kapat, kartın
  görevi ve ölçülen durum ayrı bir **WiFi ayarları** penceresine taşındı.
  Panelde tek satırlık bir özet kaldı: WiFi kartı, kartın görevi ve **bağlı
  ağ** (ad + sinyal). Ağ listesi artık ekranın dışına düşmüyor.

  Bağlı ağ ölçüme dayanır: kayıtlı ama bağlı olmayan bir profil "bağlı"
  gösterilmez, *"(bağlı değil)"* yazar.

  Geçici uyarılar (ağ değişimi sırasında bağlantı kopma bildirimi, cihazın
  kendi ağını geri açtığı durum, hata satırı) pencere kapalıyken de görünsün
  diye panelde bırakıldı.

### Düzeltildi
- **"Cihazdaki her şeyi sil" başarıyla bitiyor ama "işlem başarısız"
  diyordu.** Kaldırma sonuna kadar tamamlanıyor, yalnızca son bilgi satırı
  hatalı biçimlendiği için betik hata koduyla çıkıyordu. Operatör bir
  şeylerin silinmeden kaldığını sanıyordu.

- **Kaldırma sonrası sistemde bilerek ne bırakıldığı artık yazılıyor**
  (Docker Engine, yönetim hesabı, ağ ayarı yedekleri...). Önceden neyin
  kasıtlı neyin arıza olduğu anlaşılmıyordu.

- **Kurulum aracında cihazın kendi WiFi ağı listelenmiyor.** Erişim noktası
  modunda başka ağ görünmediğinde tek seçenek cihazın kendi ağı oluyordu;
  seçildiğinde cihaz kendine bağlanmaya çalışıp erişim noktasını düşürüyor
  ve kurulum kilitleniyordu.

- Adım sayacı `--purge-all` ile fazladan adım eklendiğinde "[8/7]" gibi
  tutarsız görünüyordu.

---

## [2.36.0] — 2026-08-02

### Eklendi
- **DNP3 kalite bayrakları artık gateway ayarlarından açılabiliyor.** Gateway
  bugüne kadar her ölçümü "iyi" olarak yayınlıyordu. Bir gösterge akım
  ölçümünü *geçersiz* diye raporladığında (örneğin CT referansını kaybettiğinde
  0 A bildirdiğinde) bu bilgi kayboluyor, SCADA değeri geçerli sanıyordu —
  "hat enerjisiz" yorumu ve buna dayalı yanlış manevra kararı mümkündü.

  Açıldığında geçersiz ölçümler **alarm değerlendirmesine girmez**: alarm
  durumu donar, o ölçümle ne yeni alarm açılır ne açık alarm kapanır.

  > Anahtar **gateway başına**. Açmak saha davranışını değiştirdiği için önce
  > tek bir gateway'de denenip yaygınlaştırılabilsin diye filo geneli tek
  > anahtar yapılmadı. Varsayılan kapalı; mevcut kurulumların davranışı
  > değişmiyor. Kaydedince gateway kurulumu tazelenir ve kısa süre telemetri
  > gelmez.

- **Yeni cihaz modeli sürüm çıkarmadan eklenebiliyor.** Model listesi artık
  sinyal kataloğundan da besleniyor: yeni bir modelin sinyallerini tanımlamak
  onu cihaz formunda seçilebilir kılmaya yetiyor. Önceden sinyalleri
  girebiliyor ama modeli hiçbir cihaza atayamıyordunuz.

- **Gateway güncellemesinde ilerleme görünüyor.** Butona basınca "İstek
  gönderiliyor → Yeni imaj indiriliyor → Güncel imajla başlatılıyor →
  Tamamlandı" akışı ekranda takip ediliyor. Önceden ekran sessiz kalıyor,
  işin başlayıp başlamadığı anlaşılmıyordu. Hata olursa nedeni gösteriliyor.

- **Hangi sürümün geldiği yazıyor.** Çalışan sürüm her durumda görünüyor;
  güncelleme varsa hedef sürüm adıyla belirtiliyor.

### Değişti
- **Sistem Durumu sayfasının üst kısmı yenilendi.** Sayfa başlığı kaldırıldı
  (sekme zaten söylüyor) ve dağınık duran üç grup tek bir gösterge şeridinde
  toplandı: canlı veri durumu → makine/çalışma süresi/son örnek → sürüm →
  yenile. Sayaç kartları sadeleştirildi.

### Düzeltildi
- **Gateway kurulu saha cihazlarında güncelleme durmuyor.** (2.35.1'de
  düzeltilmişti; bu sürümde de geçerli.)
- Gateway ajanı hataları artık ham kod yerine anlaşılır mesaj döndürüyor
  ("request_pending" yerine "Önceki istek hâlâ uygulanıyor").

---

## [2.35.1] — 2026-08-02

### Düzeltildi
- **Gateway kurulu saha cihazlarında güncelleme başlamıyordu.** Gateway
  ajanı kurulumu, repo dizininin içine `gateways/` adında bir çalışma-zamanı
  dizini açıyor. `update.sh` ise güncellemeden önce çalışma ağacının temiz
  olmasını şart koşuyor ve bu dizin `.gitignore` kapsamında olmadığı için
  güncelleme *"Repo'da commit edilmemiş lokal değişiklik var: `?? gateways/`"*
  diyerek duruyordu.

  Tek bir cihazda elle temizlenip geçilecek bir sorun değildi: dizin her
  kurulumda yeniden oluşuyor, dolayısıyla **her güncellemede** tekrarlıyordu.

  Aynı dizindeki dosyalar gateway erişim anahtarı taşıdığı için yok
  sayılması ayrıca güvenlik gereği.

---

## [2.35.0] — 2026-08-02

### Eklendi
- **Cihaz haberleşme durumu artık gateway'in bildirdiği link durumundan da
  belirleniyor.** Önceden bir cihazın "canlı" sayılması yalnızca telemetri
  gelmesine bağlıydı. Arıza bekleyen bir gösterge saatlerce hiçbir şey
  yayınlamayabilir — değer değişmiyorsa gateway veri göndermez. Bu süre
  boyunca cihazın canlı mı kopuk mu olduğu **bilinmiyordu**.

  "Veri gelmiyor" ile "haberleşme koptu" aynı şey değil. Bu ayrımı yapabilen
  tek yer gateway; DNP3 link durumu orada tutuluyor. Gateway bu bilgiyi
  saniyede bir zaten attığı istekle gönderiyor, ek yük yok.

  > Çalışması için gateway'in de güncel olması gerekir (gateway ≥ bu sürümle
  > birlikte yayınlanan imaj). Eski gateway'de davranış aynen eskisi gibi.

- **Arşiv ölü bantları GPS, sinyal seviyesi ve açı ölçümlerine genişletildi.**
  Konum bileşenlerinde eşik hareket büyüklüğünde (~11-18 m): cihaz direkte
  sabit durduğu sürece tek satır yazılır, gerçekten oynarsa kaydedilir —
  "ne zaman oynadı" sorusu (hırsızlık, direk hasarı, yanlış montaj) cevapsız
  kalmasın diye arşivden çıkarılmadı. Telsiz sinyal seviyesinde 2 dBm, açı
  ölçümlerinde 1° gürültü bandı.

  `fault_duration` bilerek eşiksiz bırakıldı: her değer ayrı bir arızanın
  süresidir, ölü bant ardışık benzer süreli arızalardan birini silerdi.

### Düzeltildi
- **TLS'siz saha cihazında harita hiç açılmıyordu.** Oturum çerezi `Secure`
  işaretleniyordu; cihaz `http://enerjione.local` üzerinden kullanıldığı için
  tarayıcı o çerezi göndermiyordu. Normal API çağrıları kurtuluyordu (ayrıca
  Bearer başlığı gidiyor), ama harita karoları `<img>` ile isteniyor ve `<img>`
  başlık gönderemez — her karo 401 alıyordu, indirilmiş çevrimdışı önbellek
  dahil. Artık bayrak isteğin şemasına bağlı.

- **Askıda kalan servisler kendini toparlıyor.** `restart: unless-stopped`
  yalnızca çıkış yapan süreci geri kaldırır; ana döngüsü kilitlenen bir worker
  "çalışıyor" görünür — container ayakta, süreç ayakta, ama telemetri sessizce
  akmayı bırakmıştır. Başında kimse olmayan bir saha cihazında fark edilmesi
  en zor arıza buydu.

- **Disk dolması.** Yeniden teslim defteri (`processed_messages`) 24 saat
  yerine 2 saat tutuluyor — gerçek yeniden teslim penceresi 10 dakika, 24 saat
  onun 144 katıydı. Alt sınır artık kodda kilitli: defteri mesaj hâlâ yeniden
  teslim edilebilirken silmek yinelenen telemetri yazdırırdı.

- **NATS akış yaş sınırları artık ayarlanabiliyor.** Üç ayar kodda tanımlıydı
  ama compose'dan geçirilmiyordu; operatör değiştiremiyordu.

### Güvenlik
- Vite 6 ve pytest 9 yükseltmeleri.

---

## [2.34.0] — 2026-08-01

### Eklendi
- **Gateway sürüm kontrolü ve güncelleme butonu.** Mühendislik > Gateway'ler
  ekranında her gateway için sürüm durumu görünüyor: *Yeni sürüm var*,
  *Güncel* ya da *Sürüm bilinmiyor*. Güncelleme tek tıkla yapılıyor; yeni
  imaj indirilemezse çalışan sürüme dokunulmuyor.

  Buton onay soruyor: gateway yeniden başlarken ona bağlı cihazlardan kısa
  süre telemetri gelmez.

  **"Sürüm bilinmiyor" ayrı bir durumdur.** Kayıt defterine ulaşılamadığında
  "güncel" göstermiyoruz — bu, sormadan verilmiş bir iddia olur ve operatör
  eski sürümde kaldığını fark etmezdi.

- **Yeni gateway sürümü çıktığında tüm kullanıcılara bildirim.** Bildirim
  sürüm başına **bir kez** gönderiliyor; aksi halde operatör güncelleyene
  kadar sürekli tekrarlar ve gerçek uyarılar bu yığının içinde kaybolurdu.

---

## [2.33.0] — 2026-08-01

Sahada görülen bir "ağ kararsız" şikâyetinin kökü bulundu ve kaynağı kapatıldı.

### Düzeltildi
- **Aynı gateway'de iki cihaza aynı IP:port verilebiliyordu.** Horstmann
  cihazı yeni bir bağlantı geldiğinde mevcut olanı kapatır; aynı adrese iki
  cihaz bağlanınca sırayla birbirlerini atarlar. Gateway günlüğünde **2.172
  bağlantı kapanması** birikmişti ve belirti "ağ kararsız, cihazlar kopuyor"
  gibi görünüyordu — oysa tek bir yanlış port alanıydı. Adres düzeltildikten
  sonra 15 dakikada sıfır kopma oldu. Artık hem cihaz eklerken hem
  **düzenlerken** engelleniyor ve hata mesajı sonucu açıklıyor.

- **Akım sinyalleri iki farklı birimde tutuluyordu.** `actual_current`
  ampere çevriliyor, diğer altı akım sinyali (trip level, min/maks/ortalama/
  arıza/son bilinen akım) miliamper olarak bırakılıyordu. Aynı cihazda aynı
  büyüklük 1000 kat farklı görünüyor, bu sinyallere kurulan alarm eşikleri
  diğerleriyle kıyaslanamıyor ve IEC 104 / Modbus çıkışlarına tutarsız
  ölçekle gidiyordu. Hepsi ampere çevrildi.

  **Dikkat:** eski arşiv kayıtları eski ölçekte kalıyor; bu altı sinyalin
  grafiğinde güncelleme anına denk gelen bir basamak görünür.

### Eklendi
- **Gateway susarsa cihazlar artık yeşil kalmıyor.** Cihaz durumu yalnızca
  telemetri geldiğinde güncelleniyordu; gateway tamamen sustuğunda tüm
  cihazlar son durumlarında donuyor ve harita sağlıklı görünmeye devam
  ediyordu. Gateway üç dakikadır görülmediyse cihazların durumu artık
  **"bilinmiyor"** olarak işaretleniyor — "çevrimdışı" değil, çünkü cihazlar
  çalışıyor olabilir ve yalnızca haber ulaşamıyordur.

  Cihaz bazlı "veri gelmiyor" kontrolü **bilerek yapılmıyor**: gateway
  yalnızca değişen değerleri yayınladığı için durağan bir fiderde yanlış
  alarm üretirdi.

---

## [2.32.0] — 2026-08-01

Saha test cihazında **15 cihazla canlı yük altında** yapılan ölçümlerden doğdu.
Okuma başına ~6,4 satır işlemi yapılıyordu; bu sürüm bunun büyük kısmını
kaldırıyor.

### Değişti
- **Historian artık seçici.** Gerçek SCADA pratiğinde her tag arşive
  yazılmaz: anlık değer her zaman güncel tutulur, arşive yalnızca
  işaretlenen tag'ler ölü bant süzgecinden geçerek yazılır. Bu sistemde iki
  ön koşul da zaten sağlanıyordu — alarm motoru akış tabanlı çalışıyor
  (geçmiş sorgusu yapmıyor) ve canlı değer ayrı bir tabloda — dolayısıyla
  **alarm doğruluğu etkilenmiyor**.

  Arşivden çıkarılanlar: seri numarası, firmware sürümü, donanım revizyonu,
  SIM CCID, GPS gibi ömür boyu sabit metadata (30 sinyal) ve
  `config_update` / `firmware_update` / `trigger_*` gibi komut noktaları
  (18 sinyal). Bunların zaman serisi hiçbir soruya cevap vermiyordu.

  Ölü bant varsayılanları: akım 0.5 A, gerilim 1 V, sıcaklık 0.5 °C.
  Miliamper birimli akım sinyalleri bilerek kapsam dışı bırakıldı (ayrıca
  belirlenecek). Her sinyal için ayrı ayrı ya da toplu kapatılabilir.

- **Alarm kendiliğinden temizlendiğinde ayrıca olay kaydı düşülmüyor**
  (varsayılan). Dalgalanan bir sinyal dakikalar içinde binlerce
  tetiklen/temizlen çifti üretiyor ve gerçek operatör olayları — yetki
  kullanımı, komut gönderimi, ayar değişikliği — bu yığının içinde
  kayboluyordu. Bilgi kaybı yok: temizlenme zaten alarm kaydının kendisinde
  duruyor ve alarm geçmişi oradan okunuyor. **Onaylanmış** bir alarm
  temizlendiğinde kayıt her zaman yazılmaya devam ediyor, çünkü orada alarm
  satırı siliniyor ve olay kaydı geriye kalan tek iz.

### Düzeltildi
- **IEC 104 açıkken her telemetri okuması için boşa iş yapılıyordu.** Nokta
  güncellemesi başına bir denetim kaydı oluşturuluyor ama hiçbir zaman
  kaydedilmiyordu (çağıran taraf oturumu kaydetmeden kapatıyor). Saniyede
  yüzlerce nesne kurulup atılıyordu. Kaydedilseydi daha kötü olurdu: denetim
  kaydı 2 yıl saklanıyor ve 15 cihazlık test kurulumunda bile günde 32
  milyon satır demekti.

- **SCADA istemcisi bağlantıyı kapattığında hata günlüğüne "çöktü" yazılıyordu.**
  Normal bağlantı sonlanması yakalanan kopma tiplerinden biri değildi;
  her oturum sonunda tam bir hata izi basılıyor, gerçek arızalar bu
  gürültünün içinde kayboluyordu.

- **Cihaz "son veri" zamanı her okumada yazılıyordu.** 15 cihazlık kurulumda
  saniyede ~55 güncelleme demekti; alanın tek tüketicisi arayüzdeki
  "Son veri: X önce" göstergesi. Birkaç saniyelik eşikle yazma yükü ~%95
  düştü, ekranda hiçbir fark yok. Cihazın çevrimiçi olup olmadığı bilgisi
  **kısılmadı** — o anında yazılmaya devam ediyor.

- **Yayınlanmış outbox kayıtları 15 dakika saklanıyor** (önceden 1 saat,
  ondan önce 24 saat). Süre tahminle değil ölçülen yeniden teslim
  penceresinden türetildi ve kod artık o eşiğin altına inilmesine izin
  vermiyor.

---

## [2.31.0] — 2026-08-01

Saha test cihazında yapılan **ölçümlerden** doğan sürüm. Önceki sürümde
kapatılamayan "yeşil yalan" sınıfı bitirildi, IEC 104 reset komutu eklendi ve
diski dolduran bir tablo bulundu.

### Düzeltildi
- **Kuyruk arızası cihazı tamamen karartıyordu.** `/health` NATS'ı kritik
  sayıp 503 dönüyor, `frontend-web` compose'da `service_healthy` beklediği
  için **arayüz hiç başlamıyordu**. Yani NATS'taki tek bir yanlış
  yapılandırma (ör. yarım uygulanmış TLS) 80 portunda hiçbir şey
  bırakmıyordu. Oysa kuyruk çökse bile giriş, yetkilendirme, ayarlar, geçmiş
  veri, arıza listesi, yedekleme ve uzaktan bakım çalışmaya devam eder.
  Artık kritiklik sınırı yalnızca Postgres; NATS/RabbitMQ düşüşü
  `status="degraded"` + `degraded_reasons` ile **açıkça** raporlanır.

- **`outbox_events` tablosu diski dolduruyordu.** Ölçüm: saatte 326.027 satır
  / 272 MB — veritabanının en büyük tablosu, telemetrinin kendisinden (65 MB)
  dört kat büyük. 24 saatlik saklama süresi ölçülen oranda **7,8 milyon satır
  / ~6,5 GB** demekti; cihazda 8,9 GB boş disk vardı, yani sürekli yük
  altında ~1,5 günde doluyordu. Saklama 1 saate çekildi, hiç taranmayan iki
  indeks kaldırıldı ve `published` indeksi kısmi indekse çevrildi (aynı sorgu
  93 buffer → 1 buffer).

- **Bilinmeyen durum yeşil "Normal" görünüyordu.** Cihaz detay ekranlarındaki
  arıza rozetleri "veri yok", "gerçekten normal" ve "haberleşmesi kopuk
  cihazdan gelen 0.0" için **aynı yeşil rozeti** üretiyordu. Üçüncüsü en
  ağırıydı: sunucu o okumayı alarm değerlendirmesine zaten sokmuyor, arayüz
  onun kararını geçersiz kılıyordu. Artık güvenilmeyen ölçüm nötr "Veri yok"
  / "Güvenilmez" rozeti alıyor.

- **Canlı veri rozeti hiç görünmüyordu.** Soket durumu iki sayfaya
  geçiriliyor ama ikisinde de okunmuyordu; soket ölse bile ekranda hiçbir
  işaret çıkmıyor, bayat değerler sessizce duruyordu. Rozet artık görünür ve
  soket durumunu değil **veri akışını** gösteriyor — sunucu 30 sn'de bir ping
  attığı için soket, gateway tamamen sussa bile açık kalıyordu.

- **Proxy bozulunca harita tamamen kararıyordu.** Karolar çevrimdışı önbellek
  için backend üzerinden geçiyor; nginx yönlendirmesi ya da backend
  bozulduğunda tarayıcıda internet olsa bile harita boş kalıyordu. Artık
  proxy'den karo gelmeyince doğrudan yukarı akışa düşülür.

- **NATS TLS yarım kalabiliyordu.** TLS'in çalışması üç şeyin aynı anda doğru
  olmasına bağlı; biri eksik kalırsa arıza **sessiz** oluyordu, çünkü NATS'ın
  kendi healthcheck'i TLS'siz izleme portunu prob ediyor ve container
  "healthy" görünüyordu. Ayrıca sertifika dizini yalnızca sunucuya mount
  ediliyordu; istemciler CA dosyasını bulamıyordu.

- **`OUTBOX_*` ayarları hiç uygulanmıyordu.** Beş ayar belgeliydi ama
  compose'da listelenmediği için container'a ulaşmıyordu; operatör `.env`'e
  yazsa da hiçbir şey değişmiyordu.

### Eklendi
- **IEC 104 üzerinden arıza göstergesi reset komutu** (`C_SC_NA_1`). Kapsam
  bilinçli olarak dar: kabul edilen tek kontrol komutu budur. Komut, arayüzden
  gelenle aynı yetki/allowlist/denetim yolundan geçer ve "kabul edildi"
  (ACT_CON) ile "cihazda gerçekleşti" (ACT_TERM) ayrı raporlanır — komut NAT
  arkasındaki gateway'e config-poll ile gittiği için arada dakikalar olabilir.

- **Frontend'in ilk otomatik testleri.** Yeni bağımlılık eklenmeden
  (esbuild + Node'un yerleşik test koşucusu) ve CI'da çalışıyor.

### Güvenlik
- IEC 104 komut kapsamı **iki katmanlı** doğrulanıyor. Tip filtresi tek başına
  yetmezdi: sinyal kataloğu düzenlenebilir olduğu için `firmware_update` gibi
  bir noktaya komut tipi verilmesi, tek katmanlı bir tasarımda uzaktan
  firmware tetiklemeye dönüşürdü.

---

## [2.30.0] — 2026-08-01

600 cihaz ölçeği için yapılan ikinci denetimin **Faz 1 ve Faz 2'sinin tamamı**
(16 madde) ve önceki denetimden kalan engelleyiciler kapatıldı.

### Güvenlik
- **WiFi erişim noktasından SCADA ve mesajlaşma portları açıktı.** Appliance'ın
  şifresiz WiFi ağına bağlanan biri, kimlik doğrulaması olmadan Modbus (502) ve
  IEC 104 (2404-2406) üzerinden tüm sahanın arıza/konum/ölçüm durumunu
  okuyabiliyor; NATS ve RabbitMQ'yu da brute-force için bulabiliyordu. Artık
  AP arayüzünde yalnızca web arayüzü (80/443) erişilebilir.
- **İstemci IP'si uydurulabiliyordu.** `X-Forwarded-For` başlığı zincire
  olduğu gibi giriyor ve backend en soldaki — yani istemcinin yazdığı —
  değeri okuyordu. Bu IP üç yerde güvenlik kararıydı: API anahtarı IP
  kısıtlaması **tek bir başlıkla atlanıyordu**, hız sınırı aşılabiliyordu ve
  denetim kayıtlarına yanlış IP yazılıyordu.
- **Gateway token'ı değiştirildiğinde eski token çalışmaya devam ediyordu.**
  Yeni token 401 alıyor, eskisi geçerli kalıyordu; yani "sızdı, değiştirelim"
  amacıyla yapılan işlem tam tersini yapıyordu.
- **Zorunlu şifre değişimi WebSocket'te uygulanmıyordu.** Varsayılan kurulum
  parolasıyla giren biri arayüzden engelleniyor ama canlı telemetri akışına
  erişebiliyordu.
- **Kilitlenen hesabın açılma yolu yoktu.** Şifre sıfırlama kilide
  dokunmuyordu; tek installer hesabı kilitlenince gateway ekleme, ağ ayarı,
  yedek ve uzaktan bakım birlikte kilitleniyor, çözüm saha ziyareti oluyordu.
- **Root ajanlar symlink takip ediyordu.** Koruma yalnızca dosya adını
  kapsıyordu; dizin bileşenleri açıktı ve bu yolla cihaz kalıcı olarak
  açılamaz hale getirilebiliyordu.

### Düzeltildi
- **Açık arızalar listeden ve haritadan kaybolabiliyordu.** Alarm listesi 500
  kayıtla sınırlıydı ve sınır, sorumluluk alanı süzgecinden önce
  uygulanıyordu. Eski ama hâlâ açık bir arıza pencerenin dışına düşünce
  haritadaki işaret yeşile dönüyordu. Artık açık alarmlar hiç kırpılmıyor ve
  süzgeç sorguya iniyor.
- **Sinyal ayarları her yeniden başlatmada fabrika değerine dönüyordu.**
  Arayüzden yapılan IOA/ölçek/etiket düzenlemeleri kaydediliyor, denetim
  kaydı tutuluyor, sonra ilk açılışta sessizce geri alınıyordu. Kullanıcının
  değiştirdiği alanlar artık korunuyor; fabrikaya dönüş ayrı ve bilinçli bir
  işlem olarak duruyor.
- **Tek bozuk mesaj tüm telemetri akışını durduruyordu.** Beklenenden uzun bir
  sinyal adı toplu yazmayı patlatıyor, hiçbir ölçüm onaylanmıyor ve aynı
  paketteki sağlam ölçümler de tekrar tekrar düşüyordu. Ekranda "bağlantı
  koptu" görünüyordu; sebep tek bir metindi.
- **SCADA genel sorgusu 12. nesneden sonra kesiliyordu.** Sorgu bitiş bildirimi
  hiç gitmiyordu.
- **Tüm cihazlar SCADA'da tek cihaz gibi görünüyordu.** Cihazlara ayrı adres
  atanmadıkça hepsi aynı adrese biniyor, hangi fiderin arızalandığı
  anlaşılamıyordu. Adresler artık otomatik atanıyor (elle verilmişlere
  dokunulmuyor).
- **Arıza bildirimi webhook'a hiç gitmeyebiliyordu.** Gönderim başarısız olsa
  bile değer "gönderildi" sayıldığı için, bağlantı döndüğünde arıza bir daha
  yollanmıyordu — arıza kalkana kadar.
- **E-posta gönderimi sonsuza kadar bekleyebiliyordu** ve bu bekleme arıza
  kaydının yazılmasını da askıya alıyordu.
- **Yedek yükleme arayüzden çalışmıyordu.** Zincirdeki en düşük boyut sınırı
  (10 MB) yüzünden felaket kurtarmanın tek arayüz adımı kullanılamıyordu.
- **Yedek dosyaları diski dolduruyordu.** Güncelleme öncesi alınan yedekler
  hiç silinmiyor, geçmiş telemetri arşivinin tamamını içeriyor ve arayüzden
  geri de yüklenemiyordu. Aynı hata müşteriye verilen elle yedek komutunda da
  vardı.
- **Off-site yedekleme hiç çalışmıyordu.** Ayar girilse bile kopya
  alınmıyordu ve bu ancak felaket anında anlaşılırdı.
- **Özet tabloları sınırsız büyüyordu** ve Sistem Durumu bu sırada "sorun yok"
  gösteriyordu.
- **Yarıda kalan güncelleme cihazı açılamaz bırakabiliyordu**; dosyalar da
  artık eski sürüme geri alınıyor.
- **Belgelenen ölçekleme ayarı veritabanı bağlantı sınırını aşıyordu** — yani
  performans için yapılan değişiklik hataya yol açıyordu.

### Değişti
- **Canlı değerler artık ayrı bir tablodan okunuyor.** Anasayfa her açılışta
  geçmiş telemetrinin tamamını tarıyordu; bu, eşzamanlı birkaç kullanıcıda
  arka ucu belleğe boğuyordu.
- **IEC 104 kapsamı sabitlendi:** yalnızca izleme sinyalleri yayınlanır;
  metin sinyalleri ve analog çıkış kapsam dışıdır. Desteklenmeyen bir komut
  artık sessizce yutulmak yerine açıkça reddedilir.
- **Geçmiş verisi daha küçük parçalara bölünüyor** (600 cihaz ölçeğinde yazma
  ve sorgu başarımı için). Mevcut veri etkilenmez.
- Kurulumda FTP parolası otomatik üretiliyor; eskiden boş kaldığı için dosya
  transferi sunucusu sürekli yeniden başlıyor ve gerçek arızaları
  maskeliyordu.

### Test ve doğrulama
- Backend testleri 265 → **884**; IEC 104 servisi 15 → **40**.
- CI 7 → **12 iş**: Modbus ve IEC 104 servisleri, nginx yapılandırması,
  güvenlik duvarı kuralları ve appliance ajanları artık gerçekten koşuluyor.
- 201 API ucunun yetki sınırı otomatik doğrulanıyor.

---

## [2.29.0] — 2026-07-31

### Düzeltildi
- **Yedekten geri yükleme veritabanını yarım bırakıyordu (kritik).** Geri
  yükleme sırasında eski bağlantıları temizleyen döngü, `pg_restore`'un kendi
  bağlantılarını da kesiyordu. Sonuç: geri yükleme her denemede aynı yerde
  duruyor, üstelik silinmiş tablolar geri gelmediği için mevcut veri de
  kaybediliyordu — tam da yedeğe en çok ihtiyaç duyulan anda.
- **SCADA çıkışı kendini boğuyordu (kritik).** IEC 104 sunucusu her değer
  değişiminde sınırsız iş kuyruğa alıyordu; SCADA tarafı yavaşladığında bellek
  dolana kadar büyüyordu. Ayrıca sıra numarası yarışı yüzünden SCADA bağlantısı
  kendiliğinden kopabiliyordu. Artık bağlantı başına sınırlı kuyruk var ve
  yetişemeyen istemcide en eski bildirimler düşürülüp kayıt altına alınıyor.
- **Alarm servisi birkaç saatte bir yeniden başlıyordu (kritik).** Hiçbir
  kuralın kullanmadığı ölçüm geçmişi biriktiriliyor ve bellek sınırı
  aşılıyordu. Her yeniden başlangıçta açık alarmlar "yeni alarm" sayılıp
  bildirimler tekrar gönderiliyordu.
- **NATS erişilemezken açılan sistem bir daha telemetri yayınlamıyordu
  (kritik).** Bağlantı yalnızca bir kez deneniyordu; NATS saniyeler sonra
  düzelse bile veri akmıyor, cihazlar arayüzde "Kesik" görünüyordu. Artık
  bağlantı kurulana kadar yeniden denenir.
- **Operatör yetkisi dışına taşabiliyordu.** "Tümünü onayla/resetle"
  işlemleri sorumluluk alanı dışındaki alarmlara da uygulanıyor, ekranda ise
  hiçbir şey olmamış gibi görünüyordu. Ayrıca gateway listesi telemetri
  şifresini düz metin döndürüyordu; bu şifreyle sahte arıza üretmek veya
  gerçek arızayı gizlemek mümkündü.
- **Zorunlu şifre değişimi atlanabiliyordu.** Uyarı yalnızca arayüzdeydi;
  doğrudan istek atan biri tam yetkiyle işlem yapabiliyordu. Artık şifre
  değiştirilene kadar diğer işlemler sunucu tarafında reddedilir.

### Eklendi
- **NATS için TLS desteği (isteğe bağlı).** Açıldığında gateway şifresi ve
  telemetri şifreli kanaldan gider. Kurulum: `nats-tls-setup.sh` ile sertifika
  üretilir, ardından `.env` içinde etkinleştirilir. Varsayılan kapalıdır.
- **Ayrı arka plan servisi (isteğe bağlı).** Yoğun kurulumlar için arayüz ve
  arka plan işleri ayrı süreçlere alınabilir; arayüz çok çekirdekli
  çalıştırılabilir. Arka plan işlerinin tek yerde çalışması garanti altına
  alındı — yedekleme veya bildirim iki kez tetiklenmez.

---

## [2.28.0] — 2026-07-31

### Düzeltildi
- **Haritalar boş kalıyordu (kritik).** Harita karo istekleri nginx'te statik
  dosya kuralına takılıyordu: yol `.png` ile bittiği için regex bloğu düz
  prefix kuralını eziyor ve istek backend'e **hiç ulaşmıyordu**. Tarayıcıda
  internet olsa bile tüm haritalar boştu. `npm run dev` bu kuralı
  çalıştırmadığı için sorun yalnızca Docker/nginx kurulumunda görünüyordu.
- **Yeniden başlatmadan sonraki ilk backlog uyarısı bastırılıyordu (kritik).**
  Uyarı sınırlayıcısı `time.monotonic()` değerini mutlak olarak
  karşılaştırıyordu; Linux'ta bu değer makine açılışından beri geçen süre
  olduğu için açılıştan sonraki ilk 5 dakika boyunca uyarı üretilmiyordu. Oysa
  telemetri birikimi tam da o pencerede zirvede olur. Telemetri akışı
  `discard=old` ile çalıştığından tampon taşarsa mesajlar sessizce düşer —
  operatör hem veri kaybını hem uyarıyı kaçırıyordu.
- **Bağlantısı kopan cihaz için "arıza geçti" denmesi.** Alarm kapatma yolları
  ölçüm kalitesine bakmıyordu; `comm_lost` ile gelen 0.0 değeri eşiğin altına
  düştüğü için açık arıza alarmı kapanıyor ve harita yeşile dönüyordu.
- **Ayar değişikliği sahaya ulaşmıyordu.** `config_version` gönderilen
  ayarların tamamını temsil etmiyordu; cihazın TCP portu gibi alanlar
  değiştiğinde sürüm aynı kalıyor, gateway "değişmedi" yanıtı alıp eski ayarla
  çalışmaya devam ediyordu.

### Eklendi
- **Cihaz türüne göre sinyal profili.** Aynı DNP3 adresi farklı cihaz
  modellerinde farklı büyüklüğü gösterir. Backend artık her cihazın türünü
  bildiriyor ve gateway o türün sinyal setini kullanıyor; adres haritası
  gateway'de yerleşik olarak da bulunuyor. İkinci bir cihaz modeli
  eklendiğinde ölçümlerin yanlış sinyal adıyla kaydedilmesi engellendi.
- **Cihaz saati göstergesi.** Cihazın kendi olay zamanı ve o zamanın
  güvenilirliği kaydediliyor; saati kaymış cihaz canlı değerler ekranında
  işaretleniyor. Alarm saatleri her zaman sunucu saatine göre belirlenir.
- **Gateway sağlık bildirimi.** NAT arkasındaki gateway'in durumu düzenli
  olarak raporlanıyor.

### Değişti
- Gateway, ayarları yalnızca **değiştiğinde** indiriyor. Önceden her
  yoklamada tüm sinyal listesi tekrar iniyordu.

---

## [2.27.0] — 2026-07-31

### Düzeltildi
- **Kurulum, istenen sürümü sessizce yok sayabiliyordu (kritik).** Kurulum
  aracında bir sürüm seçilse bile cihaz eski sürümde kalıp "başarılı"
  bitiyordu. İki nedenin çarpımıydı: (1) kurulum betikleri deponun içindeki
  ajan dosyalarına `chmod` uygulayıp çalışma ağacını **kalıcı olarak** kirli
  bırakıyordu — yani ilk kurulumdan sonra her cihazda varsayılan durum buydu;
  (2) bu kirlilik yüzünden atlanan komut `git fetch` idi, oysa fetch çalışma
  ağacına dokunmaz — korunması gereken `git checkout` idi ve orada hiçbir
  kontrol yoktu. Artık istenen sürüme geçilemiyorsa kurulum **durur** ve
  sonunda "istenen sürüm gerçekten kuruldu mu" doğrulaması yapılır.
- **Canlı değer ekranı NATS koptuğunda kararıyordu.** Yeni fan-out köprüsü
  bağlantı koptuğunda hâlâ "hazırım" dediği için bellek-içi yedek yol devreye
  girmiyordu. Ayrıca köprü ilk bağlantı başarısız olursa bir daha hiç
  denemiyordu. İkisi de giderildi.
- Uzaktan bakımda "kapat/aç" düğmesi cihazı kalıcı çevrimdışı bırakabiliyordu.

### Eklendi
- **Telemetri Boru Hattı göstergesi (Sistem Durumu).** Tüketicinin gelen
  veriye yetişip yetişmediği artık görünür: bekleyen mesaj sayısı, işlem hızı,
  hatalı mesaj, NATS bağlantı durumu. Eşik aşılırsa uyarı ve olay kaydı
  üretilir. Bu gösterge önemli çünkü tampon taşarsa en eski ölçümler
  **sessizce** düşürülüyor — ekranda başka hiçbir belirti çıkmıyor.
- **Canlı değer yayını NATS üzerinden dağıtılıyor.** Tek başına davranış
  değiştirmez; sistemin ileride birden fazla sürece bölünebilmesinin ön
  koşuludur.
- Cihaz hesaplarının profil resmi EnerjiOne logosu yapılıyor; giriş ekranında
  gri siluet yerine ürün logosu görünür.
- WiFi kartı için kalıcı görev tercihi: cihaz kendi ağını mı yayınlasın (AP)
  yoksa kayıtlı bir ağa mı katılsın (client). Tek radyo ikisini aynı anda
  yapamaz; tercih artık kalıcı. Mevcut cihazlarda davranış değişmez.

### Değişti
- Sürekli entegrasyon artık NATS servisiyle çalışıyor: fan-out köprüsünün
  koruyucu testi eskiden her koşuda sessizce atlanıyordu.

---

## [2.26.0] — 2026-07-31

### Düzeltildi
- **Telemetri alımı ~83 gün sonra tamamen duruyordu (kritik).** `telemetry` ve
  `processed_messages` tablolarının birincil anahtarı `int4` idi. 600 cihaz
  ölçeğinde günde ~26M satır girdiği için sayaç 2,1 milyar tavanına yaklaşık
  83 günde dayanıyor; o an `nextval()` hata veriyor, toplu yazım commit'i
  patlıyor ve **hiçbir NATS mesajı onaylanmadığı için** aynı grup sonsuza
  kadar yeniden deneniyordu. Sonuç kademeli yavaşlama değil, ani ve tam
  duruştu. Kolonlar ve arkalarındaki sequence'ler `bigint`e çevrildi
  (migration 0021). **Not:** retention bu sorunu çözmez — satır silmek
  sayacı geri almaz.
- **Boot sırasında sonsuza kadar bekleme.** Açılışta çalışan eski şema
  bloğunda hiçbir kilit zaman aşımı yoktu; çakışan bir kilit (zamanlanmış
  yedek, restore, açık bir psql oturumu) varsa açılış süresiz bekliyordu ve
  yeniden başlatmak bunu çözmüyordu. Artık 5 sn kilit tavanı var ve blok
  hata verse bile backend açılmaya devam ediyor (önceden sonsuz crash-loop
  ve tamamen karanlık bir cihaz demekti).

### Eklendi
- **Disk guard — "disk asla dolmasın" güvencesi.** Toplam kapasitenin %10'u
  boş kalacak şekilde gerçek boş alanı ölçer (yüzde tabanlı, farklı disk
  boyutlarına uyarlanır). Kademeli davranır: önce yalnızca uyarır, sonra
  saklama sürelerini geçici kısaltır, en son yeniden üretilebilir veriyi
  (harita önbelleği, fazla yedekler) siler. **Denetim kaydına, lisansa,
  ayarlara, alarm/arıza geçmişine ve telemetri arşivine asla dokunmaz.**
  Ayarlar: `DISK_GUARD_*`.
- **Olay kayıtları (denetim) için 2 yıllık saklama.** Önceden hiç
  temizlenmiyordu. Beklenmedik bir olay fırtınasına karşı adet tavanı da var;
  bu tavan yalnızca telemetri/outbound gürültüsünü düşürür — güvenlik,
  lisans ve kimlik kayıtlarına dokunmaz.
- **Telemetri özet arşivi artık sınırlı ve sıkıştırılıyor.** Dakikalık özet
  1 yıl, saatlik özet 2 yıl saklanır (migration 0023). Önceden bu iki tablo
  sınırsız büyüyordu ve dakikalık özet pratikte ham verinin kopyası
  boyutundaydı — diski asıl dolduran kalem buydu.

### Değişti
- **NATS tamponu dolduğunda sistem artık DURMUYOR.** Önceden akış tavanına
  çarpınca yayın reddediliyor ve telemetri tamamen kesiliyordu; artık en eski
  mesajlar düşürülüp akış sürdürülüyor. Uzun bir kesintide (ham akış için
  ~19 saat) o dönemin en eski mesajları sessizce kaybolur — bilinçli takas.
  Ayrıca akış başına disk tavanı eklendi (toplam 12 GiB).
- **Yedekler artık telemetri arşivini içermiyor.** Yedek dosyası birkaç yüz
  MB'a düştü (önceden her yedek 90 günlük arşivi taşıyordu). Ayar, alarm,
  arıza, denetim ve bildirim geçmişi korunur; felaket kurtarma sonrası
  telemetri geçmişi boş gelir ve yeniden toplanmaya başlar.
- **İdempotency defteri 7 gün yerine 24 saat tutuluyor.** Gerçek ihtiyaç
  10 dakika; eski değer tabloyu gereksiz yere ~180M satıra çıkarıyordu.
- Başarısız yedek kayıtları ve yarım kalmış dosyalar otomatik temizleniyor.
  Elle alınan yedekler **varsayılan olarak silinmez**
  (`BACKUP_MANUAL_RETENTION_DAYS=0`); her koşulda en yeni başarılı yedek
  korunur.
- Saha cihazı disk standardı **128 GB → 500 GB** olarak güncellendi
  (`docs/APPLIANCE.md`), kalem kalem disk bütçesi eklendi.

### Güvenlik
- **Uzaktan bakım artık varsayılan KAPALI (davranış değişikliği).** Saha cihazı
  tailnet'e kayıtlı kalır ama gelen tüm bağlantılar reddedilir
  (`tailscale set --shields-up=true --ssh=false`). Müşterinin yetkili
  kullanıcısı — **yalnızca `engineer` rolü** — arayüzden süreli izin verir
  (Mühendislik > Sistem > Uzaktan Bakım; 15 dk – 24 saat), süre dolunca erişim
  kendiliğinden kapanır. `installer` rolü izin **veremez**: installer üretici
  tarafıdır, kendi kendine açabilseydi "müşteri izin verir" mekanizması
  anlamsızlaşırdı.
  - Süreyi host'ta root ile çalışan yeni `e1-rad` ajanı sayar; son tarih mutlak
    zaman olarak `lease.json`da durur ve 30 sn'lik systemd timer'ı uygular.
    **Backend, veritabanı ve container tamamen kapalı olsa bile izin kapanır.**
    Yeniden başlatma izni silmez ama uzatmaz da.
  - İzin verme/geri alma ve otomatik kapanma `system_events`e
    (`category=security`) yazılır: kim, hangi rolle, hangi IP'den, ne kadar
    süreyle. Otomatik kapanma olayları **gerçekleştiği zamanla** kaydedilir.
  - `setup-tailscale.sh` artık erişimi AÇMAZ. Önceki sürümde her `update.sh`
    çalışmasında `_ensure_ssh()` SSH'i geri açıyordu ve idempotent erken çıkış
    yalnızca `BackendState == "Running"` iken devreye giriyordu — bu ikisi
    birlikte müşterinin kapattığı kapıyı sessizce geri açardı.
  - **Sahaya çıkışta dikkat:** güncelleme tailnet üzerinden yapılıyorsa
    `setup-remote-access.sh` kendi SSH oturumunuzu kesmemek için 60 dakikalık
    kurulum mahsubu yazar (`E1_RAD_GRACE_MIN`). Bu tespit ilk olarak TEK bir
    test cihazında, yerel/fiziksel erişim elde tutularak denenmelidir.

---

## [2.25.0] — 2026-07-31

### Güvenlik
- **Canlı telemetri WebSocket'inde operatör kapsamı uygulanmıyordu** — cihaz
  filtresi tamamen istemciden geliyordu; filtre göndermeyen bir operatör
  sistemdeki **tüm** cihazların telemetrisini dinleyebiliyordu. Kapsam artık
  sunucuda hesaplanıyor, istemci filtresi yalnızca daraltabiliyor.
- **Oturum iptali WebSocket'te işlemiyordu** — "oturumu at" dendikten sonra
  açık soket akmaya devam ediyordu, logout edilmiş token ile yeni bağlantı
  açılabiliyordu. Artık bağlantı kurulurken ve her 30 saniyede doğrulanıyor.
- **Gateway kurulum ajanı** artık container'dan compose dosyası kabul etmiyor;
  yalnızca doğrulanmış parametrelerden kendi şablonunu üretiyor. Önceki regex
  kara listesi uzun-form bind, named-volume `driver_opts`, `security_opt`
  unconfined gibi yollarla aşılabiliyordu (host'ta root'a çıkış).
- **nginx rate-limit'i gerçek istemci IP'si üzerinden** çalışıyor. Ters vekil
  arkasında tüm istekler aynı IP görünüyordu; bu, dakikada 5 denemeyle
  **herkesin girişini** kilitlemeye izin veriyordu.
- **Güvenlik başlıkları statik dosyalarda kayboluyordu** — nginx'te
  `add_header` miras alınmadığı için tüm `.js`/`.css` dosyaları CSP, nosniff
  ve X-Frame-Options olmadan servis ediliyordu.
- **Oturum ömrü** dört dosyada dört farklı değerdeydi ve compose'daki değer
  "beni hatırla" süresine eşitti — yani kutucuk işlevsizdi, işaretlemeyen
  kullanıcı da 30 günlük token alıyordu. Hepsi 24 saate hizalandı.

### Eklendi
- **Telemetri Arşivi sağlık kartı** (Sistem Durumu) — arşiv tablosunun saklama
  süresi politikası gerçekten kurulu mu, hypertable mı, ne kadar disk
  kullanıyor. Politika kurulmadığında tek belirti diskin dolmasıydı; artık
  önceden görülüyor. Eksik politikaları onaran migration da eklendi.
- **Arıza tel mesafesi** — arıza bölgesinin hat başından kaç metre uzakta
  olduğu hesaplanıyor ve gösteriliyor. Kuş uçuşu değil: direk koordinatları
  üzerinden hat boyunca, cihazların direkler arasındaki konumu da hesaba
  katılarak. Branşman hatlarda mesafe ana hattaki dallanma direğinden itibaren
  toplanır.
- **Çevrimdışı harita** artık modal yerine Mühendislik altında ayrı bir sayfa;
  alan seçimi harita üzerinde sürükleyerek yapılıyor.
- **Klavye erişilebilirliği** — modallar ESC ile kapanıyor ve odak modal içinde
  kalıyor. Önce yedi modalın yalnızca biri ESC ile kapanıyordu; Tab'a basan
  kullanıcı modalın arkasındaki forma düşüyordu.
- Render hatasında beyaz ekran yerine "yeniden yükle" ekranı gösteriliyor.

### Düzeltildi
- **Bozuk bir üçüncü taraf apt deposu kurulumu tamamen durduruyordu.** Sahada
  makinede duran ve imza anahtarı eksik bir Google Chrome deposu yüzünden
  `apt-get update` hata döndü ve kurulum orada öldü — oysa ihtiyaç duyulan
  Ubuntu depoları sağlamdı. Artık ilgisiz bir deponun bozuk olması kurulumu
  durdurmuyor; depo adıyla bildiriliyor ve karar paket kurulumunda veriliyor.
- **Canlı değerler ekranı çok cihazda donuyordu.** Gelen her telemetri mesajı
  tüm satır listesini baştan sona geziyordu; 600 cihazda tarayıcı sekmesi
  kilitleniyordu. Mesajlar artık toplu işleniyor (ölçüldü: 699 ms → 24 ms).
- **Arka planda gereksiz sorgu yükü** — alarm, arıza, olay, topoloji ve cihaz
  listesi hangi sayfada olduğunuza bakılmaksızın 5 saniyede bir çekiliyordu.
  Artık yalnızca o veriyi gösteren sayfalarda çekiliyor ve sekme arka plandayken
  tamamen duruyor.
- **Harita çok cihazda takılıyordu** — topoloji hesabı cihaz durumu her
  güncellendiğinde (5 saniyede bir) baştan yapılıyordu; artık yalnızca topoloji
  veya konum gerçekten değişince yapılıyor.
- **Hat Arızaları sayfası** her satır için ayrı sorgular atıyordu (200 arızada
  5 saniyede ~1.200 sorgu). Sorgu sayısı artık arıza sayısından bağımsız.

---

## [2.24.6] — 2026-07-30

### Düzeltildi
- **Lisans kilidi ağ ayarlarını da kilitliyordu** — lisanssız cihazda ağ
  yapılandırmasına erişilemiyor, dolayısıyla lisans da alınamıyordu.
- **Tailscale SSH** zaten tailnet'e katılmış cihazlarda açılmıyordu.
- **Postgres varsayılan ayarlarla koşuyordu** (`shared_buffers=128MB`,
  `work_mem=4MB`) — sıralamalar diske taşıyordu.
- Telemetri temizleme sorgusu tüm tabloyu tarıyordu; filtre pencere
  fonksiyonunun içine indirildi.
- Alarm reconcile'da N+1 — açık alarm başına bir sorgu atılıyordu.

### Değişti
- **İlk yükleme 2.1 MB → 739 KB** (%66): 21 sayfa tembel yüklemeye alındı.
- Kurulum artık **sessiz "hiçbir şey olmadı" durumlarını görünür kılıyor**:
  appliance atlandığında sonucu, güncellemede yayınlanmamış değişiklik
  olduğunu açıkça söylüyor.

### Eklendi
- Kurulum aracına **WiFi ayarı ve internet kontrolü** — cihazda internet
  yoksa kurulum GitHub'dan indirme yapamıyordu.
- Kurulum aracı sekmeli arayüz, ağ listesi, GitHub anahtarı alma yardımcısı.


---

## [2.24.5] — 2026-07-28

### Eklendi
- **Saha Kurulum Aracı (GUI)** — cihaza SSH ile bağlanıp kurulum/güncelleme/
  kaldırma işlemlerini tek ekrandan yapar, çıktıyı canlı gösterir.
  (`tools/installer-gui`)
- **Debian paketi** — müşteriye giden dağıtım biçimi; uygulama kaynak kodu
  içermez. Her PR'da üretilip temiz bir konteynerde kurularak doğrulanır.
- **Kurulum dosyası üreticisi** — anahtarlar depoda durmadan tek dosyalık
  kurulum scripti üretir (`packaging/make-provisioner.sh`).
- Uzaktan bakım VPN'i (Tailscale) ve saha kimliği (müşteri/saha) desteği.
- Sağlıklı olmayan altyapı servisi için otomatik onarım ve tek dosyalık
  teşhis raporu.

### Düzeltildi
- **Oturum kaydı hiç oluşmuyordu** (`_timedelta` yazım hatası): her girişte
  `NameError` yutuluyor, "Aktif Oturumlar" boş kalıyor ve oturum sonlandırma
  çalışacak kayıt bulamıyordu.
- **Postgres parola şifreleme uyumsuzluğu** (MD5 ↔ SCRAM): kurulum
  "parola hizalandı" deyip TCP girişinde reddediliyordu. Artık otomatik
  onarılıyor.
- `.env` şablonu CRLF ile geliyordu; Linux'ta değerlere satır sonu karakteri sızıyordu.
- Paket kurulumunda `install.sh`/`update.sh` git yokluğunda ölüyordu.

### Değişti
- **Kurulum tamamen sessiz** — hiçbir soru sorulmuyor; tüm girdiler kurulum
  aracından geliyor. systemd kaydı artık koşulsuz (atlanırsa cihaz yeniden
  başlatıldığında ayağa kalkmıyordu).
- Anahtarı depoda tutan yol tamamen kaldırıldı; depoda canlı sır yok.
- Saha cihazları bir dalın ucunu değil, yayınlanmış bir **tag**'i takip eder.
  Kurulum imajları **indirir**, cihazda derlemez.
- Sürümün tek kaynağı kök dizindeki `VERSION` dosyası.

### Altyapı
- GitHub Actions ile CI: frontend build, backend ruff+pytest, alembic tek-head
  kontrolü, shellcheck, compose doğrulama, sürüm tutarlılığı, Debian paketi.
- Tag ile tetiklenen release hattı: imajlar CI'da derlenip GHCR'a basılır.
- `update.sh --version X.Y.Z` ile belirli bir sürüme geçiş ve **geri alma**.

---

## [2.24.4] — 2026-07-28

Bu sürüm ve öncesi için ayrıntı: `git log`. Değişiklik günlüğü bu sürümden
itibaren tutulmaya başlandı.
