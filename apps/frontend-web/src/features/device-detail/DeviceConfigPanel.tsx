/**
 * DeviceConfigPanel — cihazin yapilandirma dosyasi.
 *
 * DNP3 ozeti buradan KALDIRILDI (kullanici istegi, 2026-08-05): ayni bilgi
 * Genel Bakis ve Cihaz Yonetimi sayfalarinda zaten var; burada tekrarlanmasi
 * sekmeyi "bilgi yigini" haline getiriyor ve asil is olan yapilandirma
 * duzenlemesini geri plana atiyordu.
 *
 * "FTP Config Yonetimi" adlandirmasi da birakildi: kullanici acisindan bu
 * ekran "cihazin ayarlari"dir; dosyanin FTP uzerinden tasiniyor olmasi bir
 * TASIMA DETAYIDIR ve baslikta yer almasi gereksiz kavram yuku uretiyordu.
 */

import { DeviceFtpConfigCard } from "./DeviceFtpConfigCard";
import type { DeviceRow } from "../../shared/types";

type Props = {
  device: DeviceRow;
  token: string;
  /** Yapilandirmayi DEGISTIRME yetkisi (engineer/installer). Yetkisiz kullanici
   *  dosyayi gorur ama duzenleyemez — goruntuleme teshis icin degerlidir. */
  canConfig?: boolean;
  /** Cihaza komut gonderme yetkisi — duzenlemeden AYRI. */
  canCommand?: boolean;
};

export function DeviceConfigPanel({ device, token, canConfig = false, canCommand = false }: Props) {
  return (
    <div className="device-config-panel">
      <DeviceFtpConfigCard
        deviceId={device.id}
        deviceCode={device.code}
        accessToken={token}
        canEdit={canConfig}
        canCommand={canCommand}
      />
    </div>
  );
}
