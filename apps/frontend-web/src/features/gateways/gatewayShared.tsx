/**
 * Gateway ekranlarinin ortak kucuk parcalari — ekleme sihirbazi ve duzenleme
 * modali ikisi de kullanir. Token uretimi tek yerde dursun ki iki ekran farkli
 * uzunluk/alfabe kullanmasin.
 */
import { useState } from "react";
import { Check, Copy } from "lucide-react";

const TOKEN_LENGTH = 48;
const TOKEN_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";

export function generateToken(): string {
  const arr = new Uint32Array(TOKEN_LENGTH);
  window.crypto.getRandomValues(arr);
  let out = "";
  for (let i = 0; i < TOKEN_LENGTH; i += 1) out += TOKEN_CHARS.charAt(arr[i] % TOKEN_CHARS.length);
  return out;
}

/** Iki katmanli kopyalama: Clipboard API (HTTPS gerekir) + execCommand
 *  fallback'i. Saha kurulumlari cogunlukla duz HTTP oldugu icin fallback sart —
 *  Clipboard API guvenli olmayan origin'de sessizce basarisiz olur. */
export async function copyText(value: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch {
    /* fallback'e dus */
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = value;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

export function CopyButton({ value, label }: { value: string; label: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className="gw-copy-btn"
      title={label}
      aria-label={label}
      onClick={() => {
        void copyText(value).then((ok) => {
          if (!ok) return;
          setDone(true);
          window.setTimeout(() => setDone(false), 1500);
        });
      }}
    >
      {done ? <Check size={15} strokeWidth={2.4} /> : <Copy size={15} strokeWidth={2.2} />}
    </button>
  );
}
