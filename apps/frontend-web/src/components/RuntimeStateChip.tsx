/**
 * Cihaz calisma-zamani durumunun ORTAK gorunumu — nokta ve rozet.
 *
 * Karar BU DOSYADA VERILMEZ: gelen `DeviceRuntimeState` zaten
 * `shared/deviceRuntimeState.ts` icindeki tek normalizerden cikmistir. Burada
 * yalnizca ton -> sinif, `labelKey` -> metin donusumu yapilir.
 *
 * NEDEN ORTAK BILESEN
 * -------------------
 * Ayni durum listede, kartta, haritada ve detayda GORUNUYOR. Her ekran kendi
 * rengini secseydi, "Smart Bekleme"nin mavi oldugu tek bir ekran olur,
 * digerlerinde gri kalirdi — ve gri, operator icin "olu cihaz" demek. Renk
 * sozlesmesi tek yerde durur.
 */
import { useTranslation } from "react-i18next";

import type { DeviceRuntimeState } from "../shared/deviceRuntimeState";
import { RUNTIME_HEALTH_CAPABILITY, supportsCapability } from "../shared/gatewayCapabilities";
import { useRuntimeTip } from "./RuntimeTooltip";

/** Ton -> CSS sinifi. Sinifin kendisi `--rt*` degiskenlerini kurar. */
export function runtimeToneClass(state: DeviceRuntimeState): string {
  return `runtime-tone--${state.tone}`;
}

type DotProps = {
  state: DeviceRuntimeState;
  /** Ek sinif (mevcut yerlesimlere oturmak icin). */
  className?: string;
  /** `title` metnini bastir (satirin kendi ipucu varsa). */
  silent?: boolean;
};

/** Kucuk durum noktasi — liste satiri ve kart basligi icin.
 *
 *  USTUNE GELINCE ANLAMINI SOYLER. Nokta tek basina yalnizca bir RENK;
 *  operator icin "mavi" kendiliginden okunmaz ve `Smart Bekleme` mavi ama
 *  SAGLIKLI oldugu icin yanlis okunmasi kolaydir. Eski `title` yalnizca adi
 *  gosteriyordu; ipucu artik adi, kovayi (saglikli/uyari/ariza) ve bir
 *  cumlelik anlami birlikte veriyor (bkz. `RuntimeTooltip`).
 *
 *  `aria-label` KALIYOR: ekran okuyucu ipucunu acmaz, noktanin kendisini
 *  okur. `silent` yalnizca GORSEL ipucunu kapatir (satirin kendi ipucu
 *  varsa); erisilebilir ad her durumda durur. */
export function RuntimeStateDot({ state, className = "", silent = false }: DotProps) {
  const { t } = useTranslation();
  const label = t(state.labelKey);
  // Nokta bir `<button>` icinde duruyor; odaklanabilir YAPILMAZ.
  const { triggerProps, tip } = useRuntimeTip(state);
  const dokun = silent ? {} : triggerProps;
  return (
    <>
      <span
        {...dokun}
        className={`runtime-dot ${runtimeToneClass(state)} ${className}`.trim()}
        aria-label={label}
        role="img"
      />
      {tip}
    </>
  );
}

type ChipProps = {
  state: DeviceRuntimeState;
  /** Ikonu goster (dar yerlerde kapatilir). */
  withIcon?: boolean;
  /** Aciklamayi `title` olarak ekle. */
  withHint?: boolean;
  className?: string;
};

/** Metinli rozet — detay ekrani ve harita yan paneli icin. */
export function RuntimeStateChip({
  state,
  withIcon = true,
  withHint = true,
  className = ""
}: ChipProps) {
  const { t } = useTranslation();
  // Rozet TEK BASINA duruyor (detay, harita paneli, ozet basligi):
  // odaklanabilir olmasi klavye kullanicisina aciklamayi acar ve
  // hicbir butonun icine girmez.
  const { triggerProps, tip } = useRuntimeTip(state, { focusable: true });
  // Ipucu artik isletim sisteminin `title` kutusu DEGIL: orada renk yok ve
  // ~1 saniye gecikmeyle aciliyordu, yani "rengi acikladigi" soylenemezdi.
  const dokun = withHint ? triggerProps : {};
  return (
    <>
    <span
      {...dokun}
      className={`runtime-chip ${runtimeToneClass(state)} ${className}`.trim()}
    >
      {withIcon ? (
        // Ikon adi sabit tablodan gelir (normalizer); fontta olmayan bir ad
        // ekranda ikon yerine METIN olarak cikardi — bkz. iconSubset testi.
        <span className="material-symbols-outlined" aria-hidden="true">
          {state.icon}
        </span>
      ) : null}
      {t(state.labelKey)}
    </span>
    {withHint ? tip : null}
    </>
  );
}

/**
 * Durumun NEREDEN geldigini soyleyen kucuk not.
 *
 * Bos donebilir (gateway otoritesi tazeyken gosterilecek bir sey yok).
 * Bayat/eski gateway durumunda ekranin "bu bilgi gateway'in ANLIK karari"
 * demeye hakki yoktur; o hakki geri almak icin var.
 */
export function RuntimeSourceNote({
  state,
  gatewayVersion
}: {
  state: DeviceRuntimeState;
  /** Gateway'in bildirdigi surum. UC DURUMLU: `undefined` = sorulmadi,
   *  `null` = soruldu ama gateway bildirmedi, metin = surum. */
  gatewayVersion?: string | null;
}) {
  const { t } = useTranslation();
  if (state.stale) {
    return (
      <span className="runtime-note runtime-note--stale" title={t("deviceRuntime.source.staleHint")}>
        <span className="material-symbols-outlined" aria-hidden="true">
          history
        </span>
        {t("deviceRuntime.source.stale")}
      </span>
    );
  }
  if (state.source !== "gateway") {
    const sonEk = runtimeSourceReason(gatewayVersion);
    return (
      <span className="runtime-note" title={t(`deviceRuntime.source.${sonEk}Hint`)}>
        <span className="material-symbols-outlined" aria-hidden="true">
          info
        </span>
        {t(`deviceRuntime.source.${sonEk}`)}
      </span>
    );
  }
  return null;
}

/**
 * Saglik verisi neden yok — GATEWAY SURUMUNE gore.
 *
 * Eskiden tek bir cevap vardi: "Eski gateway". Sahada YANLIS cikti: gateway
 * 1.15.0 kuruluydu, eksik olan yalnizca yayinci bayragiydi
 * (`DEVICE_HEALTH_PUBLISH_ENABLED`, varsayilan KAPALI). Guncel bir gateway'e
 * "eski" demek operatoru olmayan bir yukseltmeye yonlendirir; asil yapilacak
 * is (bayragi ac) hic gorunmez.
 *
 * Surum BILINMIYORSA (`null`/`undefined`) iddiada bulunulmaz: notr bir
 * "rapor yok" denir. Bildirmemis bir gateway pekala guncel olabilir —
 * `supportsCapability` de ayni uc durumlu mantigi kullaniyor.
 */
export function runtimeSourceReason(
  gatewayVersion: string | null | undefined
): "legacy" | "publisherOff" | "noReport" {
  if (gatewayVersion == null) return "noReport";
  const destek = supportsCapability(RUNTIME_HEALTH_CAPABILITY, gatewayVersion);
  if (destek === false) return "legacy";       // surum gercekten eski
  if (destek === true) return "publisherOff";  // surum yeterli -> bayrak kapali
  return "noReport";                            // cozulemedi
}
