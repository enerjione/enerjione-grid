/**
 * Direk ROL modeli — TEK dogruluk kaynagi.
 *
 * Sistem ekipman envanteri DEGIL: direk siniflandirmasi iki bagimsiz eksende
 * ISLEV anlatir (bir direk ayni anda bransman + tuketim noktasi olabilir):
 *
 *   topology_role  hattin neresi:  line_start | transit | branch | line_end
 *                                  | cable_transition
 *   energy_role    enerji gorevi:  none | generation | consumption
 *                                  | bidirectional
 *
 * Harita: ANA ikon topolojik rolden gelir; enerji rolu ikonun kosesinde
 * kucuk ROZET olarak gosterilir (uretim ↑ yesil, tuketim ↓ mavi, cift yon
 * ⇅ mor). Ariza/enerjili-enerjisiz/haberlesme gibi OPERASYONEL durumlar bu
 * modele KARISMAZ — onlar ayri alanlardan gelir.
 *
 * Eski ekipman tipleri (transformer/breaker/...) role DONUSTURULUR
 * (rolesOf) — eski backend'e karsi da calisir.
 */
import type { EnergyRole, Pole, TopologyRole } from "../../shared/types";

export type TopologyRoleMeta = {
  value: TopologyRole;
  /** Harita pin sinifi (grid-pole-pin is-*). Bos = duz gecis diregi. */
  cls: string;
  /** Pin icine yazilan sembol (duz direkte yok — sira numarasi gorunur). */
  symbol: string;
  title: string;
  labelKey: string;
  /** Form secim listesindeki material ikon (SUBSET'te var olanlardan). */
  icon: string;
  /** Kullanici FORMDAN secebilir mi? line_start/line_end SECILEMEZ —
   *  hat basi/sonu KONUMDAN turetilir (1. direk = bas, son direk = son).
   *  Kullanicinin 5. direge "hat basi" diyebilmesi modeli bozuyordu. */
  selectable: boolean;
};

export const TOPOLOGY_ROLE_META: TopologyRoleMeta[] = [
  // line_start/line_end: cls/symbol BOS — hat ucu gorseli KONUMSAL
  // is-start/is-end isaretinden gelir (tek dogruluk: sira). Rol degeri
  // veri olarak durur (migration/import yazabilir) ama gorunumu ve secimi
  // konum yonetir; celiski (ortadaki direge "bas" gorseli) olusamaz.
  { value: "line_start", cls: "", symbol: "", title: "Hat başlangıcı", labelKey: "engineering.grid.roleTopo.line_start", icon: "flag", selectable: false },
  { value: "transit", cls: "", symbol: "", title: "Geçiş direği", labelKey: "engineering.grid.roleTopo.transit", icon: "location_on", selectable: true },
  // BRANSMAN SEMBOLU CIZIM (SVG), harf DEGIL.
  //
  // Eskiden pin yesil-turkuaz bir DAIRE idi ve icinde "⑂" glifi vardi.
  // Haritada cihaz marker'i da yesil daire (icinde beyaz simsek) oldugu icin
  // ikisi uzaktan ayirt edilemiyordu — operator bransman diregini cihaz
  // saniyordu. Simdi hem RENK hem BICIM hem SEMBOL ayri: indigo, kosesi
  // yuvarlatilmis kare ve catallanma cizimi (bkz. styles.css `.is-branchpt`).
  //
  // Glif yerine SVG: "⑂" (U+2442) yaygin sistem fontlarinin cogunda YOK;
  // bulunmadigi yerde tarayici yedek font arar ve boyut/hiza pinden pine
  // kayar. Cizim her yerde ayni gorunur ve ikon subset'ine de bagli degil.
  {
    value: "branch",
    cls: "is-branchpt",
    symbol:
      '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">' +
      '<path fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round" ' +
      'stroke-linejoin="round" d="M12 22v-8.5M12 13.5 6.5 8M12 13.5 17.5 8"/>' +
      '<circle cx="5.6" cy="6.4" r="2.6" fill="#fff"/>' +
      '<circle cx="18.4" cy="6.4" r="2.6" fill="#fff"/>' +
      "</svg>",
    title: "Branşman noktası",
    labelKey: "engineering.grid.roleTopo.branch",
    icon: "account_tree",
    selectable: true
  },
  { value: "line_end", cls: "", symbol: "", title: "Hat sonu", labelKey: "engineering.grid.roleTopo.line_end", icon: "location_off", selectable: false },
  { value: "cable_transition", cls: "is-cable", symbol: "▼", title: "Kablo geçişi", labelKey: "engineering.grid.roleTopo.cable_transition", icon: "cable", selectable: true },
];

