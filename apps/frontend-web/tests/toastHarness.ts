/**
 * ToastProvider'i GERCEKTEN CALISTIRAN test kosumu.
 *
 * NEDEN BOYLE — kaynak aramasi yetmiyor
 * -------------------------------------
 * Bu depoda daha once davranis bozukken yesil kalan testler oldu: dosyada
 * bir metnin gecmesi, o kodun CALISTIGINI kanitlamaz. Susturma ozelliginde
 * yanlis karar en agir sonucu uretir — kullanicinin "Kaydet" hatasinin
 * yutulmasi sessiz veri kaybi gibi davranir. Bu yuzden burada gercek
 * ToastProvider derlenir, calistirilir ve URETTIGI AGAC incelenir.
 *
 * Bagimlilik EKLENMEDI: jsdom/testing-library yok. React'in yerine bu testin
 * ihtiyaci kadar (useState/useRef/useCallback/useMemo/useEffect/useContext +
 * createContext + JSX fabrikasi) minik bir kosum kullanilir; ToastProvider bu
 * yuzeyin disina cikmiyor. Derleme, projenin kendi esbuild'i ile yapilir
 * (run.mjs `E1_ESBUILD` ile mutlak yolunu geciyor).
 *
 * ProjectSettingsProvider BILEREK sahte: gercegi `shared/api.ts`'i ve onunla
 * birlikte `import.meta.env`'i Node'a tasirdi. Sahtenin dondurdugu alan
 * adlari (`toast_muted`, `toast_position`) sozlesmenin ta kendisi — provider
 * baska bir alan okumaya baslarsa test duser.
 */

import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

/** coz() ciktisindaki dugum. */
export type Dugum = {
  tag: string;
  props: Record<string, unknown>;
  children: Dugum[];
  metin: string;
};

export type ToastModulu = {
  ToastProvider: (props: { children: unknown }) => unknown;
  useToast: () => {
    show: (kind: string, message: string, options?: Record<string, unknown>) => void;
    success: (message: string, options?: Record<string, unknown>) => void;
    error: (message: string, options?: Record<string, unknown>) => void;
    info: (message: string, options?: Record<string, unknown>) => void;
    warning: (message: string, options?: Record<string, unknown>) => void;
  };
  toastPosition: (v: string | null | undefined) => string;
  TOAST_POSITIONS: readonly string[];
  DEFAULT_TOAST_POSITION: string;
  /** JSX fabrikasi. */
  h: (type: unknown, props: unknown, ...kids: unknown[]) => unknown;
  /** Kok elemani kurar ve ilk cizimi yapar. */
  kur: (el: unknown) => Dugum[];
  /** Son cizilen agac. */
  agac: () => Dugum[];
  /** Hook deposunu bosaltir (testler arasinda izolasyon). */
  sifirla: () => void;
  /** Sahte ProjectSettingsProvider'in dondurecegi ayarlar. */
  ayarlariKur: (s: Record<string, unknown>) => void;
};

const MINI_REACT = `
const depolar = new Map();
let aktif = null;
let kokEl = null;
let sonAgac = [];

function depo(yol) {
  let s = depolar.get(yol);
  if (!s) { s = { hooks: [], idx: 0 }; depolar.set(yol, s); }
  return s;
}

export function useState(baslangic) {
  const s = aktif, i = s.idx++;
  if (!(i in s.hooks)) s.hooks[i] = typeof baslangic === "function" ? baslangic() : baslangic;
  const set = (v) => {
    s.hooks[i] = typeof v === "function" ? v(s.hooks[i]) : v;
    cizim();
  };
  return [s.hooks[i], set];
}
export function useRef(baslangic) {
  const s = aktif, i = s.idx++;
  if (!(i in s.hooks)) s.hooks[i] = { current: baslangic };
  return s.hooks[i];
}
export function useCallback(fn) { aktif.idx++; return fn; }
export function useMemo(fn) { aktif.idx++; return fn(); }
export function useEffect() { aktif.idx++; }
export function useContext(ctx) { return ctx.__deger; }
export function createContext(varsayilan) {
  const ctx = { __deger: varsayilan };
  ctx.Provider = { __provider: true, __ctx: ctx };
  return ctx;
}

export function h(type, props, ...kids) {
  const p = { ...(props || {}) };
  if (kids.length === 1) p.children = kids[0];
  else if (kids.length > 1) p.children = kids;
  return { __el: true, type, props: p };
}
globalThis.__h = h;
globalThis.__Fragment = "#fragment";

function coz(node, yol) {
  if (node == null || node === false || node === true || node === "") return [];
  if (Array.isArray(node)) return node.flatMap((n, i) => coz(n, yol + "[" + i + "]"));
  if (typeof node === "string" || typeof node === "number") {
    return [{ tag: "#text", props: {}, children: [], metin: String(node) }];
  }
  if (!node.__el) return [];
  const { type, props } = node;
  if (typeof type === "function") {
    const ad = type.name || "fn";
    const s = depo(yol + "/" + ad);
    const onceki = aktif;
    aktif = s; s.idx = 0;
    let cikti;
    try { cikti = type(props); } finally { aktif = onceki; }
    return coz(cikti, yol + "/" + ad + "#");
  }
  if (type && type.__provider) {
    const eski = type.__ctx.__deger;
    type.__ctx.__deger = props.value;
    try { return coz(props.children, yol + "/P"); }
    finally { type.__ctx.__deger = eski; }
  }
  if (type === "#fragment") return coz(props.children, yol + "/F");
  return [{
    tag: String(type),
    props,
    children: coz(props.children, yol + "/" + String(type)),
    metin: "",
  }];
}

export function kur(el) { kokEl = el; return cizim(); }
export function cizim() { sonAgac = coz(kokEl, ""); return sonAgac; }
export function agac() { return sonAgac; }
export function sifirla() { depolar.clear(); kokEl = null; sonAgac = []; }
`;

