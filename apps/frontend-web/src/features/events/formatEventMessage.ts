import i18n from "../../shared/i18n";
import { signalLabel } from "../../shared/signalLabel";
import type { SystemEvent } from "../../shared/types";

/** Slug gorunumlu deger mi? (reset_all_fcis gibi) */
const SLUG_RE = /^[a-z0-9_]+$/;

/**
 * Olay mesajini i18n destegi ile cevirir.
 *
 * Backend yeni event yazdiginda metadata.json icine `_i18n: { key, params }`
 * koyuyor. Frontend bu key'i `events.message.<key>` altinda arar; varsa
 * params ile interpole eder. Yoksa backend'den gelen ham `message`'a duser
 * (eski TR kayitlar geriye donuk uyumlu).
 */
export function formatEventMessage(event: SystemEvent): string {
  // 1) metadata_json icindeki _i18n bilgisini cek
  let i18nKey: string | undefined;
  let i18nParams: Record<string, unknown> | undefined;
  if (event.metadata_json) {
    try {
      const parsed = JSON.parse(event.metadata_json);
      const tag = parsed?._i18n;
      if (tag && typeof tag === "object") {
        if (typeof tag.key === "string" && tag.key.length > 0) i18nKey = tag.key;
        if (tag.params && typeof tag.params === "object") {
          i18nParams = tag.params as Record<string, unknown>;
        }
      }
    } catch {
      // bozuk metadata — yoksay
    }
  }

  if (i18nKey) {
    const fullKey = `events.message.${i18nKey}`;
    if (i18n.exists(fullKey)) {
      const params = { ...(i18nParams ?? {}) };
      // KOMUT ADI CEVIRISI: command parametresi makine slug'i tasir
      // (reset_all_fcis). Ekranda sinyal sozlugundeki Turkce ad gosterilir
      // ("Tüm Göstergeleri Sıfırla"); sozlukte yoksa slug oldugu gibi kalir.
      if (typeof params.command === "string" && SLUG_RE.test(params.command)) {
        params.command = signalLabel(`master.${params.command}`, params.command);
      }
      return i18n.t(fullKey, params);
    }
  }

  // Fallback: backend'den gelen ham mesaj (TR)
  return event.message;
}
