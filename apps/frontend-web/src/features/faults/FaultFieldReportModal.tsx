/**
 * FaultFieldReportModal — saha raporu (yorum akisi) yan paneli.
 *
 * NEDEN SEBEPTEN AYRI
 * -------------------
 * Sebep bir SINIFLANDIRMADIR: kapali kume, analiz sorgulari onun uzerinden
 * calisir. Saha raporu ise SERBEST metinli, zamana yayilan bir akistir
 * ("direge cikildi", "parca degisti"). Ikisi ayni kutuda dururken hangisinin
 * kalici kayit oldugu belirsizdi ve kullanici sebep girmek isterken yorum
 * yaziyordu. Artik sebep FaultResolveModal'da, akis burada.
 *
 * SAG YAN PANEL (ortadaki bir kutu degil): akis dikeydir ve okunurken
 * arkadaki haritanin/kunyenin gorunur kalmasi ise yarar — saha ekibi
 * telefonda "hangi direk" derken ikisine birden bakiyor.
 *
 * KAPATILMIS ARIZA SALT OKUNUR: kapanmis bir kayda yeni yorum eklenmesi,
 * kapanis raporunun sonradan degismesi demektir. Yazma alani gorunmez
 * (backend de reddeder), eski yorumlar okunmaya devam eder.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Lock, MessagesSquare, Send, X } from "lucide-react";

import { useModalDialog } from "../../shared/useModalDialog";
import type { FaultComment, FaultEvent } from "../../shared/types";

/** Ad-soyaddan iki harf. Kullanici adi yoksa "?" — uydurma bas harf yok. */
export function bashafler(ad: string | null | undefined): string {
  const temiz = (ad ?? "").trim();
  if (!temiz) return "?";
  const parcalar = temiz.split(/\s+/);
  if (parcalar.length === 1) return parcalar[0].substring(0, 2).toUpperCase();
  return (parcalar[0][0] + parcalar[parcalar.length - 1][0]).toUpperCase();
}

type Props = {
  fault: FaultEvent;
  comments: FaultComment[];
  currentUsername: string;
  /** false ise yalnizca okuma (kapatilmis ariza ya da yetkisiz kullanici). */
  canComment: boolean;
  localeTag: string;
  onKapat: () => void;
  /** Hata FIRLATIR; panel hatayi kendi icinde gosterir. */
  onYorumEkle: (body: string) => Promise<void>;
};

export function FaultFieldReportModal({
  fault,
  comments,
  currentUsername,
  canComment,
  localeTag,
  onKapat,
  onYorumEkle
}: Props) {
  const { t } = useTranslation();
  const kutuRef = useModalDialog<HTMLDivElement>(onKapat);
  const [taslak, setTaslak] = useState("");
  const [calisiyor, setCalisiyor] = useState(false);
  const [hata, setHata] = useState("");

  const gonder = async () => {
    const body = taslak.trim();
    if (!body) return;
    setCalisiyor(true);
    setHata("");
    try {
      await onYorumEkle(body);
      setTaslak("");
    } catch (err) {
      setHata(err instanceof Error ? err.message : t("common.errorOccurred"));
    } finally {
      setCalisiyor(false);
    }
  };

  return (
    <div className="fdr-backdrop" role="presentation" onClick={onKapat}>
      <aside
        className="fdr"
        role="dialog"
        aria-modal="true"
        aria-labelledby="fdr-baslik"
        ref={kutuRef}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="fdr-head">
          <span className="fdr-ico">
            <MessagesSquare size={16} strokeWidth={2.2} />
          </span>
          <div className="fdr-head-body">
            <h2 id="fdr-baslik">
              {t("faults.detail.commentsTitle")}
              {comments.length > 0 ? <span className="fd-count">{comments.length}</span> : null}
            </h2>
            <p>
              {fault.line_name} · #{fault.id}
            </p>
          </div>
          <button
            type="button"
            className="fdm-x"
            onClick={onKapat}
            aria-label={t("common.close")}
          >
            <X size={16} />
          </button>
        </header>

        <ul className="fdr-list">
          {comments.length === 0 ? (
            <li className="fd-empty">{t("faults.detail.commentsHint")}</li>
          ) : (
            comments.map((c) => (
              <li
                key={c.id}
                className={`fd-comment ${c.author_username === currentUsername ? "is-mine" : ""}`}
              >
                <header>
                  <span className="fd-avatar fd-avatar--sm">{bashafler(c.author_username)}</span>
                  <strong>{c.author_username}</strong>
                  <time>{new Date(c.created_at).toLocaleString(localeTag)}</time>
                </header>
                <p>{c.body}</p>
              </li>
            ))
          )}
        </ul>

        <footer className="fdr-foot">
          {hata ? <p className="fd-error">{hata}</p> : null}
          {canComment ? (
            <div className="fd-comment-add">
              <textarea
                className="fd-textarea"
                rows={2}
                placeholder={t("faults.detail.commentPlaceholder")}
                value={taslak}
                onChange={(e) => setTaslak(e.target.value)}
                disabled={calisiyor}
              />
              <button
                type="button"
                className="fd-save fd-save--send"
                onClick={() => void gonder()}
                disabled={calisiyor || !taslak.trim()}
              >
                <Send size={14} />
                {t("faults.detail.addCommentBtn")}
              </button>
            </div>
          ) : (
            <p className="fdr-locked">
              <Lock size={13} />
              {fault.status === "closed"
                ? t("faults.detail.commentsClosed")
                : t("faults.detail.commentsReadOnly")}
            </p>
          )}
        </footer>
      </aside>
    </div>
  );
}
