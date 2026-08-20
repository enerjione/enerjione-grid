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

/** Kucuk durum noktasi — liste satiri ve kart basligi icin. */
export function RuntimeStateDot({ state, className = "", silent = false }: DotProps) {
  const { t } = useTranslation();
  const label = t(state.labelKey);
  return (
    <span
      className={`runtime-dot ${runtimeToneClass(state)} ${className}`.trim()}
      title={silent ? undefined : label}
      aria-label={silent ? undefined : label}
    />
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
  // `labelKey` -> "deviceRuntime.state.smartIdle"; ipucu ayni son eki tasir.
  const hintKey = state.labelKey.replace(".state.", ".stateHint.");
  return (
    <span
      className={`runtime-chip ${runtimeToneClass(state)} ${className}`.trim()}
      title={withHint ? t(hintKey) : undefined}
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
  );
}

/**
 * Durumun NEREDEN geldigini soyleyen kucuk not.
 *
 * Bos donebilir (gateway otoritesi tazeyken gosterilecek bir sey yok).
 * Bayat/eski gateway durumunda ekranin "bu bilgi gateway'in ANLIK karari"
 * demeye hakki yoktur; o hakki geri almak icin var.
 */
export function RuntimeSourceNote({ state }: { state: DeviceRuntimeState }) {
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
    return (
      <span className="runtime-note" title={t("deviceRuntime.source.legacyHint")}>
        <span className="material-symbols-outlined" aria-hidden="true">
          info
        </span>
        {t("deviceRuntime.source.legacy")}
      </span>
    );
  }
  return null;
}
