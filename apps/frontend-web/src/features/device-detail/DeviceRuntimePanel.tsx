/**
 * Cihaz detay — "Baglanti ve Oturum" karti.
 *
 * UC BOLUM, UC AYRI SAHIP. Karisirlarsa ekran yalan soyler:
 *
 *   YAPILANDIRMA  Operatorun Grid'e girdigi ayarlar (Dnp3 uzantisi). Sahada
 *                 gecerli olduklarinin KANITI DEGILDIR — gateway eski surumse
 *                 ya da cihaz ayari almadiysa yalnizca "ne istedigimizi"
 *                 gosterir.
 *   CALISMA ZAMANI Gateway'in bildirdigi ANLIK gozlem (`device_health_v1`).
 *                 Baglanti kararinin TEK kaynagi budur.
 *   TESHIS        Sonda (probe) sonuclari. DURUM BELIRLEMEZLER.
 *                 `ip_probe_status = "unreachable"` gormek NORMALDIR: ICMP
 *                 saha aglarinda/APN'lerde sikca engellidir ve Smart bir
 *                 modem mesru olarak uykudadir (sozlesme bolum 5). Bu yuzden
 *                 sonda satirlarinin RENGI YOKTUR; renk verirsek operator
 *                 saglikli uyuyan yarim filoyu ariza sanar.
 *
 * Yapilandirilan oturum politikasi ile SAHADA ETKIN olan AYRI satirlardir:
 * `auto` gateway tarafinda `continuous`/`smart` olarak cozulur ve ikisini tek
 * satirda birlestirmek, cozumun ne oldugunu goremeyen bir ekran uretirdi.
 *
 * Karar mantigi burada YOK: durum `shared/deviceRuntimeState.ts` icindeki tek
 * normalizerden gelir, bu dosya yalnizca cizer.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { DialInCountdown } from "../../components/DialInCountdown";
import {
  RuntimeSourceNote,
  RuntimeStateChip,
  runtimeSourceReason
} from "../../components/RuntimeStateChip";
import { fetchGatewayUpdate } from "../../shared/api";
import { formatDateTime } from "../../shared/format";
import {
  deviceRuntimeStateOf,
  epochToDate,
  runtimeEnumKey,
  sessionPolicyMismatch
} from "../../shared/deviceRuntimeState";
import { useMinuteTick } from "../../shared/useMinuteTick";
import { COMMUNICATION_GRACE_MIN_DEFAULT } from "../../shared/types";
import type { DeviceRow } from "../../shared/types";
import { clockGorunum, clockToneClass } from "./deviceClockStatus";

type Props = {
  device: DeviceRow;
};

/** Satir — deger `null` ise "—". Renk YALNIZCA acikca istenirse. */
function Row({
  label,
  value,
  hint,
  tone
}: {
  label: string;
  value: string | null;
  hint?: string;
  tone?: "green" | "blue" | "orange" | "amber" | "red" | "slate";
}) {
  return (
    <li className="device-runtime-row" title={hint}>
      <span className="device-runtime-row-label">{label}</span>
      <span className={`device-runtime-row-value${tone ? ` runtime-tone--${tone} is-toned` : ""}`}>
        {value ?? "—"}
      </span>
    </li>
  );
}

