/**
 * Sifre gucu — YAZARKEN gorunen geri bildirim.
 *
 * NEDEN: kullanici sifreyi yaziyor, kaydediyor ve ancak SONRA "cok kisa" gibi
 * bir hata goruyordu. Kurallar yazarken gorunurse dusunulen sifre daha
 * yazilirken duzelir.
 *
 * BU BIR GUVENLIK OLCUSU DEGIL: gercek dayaniklilik entropi ile olculur ve
 * sunucu tarafinda zorlanir. Buradaki puan, kullaniciya "daha karisik yap"
 * demenin okunur bir yoludur; hicbir sifreyi ENGELLEMEZ (uzunluk disinda).
 */

export const MIN_PASSWORD_LENGTH = 8;

export type PasswordRuleKey = "length" | "letter" | "digit" | "symbol";

export type PasswordStrength = {
  /** Saglanan kural sayisi (0..4). */
  score: number;
  /** 0..4 -> "weak" | "fair" | "good" | "strong" */
  level: "empty" | "weak" | "fair" | "good" | "strong";
  /** Kural bazinda durum — arayuz onay listesi cizer. */
  rules: Record<PasswordRuleKey, boolean>;
};

export function passwordStrength(value: string): PasswordStrength {
  const rules: Record<PasswordRuleKey, boolean> = {
    length: value.length >= MIN_PASSWORD_LENGTH,
    letter: /[a-zA-ZçğıöşüÇĞİÖŞÜ]/.test(value),
    digit: /[0-9]/.test(value),
    symbol: /[^a-zA-Z0-9çğıöşüÇĞİÖŞÜ]/.test(value)
  };
  const score = Object.values(rules).filter(Boolean).length;
  if (!value) return { score: 0, level: "empty", rules };
  // Uzunluk kurali saglanmadan "iyi" denmez: 4 karakterlik "a1!X" dort
  // kuraldan ucunu saglar ama kotu bir sifredir.
  if (!rules.length) return { score, level: "weak", rules };
  const level = score >= 4 ? "strong" : score === 3 ? "good" : "fair";
  return { score, level, rules };
}