/** Formda gosterilecek roller (uclar haric — onlar konumdan). */
export const SELECTABLE_TOPOLOGY_ROLES = TOPOLOGY_ROLE_META.filter((m) => m.selectable);

export type EnergyRoleMeta = {
  value: EnergyRole;
  /** Rozet sinifi (grid-pole-energy is-*). none = rozet yok. */
  cls: string;
  /** Rozet sembolu. */
  badge: string;
  title: string;
  labelKey: string;
  icon: string;
};

export const ENERGY_ROLE_META: EnergyRoleMeta[] = [
  { value: "none", cls: "", badge: "", title: "Özel enerji görevi yok", labelKey: "engineering.grid.roleEnergy.none", icon: "remove" },
  { value: "generation", cls: "is-gen", badge: "↑", title: "Üretim noktası", labelKey: "engineering.grid.roleEnergy.generation", icon: "bolt" },
  { value: "consumption", cls: "is-cons", badge: "↓", title: "Tüketim noktası", labelKey: "engineering.grid.roleEnergy.consumption", icon: "power" },
  { value: "bidirectional", cls: "is-bi", badge: "⇅", title: "Çift yönlü nokta", labelKey: "engineering.grid.roleEnergy.bidirectional", icon: "swap_horiz" },
];

/** Eski ekipman tipi -> rol cifti (backend LEGACY_TYPE_TO_ROLES ile AYNI). */
const LEGACY_TYPE_TO_ROLES: Record<string, [TopologyRole, EnergyRole]> = {
  pole: ["transit", "none"],
  transformer: ["transit", "consumption"],
  breaker: ["transit", "none"],
  disconnector: ["transit", "none"],
  fuse_cutout: ["transit", "none"],
  source: ["transit", "generation"],
  cable_transition: ["cable_transition", "none"],
  branch_point: ["branch", "none"],
};

/** Direkten rolleri oku; rol alanlari yoksa (eski veri/backend) ekipman
 *  tipinden donustur. */
export function rolesOf(
  pole: Pick<Pole, "topology_role" | "energy_role" | "pole_type"> | {
    topology_role?: string | null;
    energy_role?: string | null;
    pole_type?: string | null;
  }
): { topo: TopologyRole; energy: EnergyRole } {
  const topo = pole.topology_role as TopologyRole | undefined;
  const energy = pole.energy_role as EnergyRole | undefined;
  if (topo || energy) {
    return { topo: topo ?? "transit", energy: energy ?? "none" };
  }
  const legacy = LEGACY_TYPE_TO_ROLES[pole.pole_type ?? "pole"] ?? LEGACY_TYPE_TO_ROLES.pole;
  return { topo: legacy[0], energy: legacy[1] };
}

export function topologyMeta(role: TopologyRole): TopologyRoleMeta {
  return TOPOLOGY_ROLE_META.find((m) => m.value === role) ?? TOPOLOGY_ROLE_META[1];
}

export function energyMeta(role: EnergyRole): EnergyRoleMeta {
  return ENERGY_ROLE_META.find((m) => m.value === role) ?? ENERGY_ROLE_META[0];
}

/** divIcon HTML'ine eklenecek enerji rozeti (none = bos dize). */
export function energyBadgeHtml(role: EnergyRole): string {
  const m = energyMeta(role);
  if (!m.badge) return "";
  return `<span class="grid-pole-energy ${m.cls}" title="${m.title}">${m.badge}</span>`;
}