export function DeviceRuntimePanel({ device }: Props) {
  const { t } = useTranslation();
  // GATEWAY SURUMU — "saglik verisi neden yok" sorusunun dogru cevabi icin.
  //
  // UC DURUMLU: `undefined` = sorulmadi, `null` = soruldu ama gateway
  // bildirmedi, metin = surum. Bilinmeyeni "eski" saymak sahada yanlis
  // cikti: 1.15.0 kurulu gateway'de eksik olan yalnizca yayinci bayragiydi.
  const [gatewaySurumu, setGatewaySurumu] = useState<string | null | undefined>(undefined);
  const gwKod = device.gatewayCode ?? null;
  useEffect(() => {
    setGatewaySurumu(undefined);
    if (!gwKod) return;
    let iptal = false;
    void (async () => {
      try {
        const durum = await fetchGatewayUpdate(gwKod);
        if (!iptal) setGatewaySurumu(durum.current_version);
      } catch {
        // Soruldu ama ogrenilemedi -> `null`: iddiada bulunmayiz.
        if (!iptal) setGatewaySurumu(null);
      }
    })();
    return () => {
      iptal = true;
    };
  }, [gwKod]);
  // Tek saat: DAKIKADA bir. Bayatlik esigi ve geri sayim ayni "simdi"yi
  // okur, yoksa rozet ile satir birbirini bir dakika boyunca yalanlardi.
  const now = useMinuteTick();
  const state = deviceRuntimeStateOf(device, now);
  const rt = device.runtimeHealth ?? null;
  // Saat teshisi — saf, test edilebilir bir modulden. Gateway alani
  // gondermiyorsa (1.15.0) null doner ve blok cizilmez.
  const saat = clockGorunum(rt);
  const cfg = device.dnp3Extended;

  /** Belgelenmis enum ise cevrilir; degilse HAM deger gosterilir.
   *  Uydurma etiket yerine gercek veriyi ekranda birakmak, sozlesme acikken
   *  eklenen yeni bir degeri gorunur kilar. */
  const enumMetin = (
    group: "sessionPolicy" | "operationMode" | "ipProbe" | "tcpProbe" | "ipEndpoint",
    value: string | null | undefined
  ): string | null => {
    if (value == null || value === "") return null;
    const key = runtimeEnumKey(group, value);
    return key ? t(key) : value;
  };

  const dk = (v: number | null | undefined): string | null =>
    typeof v === "number" && Number.isFinite(v) ? t("deviceRuntime.panel.minutes", { count: v }) : null;
  const sn = (v: number | null | undefined): string | null =>
    typeof v === "number" && Number.isFinite(v) ? t("deviceRuntime.panel.seconds", { count: v }) : null;
  const evetHayir = (v: boolean | null | undefined): string | null =>
    typeof v === "boolean" ? (v ? t("deviceRuntime.panel.yes") : t("deviceRuntime.panel.no")) : null;
  /** Epoch -> tarih. `null` = HIC OLMADI; 1970 tarihi BASILMAZ. */
  const an = (epoch: number | null | undefined): string | null => {
    const d = epochToDate(epoch);
    return d ? formatDateTime(d) : t("deviceRuntime.panel.never");
  };

  const politikaAyristi = sessionPolicyMismatch(
    rt?.configured_session_policy,
    rt?.effective_session_policy
  );

  return (
    <section className="device-runtime-panel">
      <header className="device-runtime-head">
        <div className="device-runtime-head-main">
          <span className="device-runtime-title">{t("deviceRuntime.panel.title")}</span>
          <RuntimeStateChip state={state} />
          <RuntimeSourceNote state={state} gatewayVersion={gatewaySurumu} />
        </div>
        {/* Geri sayim BASLIKTA: operatorun ilk baktigi yer burasi ve
            "gecikmis" ile "haberlesme kaybi" ayrimi orada goruluyor. */}
        <DialInCountdown runtime={rt} state={state} />
      </header>

      {/* Bayat kayitta durum karari legacy'ye dustu; gateway'in SON BILDIGI
          durumu yine de gosteriyoruz — ama "su anki durum" diye DEGIL. */}
      {state.stale && state.rawState ? (
        <p className="device-runtime-stale-note">
          {t("deviceRuntime.source.lastKnown")}: <code>{state.rawState}</code>
        </p>
      ) : null}

      {/* `report_late` BAYRAK olarak ayrica gosterilir. Durum satirini
          degistirmez — bagliligini metinde de soyluyoruz. */}
      {state.reportLate ? (
        <p className="device-runtime-flag" title={t("deviceRuntime.panel.reportLateHint")}>
          <span className="material-symbols-outlined" aria-hidden="true">
            warning
          </span>
          {t("deviceRuntime.panel.reportLate")}
        </p>
      ) : null}

      <div className="device-runtime-grid">
        {/* ---------------- YAPILANDIRMA ---------------- */}
        <div className="device-runtime-col device-runtime-col--config">
          <h4 className="device-runtime-col-title" title={t("deviceRuntime.panel.configHint")}>
            <span className="material-symbols-outlined" aria-hidden="true">
              settings
            </span>
            {t("deviceRuntime.panel.configTitle")}
          </h4>
          <ul className="device-runtime-list">
            <Row
              label={t("deviceRuntime.panel.endpoint")}
              value={enumMetin("ipEndpoint", cfg?.ip_endpoint_type)}
            />
            <Row
              label={t("deviceRuntime.panel.sessionPolicyConfigured")}
              value={enumMetin("sessionPolicy", cfg?.session_policy)}
            />
            <Row label={t("deviceRuntime.panel.dialIn")} value={dk(cfg?.dial_in_interval_min)} />
            {/* Tolerans yazilmadiysa URUN VARSAYILANI gecerlidir ve backend de
                onu uygular; bos gostermek "tolerans yok" gibi okunurdu. */}
            <Row
              label={t("deviceRuntime.panel.grace")}
              value={dk(cfg?.communication_grace_min ?? COMMUNICATION_GRACE_MIN_DEFAULT)}
            />
            <Row
              label={t("deviceRuntime.panel.reconnect")}
              value={sn(cfg?.smart_listen_reconnect_max_sec)}
            />
          </ul>
        </div>

        {/* ---------------- CALISMA ZAMANI ---------------- */}
        <div className="device-runtime-col device-runtime-col--runtime">
          <h4 className="device-runtime-col-title" title={t("deviceRuntime.panel.runtimeHint")}>
            <span className="material-symbols-outlined" aria-hidden="true">
              monitor_heart
            </span>
            {t("deviceRuntime.panel.runtimeTitle")}
          </h4>
          {rt ? (
            <ul className="device-runtime-list">
              <Row
                label={t("deviceRuntime.panel.state")}
                value={t(state.labelKey)}
                tone={state.tone}
              />
              {/* ETKIN oturum — yapilandirilandan AYRI satir. */}
              <Row
                label={t("deviceRuntime.panel.sessionPolicyEffective")}
                value={enumMetin("sessionPolicy", rt.effective_session_policy)}
                hint={politikaAyristi ? t("deviceRuntime.panel.policyMismatch") : undefined}
                tone={politikaAyristi ? "amber" : undefined}
              />
              <Row
                label={t("deviceRuntime.panel.operationMode")}
                value={enumMetin("operationMode", rt.operation_mode)}
              />
              {/* `connected=false` / `reachable=false` uyuyan bir Horstmann
                  icin BEKLENEN degerlerdir; renk verilmez. */}
              <Row label={t("deviceRuntime.panel.connected")} value={evetHayir(rt.connected)} />
              <Row label={t("deviceRuntime.panel.reachable")} value={evetHayir(rt.reachable)} />
              <Row
                label={t("deviceRuntime.panel.lastValidContact")}
                value={an(rt.last_valid_contact_epoch)}
              />
              <Row label={t("deviceRuntime.panel.lastFrame")} value={an(rt.last_frame_epoch)} />
              <Row
                label={t("deviceRuntime.panel.nextDialIn")}
                value={an(rt.next_expected_report_epoch)}
              />
              {/* Gecikme GATEWAY'IN bildirdigi degerdir; basliktaki geri sayim
                  ise `next_expected_report_epoch`tan cozulur. Ikisi ayni seyin
                  iki gosterimi degil, iki AYRI kayittir. */}
              <Row
                label={t("deviceRuntime.panel.overdue")}
                value={
                  typeof rt.report_overdue_sec === "number" && rt.report_overdue_sec > 0
                    ? sn(Math.round(rt.report_overdue_sec))
                    : null
                }
              />
            </ul>
          ) : (
            <p
              className="device-runtime-empty"
              title={t(`deviceRuntime.source.${runtimeSourceReason(gatewaySurumu)}Hint`)}
            >
              {t("deviceRuntime.panel.noRuntime")}
            </p>
          )}
        </div>

        {/* ---------------- TESHIS ---------------- */}
        <div className="device-runtime-col device-runtime-col--diag">
          <h4 className="device-runtime-col-title" title={t("deviceRuntime.panel.diagnosticsHint")}>
            <span className="material-symbols-outlined" aria-hidden="true">
              troubleshoot
            </span>
            {t("deviceRuntime.panel.diagnosticsTitle")}
          </h4>
          {rt ? (
            <>
              <ul className="device-runtime-list device-runtime-list--diag">
                {/* RENK YOK — sonda sonucu durum belirlemez. */}
                <Row
                  label={t("deviceRuntime.panel.ipProbe")}
                  value={enumMetin("ipProbe", rt.ip_probe_status)}
                />
                <Row
                  label={t("deviceRuntime.panel.tcpProbe")}
                  value={enumMetin("tcpProbe", rt.tcp_probe_status)}
                />
                <Row label={t("deviceRuntime.panel.lastProbe")} value={an(rt.last_probe_epoch)} />
              </ul>

              {/* CIHAZ SAATI — 1.15.1.
                  Sahada bir Horstmann'in RTC'si 2066 yilina kaymisti ve bu
                  Grid'de HIC gorunmuyordu: cihaz `online`, olcum gonderiyor,
                  komut kabul ediyor — ama urettigi her olay damgasi 40 yil
                  ileri.

                  BAGLANTI DURUMUNU ETKILEMEZ ve o yuzden burada, TESHIS
                  sutununda duruyor. Gateway bu alani gondermiyorsa (1.15.0)
                  blok HIC cizilmez: olculmemis bir seyi "bilinmiyor" diye
                  gostermek de bir iddiadir. */}
              {saat ? (
                <div className={`device-clock ${clockToneClass(saat.tone)}`}>
                  <div className="device-clock-head">
                    <span className="device-clock-label">{t("deviceDetail.clock.title")}</span>
                    <span className="device-clock-state">{t(saat.labelKey)}</span>
                  </div>
                  {saat.offsetText ? (
                    <div className="device-clock-row" title={t("deviceDetail.clock.offsetHint")}>
                      <span>{t("deviceDetail.clock.offset")}</span>
                      <strong>{saat.offsetText}</strong>
                    </div>
                  ) : null}
                  {/* UC DURUMLU: `null` iken satir HIC cizilmez —
                      "hic IIN gorulmedi" ile "istemiyor" ayni sey degil. */}
                  {saat.needTime !== null ? (
                    <p className="device-clock-flag">
                      {saat.needTime
                        ? t("deviceDetail.clock.needTimeFlag")
                        : t("deviceDetail.clock.noNeedTimeFlag")}
                    </p>
                  ) : null}
                  <p className="device-clock-hint">{t(saat.hintKey)}</p>
                </div>
              ) : null}

              <p className="device-runtime-diag-note">{t("deviceRuntime.panel.diagnosticsHint")}</p>
            </>
          ) : (
            <p className="device-runtime-empty">{t("deviceRuntime.panel.noRuntime")}</p>
          )}
        </div>
      </div>
    </section>
  );
}
