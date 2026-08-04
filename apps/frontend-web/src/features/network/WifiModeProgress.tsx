/**
 * WiFi gorev degisimi sirasinda gosterilen ilerleme ekrani.
 *
 * NEDEN VAR
 * ---------
 * AP yayinini acip kapatmak sahada "cok uzun suruyor ve ne oldugu belli
 * degil" seklinde bildirildi. Olculdugunde iki AYRI sorun cikti:
 *
 *   1. Gercek sure. NetworkManager gorevi degistirirken baglantiyi indirip
 *      hostapd/dnsmasq'i yeniden ayaga kaldirir; bu birkac saniye surer ve
 *      KISALTILAMAZ.
 *   2. ALGILANAN sure. Durum yoklamasi 10 saniyede bir kosuyordu, yani islem
 *      2 saniyede bitse bile ekran bunu 10 saniyeye kadar ogrenmiyordu.
 *      Kullanicinin sikayet ettigi surenin buyuk kismi buydu.
 *
 * (2) yoklama hizlandirilarak cozuldu (bkz. NetworkSettingsPage). Bu bilesen
 * (1) icin: is BITENE KADAR ne oldugunu acikca yazar.
 *
 * ILERLEME UYDURULMAZ
 * -------------------
 * Adimlar zamanlayiciyla degil, cihazdan OLCULEN durumdan turetilir. Sahte
 * bir yuzde cubugu burada ozellikle yanlis olurdu: gorev degisimi
 * BASARISIZ olabiliyor (tek kartli cihazda AP'ye gecis, mesgul kanal,
 * surucu reddi) ve dolmakta olan bir cubuk kullaniciya "yolunda gidiyor"
 * diye yalan soylerdi. Olcum yoksa adim "bilinmiyor" olarak kalir.
 *
 * OTURUM KOPMASI
 * --------------
 * Kullanici cihaza tam da degistirdigimiz arayuz uzerinden bagliysa
 * (ornegin AP'ye baglanmisken client moduna geciliyor) sonucu GOREMEYIZ:
 * baglanti biz cevabi almadan kopar. O durumda "basarisiz" demek yanlis
 * olur; ne yapilmasi gerektigini yazip bekleyisi orada bitiriyoruz.
 */
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import type { NetworkStatus } from "../../shared/types";

/** Bu ekranin izledigi islemler. `connect`/`forget` ayri akista. */
export type ProgressAction =
  | { kind: "role"; mode: "ap" | "client" }
  | { kind: "radio_off" }
  | { kind: "radio_on" };

type Durum = "bekliyor" | "calisiyor" | "tamam" | "hata" | "bilinmiyor";

type Adim = {
  key: string;
  label: string;
  durum: Durum;
};

/** Hedefe ulasildi mi? null = OLCUM YOK (varsayim uretmiyoruz). */
function hedefeUlasildi(action: ProgressAction, status: NetworkStatus | null): boolean | null {
  if (!status) return null;
  switch (action.kind) {
    case "role": {
      const mode = status.role?.mode ?? null;
      if (mode === null) return null;
      if (action.mode === "client") return mode === "client";
      // AP icin gorev degismis olmasi YETMEZ: yayin gercekten baslamali.
      // Ilk isteki hata tam olarak buydu — mod "ap" yaziyor ama ortada
      // yayin yok, kullanici telefonundan agi goremiyordu.
      if (mode !== "ap") return false;
      return status.ap?.active ?? null;
    }
    case "radio_off":
      return status.radio ? status.radio.enabled === false : null;
    case "radio_on":
      return status.radio ? status.radio.enabled === true : null;
  }
}

