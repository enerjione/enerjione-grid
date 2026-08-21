/**
 * YAPILANDIRMA UYGULAMA DURUMU — ekranda ne yazacagiz.
 *
 * NE COZUYOR
 * ----------
 * Kart eskiden TEK bir sey soyluyordu: "Cihaza gonderildi: {tarih}". O metin
 * `version.appliedAt` alanina bakiyordu ve o alan, komut kuyruga girer
 * girmez doluyordu. Uyuyan bir Horstmann'da bu duz bir yalandi: dosya bir
 * FTP sunucusuna kondu, komut 120 saniye sonra oldu, cihaz hala eski
 * yapilandirmayla calisiyordu — ama ekran "gonderildi" diyordu.
 *
 * Artik surecin her asamasi ayri ayri gorunur ve HICBIRI kanitsiz basari
 * iddia etmez:
 *
 *   dosya hazirlandi -> cihaz bekleniyor -> komut sirada -> iletildi
 *                                                             -> DOGRULANDI
 *
 * `iletildi` ile `dogrulandi` ARASINDAKI FARK KORUNUR. Gateway yalnizca
 * komutu cihaza ILETTIGINI bilir; cihazin dosyayi gercekten yukledigi ancak
 * cihazin KENDI kaniti ile anlasilir.
 *
 * NEDEN AYRI DOSYA
 * ----------------
 * Karttan (`DeviceFtpConfigCard.tsx`) import etmek, testte `api.ts`i ve
 * onunla birlikte `import.meta.env`i suruklerdi — Node test kosucusunda
 * bu tanimsizdir ve modul yuklenirken coker (ayni tuzak `commandScopes.ts`
 * icin de yasandi). Saf ve bagimsiz kalmasi testi mumkun kilar.
 */
import type { ConfigApplication } from "../../shared/types";

/** Gorunum kovasi — renk/ikon secimi buradan turer. */
export type ApplyTone = "notr" | "bekleme" | "ilerliyor" | "basarili" | "hata";

export type ApplyGorunum = {
  /** i18n anahtari (deviceDetail.config.ftp.apply.* altinda). */
  labelKey: string;
  tone: ApplyTone;
  /** Varsa ek aciklama anahtari; yoksa null. */
  hintKey: string | null;
  /**
   * Kanitlanmis fiziksel uygulama mi?
   *
   * YALNIZCA bu `true` iken arayuz "uygulandi" diyebilir. Ara asamalarin
   * hicbiri bu bayragi kaldirmaz — kanitsiz basari iddiasi, bu isin
   * duzeltmek icin var oldugu hatanin kendisi.
   */
  kanitli: boolean;
};

/** Backend durum sabitleri (`device_config_application.py`). */
export const DURUM = {
  BEKLIYOR: "cihaz_bekleniyor",
  KUYRUKTA: "kuyrukta",
  ILETILDI: "iletildi",
  DOGRULANDI: "dogrulandi",
  BASARISIZ: "basarisiz",
  GECERSIZ: "gecersiz_kilindi"
} as const;

/** Hazirlik gerekcesi -> aciklama anahtari (`device_session_readiness.py`). */
const GEREKCE: Readonly<Record<string, string>> = {
  uykuda: "reasonSleeping",
  erisilemez: "reasonUnreachable",
  bayat_gozlem: "reasonStale",
  temas_yok: "reasonNoContact",
  yeni_kanit_bekleniyor: "reasonWaitEvidence",
  eski_kanit_cevrimdisi: "reasonLegacyOffline"
};

const K = "deviceDetail.config.ftp.apply.";

/**
 * Uygulama kaydini ekran gorunumune cevirir.
 *
 * `null` girdi = bu cihaz icin hic uygulama denenmemis; kart o durumda
 * uygulama satirini HIC cizmez (bos bir "durum yok" satiri gurultudur).
 */
export function applyGorunum(app: ConfigApplication | null): ApplyGorunum | null {
  if (!app) return null;

  switch (app.state) {
    case DURUM.BEKLIYOR:
      return {
        labelKey: K + "waiting",
        tone: "bekleme",
        // Neden bekledigi biliniyorsa onu soyle; bilinmiyorsa uydurma.
        hintKey: gerekceAnahtari(app.reason),
        kanitli: false
      };

    case DURUM.KUYRUKTA:
      return { labelKey: K + "queued", tone: "ilerliyor", hintKey: null, kanitli: false };

    case DURUM.ILETILDI:
      return {
        labelKey: K + "delivered",
        tone: "ilerliyor",
        // KRITIK: "iletildi" basari DEGIL. Ipucu bunu acikca soyler.
        hintKey: K + "deliveredHint",
        kanitli: false
      };

    case DURUM.DOGRULANDI:
      return {
        labelKey: K + "verified",
        tone: "basarili",
        // Kanit SINIFI gorunur: kesin mi zayif mi.
        hintKey:
          app.verifiedBy === "cihaz_dosyasi"
            ? K + "verifiedStrong"
            : app.verifiedBy === "damga_degisti"
              ? K + "verifiedWeak"
              : null,
        kanitli: true
      };

    case DURUM.BASARISIZ:
      return { labelKey: K + "failed", tone: "hata", hintKey: null, kanitli: false };

    case DURUM.GECERSIZ:
      return { labelKey: K + "superseded", tone: "notr", hintKey: null, kanitli: false };

    default:
      // TANIMADIGIMIZ DURUM BASARI SAYILMAZ. Backend ileride yeni bir durum
      // eklerse arayuz notr gosterir; "dogrulandi" varsaymak en tehlikeli
      // yanlis olurdu.
      return { labelKey: K + "unknown", tone: "notr", hintKey: null, kanitli: false };
  }
}

function gerekceAnahtari(reason: string | null): string | null {
  if (!reason) return null;
  const ad = GEREKCE[reason];
  return ad ? K + ad : null;
}

/** Ton -> CSS sinifi. Tek yerden, ki kart ile test ayrismasin. */
export function applyToneClass(tone: ApplyTone): string {
  return `dev-ftp-apply--${tone}`;
}
