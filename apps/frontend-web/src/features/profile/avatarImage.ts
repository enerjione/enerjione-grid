/**
 * Profil fotografini TARAYICIDA kucultup gomulu bir `data:` URI'sine cevirir.
 *
 * NEDEN ISTEMCIDE: kullanicinin sectigi dosya cogunlukla telefon kamerasindan
 * cikma 3-8 MB'lik bir goruntudur. Ham haliyle gondermek istegi sisirir,
 * sunucunun sinirina takilir ve kullanici sebebini anlamadigi bir hata gorur.
 * Burada 192 piksele kucultulup JPEG'e cevrildiginde tipik boyut 8-15 KB'a
 * duser — sunucuya giden veri kucucuk kalir, kullanici hicbir sinir gormez.
 *
 * KARE KIRPMA: fotograf yuvarlak gosteriliyor; kisa kenardan MERKEZE gore
 * kare kirpilmazsa dikey bir portre yuvarlagin icinde ezilir.
 */

/** Cikti kenar uzunlugu (piksel). Yuvarlak 96px cizildigi icin 2x yeterli. */
const HEDEF_PX = 192;

/** Kabul edilen dosya turleri — sunucu da ayni uc turu kabul eder. */
export const AVATAR_ACCEPT = "image/png,image/jpeg,image/webp";

/** Okuma oncesi kaba sinir: 12 MB uzeri bir dosyayi tarayicida cozmeye
 *  calismak dusuk gucIu bir istemciyi kilitleyebilir. */
const GIRDI_MAX_BYTE = 12 * 1024 * 1024;

export class AvatarError extends Error {}

/**
 * Secilen dosyayi kare, kucultulmus bir JPEG data URI'sine cevirir.
 * Hata durumunda `AvatarError` firlatir (mesaj kullaniciya gosterilebilir).
 */
export async function fileToAvatarDataUrl(
  file: File,
  labels: { tooBig: string; notImage: string; failed: string }
): Promise<string> {
  if (!file.type.startsWith("image/")) throw new AvatarError(labels.notImage);
  if (file.size > GIRDI_MAX_BYTE) throw new AvatarError(labels.tooBig);

  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const el = new Image();
      el.onload = () => resolve(el);
      el.onerror = () => reject(new AvatarError(labels.failed));
      el.src = url;
    });

    const kenar = Math.min(img.naturalWidth, img.naturalHeight);
    if (!kenar) throw new AvatarError(labels.failed);
    const sx = (img.naturalWidth - kenar) / 2;
    const sy = (img.naturalHeight - kenar) / 2;

    const canvas = document.createElement("canvas");
    canvas.width = HEDEF_PX;
    canvas.height = HEDEF_PX;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new AvatarError(labels.failed);
    // Beyaz zemin: seffaf PNG JPEG'e cevrilirken saydam alanlar SIYAH olur.
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, HEDEF_PX, HEDEF_PX);
    ctx.drawImage(img, sx, sy, kenar, kenar, 0, 0, HEDEF_PX, HEDEF_PX);

    const data = canvas.toDataURL("image/jpeg", 0.85);
    if (!data.startsWith("data:image/jpeg;base64,")) throw new AvatarError(labels.failed);
    return data;
  } finally {
    URL.revokeObjectURL(url);
  }
}

/** Ad soyaddan bas harf — fotograf yoksa yuvarlakta bu gorunur. */
export function initialsOf(fullName: string | null | undefined, fallback = "?"): string {
  const parcalar = (fullName ?? "")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  if (parcalar.length === 0) return fallback;
  if (parcalar.length === 1) return parcalar[0].slice(0, 2).toLocaleUpperCase("tr-TR");
  return (parcalar[0][0] + parcalar[parcalar.length - 1][0]).toLocaleUpperCase("tr-TR");
}