export default function WifiModeProgress({
  action,
  status,
  startedAt,
  dropsSession,
  onClose,
}: {
  action: ProgressAction;
  status: NetworkStatus | null;
  /** Istegin gonderildigi an (ms). Gecen sure bundan sayilir. */
  startedAt: number;
  /** Islem bizim baglantimizi kesecek mi? Sonucu GOREMEYECEGIZ demektir. */
  dropsSession: boolean;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(id);
  }, []);

  const gecenSn = Math.max(0, Math.floor((now - startedAt) / 1000));
  const uygulaniyor = Boolean(status?.pending) || status?.last_apply?.status === "applying";
  const basarisiz = status?.last_apply?.status === "failed";
  const ulasildi = hedefeUlasildi(action, status);

  // Sonuc: bitti / hata / devam. Oturum kopacaksa "devam"da kalmayiz.
  const sonuc: "devam" | "tamam" | "hata" | "devirteslim" = basarisiz
    ? "hata"
    : ulasildi === true
      ? "tamam"
      : dropsSession && gecenSn >= 6
        ? "devirteslim"
        : "devam";

  const hedefAdi = useMemo(() => {
    if (action.kind === "role") {
      return t(action.mode === "ap" ? "network.wifi.roleAp" : "network.wifi.roleClient");
    }
    return t(action.kind === "radio_on" ? "network.wifi.radioOn" : "network.wifi.radioOff");
  }, [action, t]);

  const adimlar: Adim[] = useMemo(() => {
    // 1. Istek cihaza iletildi — POST 2xx dondugu icin BILIYORUZ.
    const gonderildi: Durum = "tamam";

    // 2. Cihaz uyguluyor.
    let uygula: Durum;
    if (sonuc === "hata") uygula = "hata";
    else if (sonuc === "tamam") uygula = "tamam";
    else if (sonuc === "devirteslim") uygula = "bilinmiyor";
    else if (uygulaniyor) uygula = "calisiyor";
    else if (status === null) uygula = "bilinmiyor";
    else uygula = "calisiyor";

    // 3. Hedef durum dogrulandi.
    let dogrula: Durum;
    if (sonuc === "hata") dogrula = "bekliyor";
    else if (sonuc === "tamam") dogrula = "tamam";
    else if (sonuc === "devirteslim") dogrula = "bilinmiyor";
    else if (ulasildi === null) dogrula = "bilinmiyor";
    else dogrula = uygula === "calisiyor" ? "bekliyor" : "calisiyor";

    return [
      { key: "sent", label: t("network.wifi.progress.stepSent"), durum: gonderildi },
      { key: "applying", label: t("network.wifi.progress.stepApplying"), durum: uygula },
      {
        key: "verify",
        label: t("network.wifi.progress.stepVerify", { hedef: hedefAdi }),
        durum: dogrula,
      },
    ];
  }, [hedefAdi, sonuc, status, t, ulasildi, uygulaniyor]);

  const bitti = sonuc !== "devam";

  return (
    <div className="net-modal-backdrop" onClick={() => bitti && onClose()}>
      <div
        className="net-modal wifi-prog"
        role="dialog"
        aria-live="polite"
        aria-busy={!bitti}
        onClick={(e) => e.stopPropagation()}
      >
        <div className={`wifi-prog-head wifi-prog-head--${sonuc}`}>
          <span className="wifi-prog-icon" aria-hidden="true">
            {sonuc === "tamam" ? (
              <span className="material-symbols-outlined">check_circle</span>
            ) : sonuc === "hata" ? (
              <span className="material-symbols-outlined">error</span>
            ) : sonuc === "devirteslim" ? (
              <span className="material-symbols-outlined">swap_horiz</span>
            ) : (
              <span className="wifi-prog-spin" />
            )}
          </span>
          <div>
            <h4>
              {sonuc === "tamam"
                ? t("network.wifi.progress.doneTitle", { hedef: hedefAdi })
                : sonuc === "hata"
                  ? t("network.wifi.progress.failedTitle")
                  : sonuc === "devirteslim"
                    ? t("network.wifi.progress.handoverTitle")
                    : t("network.wifi.progress.title", { hedef: hedefAdi })}
            </h4>
            <p className="wifi-prog-sub">
              {sonuc === "devam"
                ? t("network.wifi.progress.elapsed", { sn: gecenSn })
                : sonuc === "tamam"
                  ? t("network.wifi.progress.tookSeconds", { sn: gecenSn })
                  : ""}
            </p>
          </div>
        </div>

        <ol className="wifi-prog-steps">
          {adimlar.map((a) => (
            <li key={a.key} className={`wifi-prog-step wifi-prog-step--${a.durum}`}>
              <span className="wifi-prog-dot" aria-hidden="true" />
              <span>{a.label}</span>
            </li>
          ))}
        </ol>

        {/* Sabir metni: gorev degisimi GERCEKTEN birkac saniye surer.
            Beklemenin normal oldugunu ancak o esikten SONRA soyluyoruz;
            hemen yazmak "yavas" algisini kendimiz uretmek olurdu. */}
        {sonuc === "devam" && gecenSn >= 8 ? (
          <p className="wifi-prog-note">{t("network.wifi.progress.slowHint")}</p>
        ) : null}

        {sonuc === "devirteslim" ? (
          <p className="wifi-prog-note">
            {action.kind === "role" && action.mode === "ap"
              ? t("network.wifi.progress.handoverToAp", {
                  ssid: status?.ap?.ssid ?? "EnerjiOne Grid",
                })
              : t("network.wifi.progress.handoverGeneric")}
          </p>
        ) : null}

        {sonuc === "hata" ? (
          <p className="wifi-prog-note wifi-prog-note--bad">
            {status?.last_apply?.error || t("network.wifi.progress.failedGeneric")}
          </p>
        ) : null}

        <div className="net-modal-actions">
          <button type="button" onClick={onClose} disabled={!bitti}>
            {bitti ? t("common.close") : t("network.wifi.progress.working")}
          </button>
        </div>
      </div>
    </div>
  );
}