const AYAR_STUB = `
let _ayarlar = {};
export function ayarlariKur(s) { _ayarlar = s || {}; }
export function useProjectSettings() {
  return { settings: _ayarlar, loading: false, refresh: async () => {}, applyLocal: () => {} };
}
`;

const duz = (p: string) => p.replace(/\\/g, "/");

/** ToastProvider'i minik React kosumuyla derleyip yukler. */
export async function yukleToastModulu(): Promise<ToastModulu> {
  // Mutlak yol run.mjs tarafindan veriliyor (bundle gecici dizinde kosuyor).
  const { build } = (await import(process.env.E1_ESBUILD!)) as {
    build: (o: Record<string, unknown>) => Promise<unknown>;
  };

  const dizin = mkdtempSync(join(tmpdir(), "e1-toast-"));
  const reactYolu = join(dizin, "mini-react.mjs");
  const ayarYolu = join(dizin, "ayar-stub.mjs");
  const cikti = join(dizin, "bundle.mjs");
  writeFileSync(reactYolu, MINI_REACT, "utf8");
  writeFileSync(ayarYolu, AYAR_STUB, "utf8");

  const saglayici = duz(`${process.cwd()}/src/components/ToastProvider.tsx`);

  try {
    await build({
      stdin: {
        contents:
          `export * from ${JSON.stringify(saglayici)};\n` +
          `export * from ${JSON.stringify(duz(reactYolu))};\n` +
          `export { ayarlariKur } from ${JSON.stringify(duz(ayarYolu))};\n`,
        resolveDir: process.cwd(),
        loader: "ts",
      },
      outfile: cikti,
      bundle: true,
      format: "esm",
      platform: "node",
      target: "node20",
      // Klasik JSX: fabrika globalThis uzerinden verilir, boylece hicbir
      // modulde `h` import'una gerek kalmaz.
      jsx: "transform",
      jsxFactory: "globalThis.__h",
      jsxFragment: "globalThis.__Fragment",
      logLevel: "warning",
      plugins: [
        {
          name: "e1-toast-stub",
          setup(b: {
            onResolve: (
              o: { filter: RegExp },
              cb: (a: { path: string }) => { path: string } | undefined,
            ) => void;
          }) {
            b.onResolve({ filter: /^react$/ }, () => ({ path: reactYolu }));
            b.onResolve({ filter: /ProjectSettingsProvider$/ }, () => ({ path: ayarYolu }));
          },
        },
      ],
    });

    const mod = (await import(pathToFileURL(cikti).href)) as ToastModulu;
    return mod;
  } finally {
    // Bundle import edildikten sonra dosyalar silinebilir; Node modulu
    // bellekte tutar. Silme basarisiz olursa testi dusurmeyelim.
    try {
      rmSync(dizin, { recursive: true, force: true });
    } catch {
      /* gecici dizin kalirsa sorun degil */
    }
  }
}

/** Agacta verilen sinifi tasiyan ilk dugumu bul. */
export function sinifBul(dugumler: Dugum[], sinif: string): Dugum | null {
  for (const d of dugumler) {
    const c = typeof d.props.className === "string" ? d.props.className : "";
    if (c.split(/\s+/).includes(sinif)) return d;
    const alt = sinifBul(d.children, sinif);
    if (alt) return alt;
  }
  return null;
}

/** Bir dugumun altindaki tum metinleri toplar. */
export function metinler(d: Dugum | null): string[] {
  if (!d) return [];
  const cikti: string[] = [];
  const gez = (n: Dugum) => {
    if (n.tag === "#text" && n.metin) cikti.push(n.metin);
    n.children.forEach(gez);
  };
  gez(d);
  return cikti;
}
