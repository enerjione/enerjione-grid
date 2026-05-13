/**
 * Public REST API erisimi yonetim paneli.
 *
 * Iki bolum:
 *   1) Personal Access Token (PAT) listesi + yeni token formu.
 *   2) Hazir curl ornekleri — kullanici tokeni olusturduktan sonra yapistirir,
 *      Postman / harici uygulamasinda dener.
 *
 * Token plain hali yalniz POST cevabinda doner; sonra hash kalir. Kullanici
 * "kopyaladiniz mi?" uyarisini gormeden modal kapatamaz.
 */
import { useMemo, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { asyncConfirm } from "../../components/ConfirmDialog";

import type { ApiKey, ApiKeyCreated } from "../../shared/types";

type ScopeDef = {
  id: string;
  icon: string;
};

const ALL_SCOPES: ScopeDef[] = [
  { id: "devices:read", icon: "router" },
  { id: "signals:read", icon: "rss_feed" },
  { id: "telemetry:read", icon: "monitoring" },
  { id: "alarms:read", icon: "warning" },
  { id: "system:read", icon: "settings_suggest" }
];

const DEFAULT_SELECTED_SCOPES: string[] = ["devices:read", "telemetry:read", "alarms:read"];

type Props = {
  apiBaseUrl: string;
  keys: ApiKey[];
  loading: boolean;
  onRefresh: () => Promise<void>;
  onCreate: (payload: {
    name: string;
    scopes: string[];
    expires_at: string | null;
    allowed_ips: string[] | null;
  }) => Promise<ApiKeyCreated>;
  onRevoke: (keyId: number) => Promise<void>;
  /** Revoke edilmis anahtari listeden tamamen sil. Aktif anahtarlarda
   *  buton gosterilmez; sadece zaten iptal edilmis kayitlar icin. */
  onPurge?: (keyId: number) => Promise<void>;
  onToggleActive: (keyId: number, active: boolean) => Promise<void>;
};

export function ApiAccessPanel({
  apiBaseUrl,
  keys,
  loading,
  onRefresh,
  onCreate,
  onRevoke,
  onPurge,
  onToggleActive
}: Props) {
  const { t } = useTranslation();
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("");
  const [selectedScopes, setSelectedScopes] = useState<string[]>(DEFAULT_SELECTED_SCOPES);
  const [expiresAt, setExpiresAt] = useState<string>("");  // YYYY-MM-DD
  const [allowedIps, setAllowedIps] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  // Plain token modal — tek seferlik gosterim.
  const [createdToken, setCreatedToken] = useState<ApiKeyCreated | null>(null);
  const [copied, setCopied] = useState(false);

  // Bir token id icin "curl ornekleri ac" toggle.
  const [exampleTokenId, setExampleTokenId] = useState<number | null>(null);

  const sortedKeys = useMemo(
    () => [...keys].sort((a, b) => b.created_at.localeCompare(a.created_at)),
    [keys]
  );

  const toggleScope = (scope: string) => {
    setSelectedScopes((prev) =>
      prev.includes(scope) ? prev.filter((s) => s !== scope) : [...prev, scope]
    );
  };

  const resetForm = () => {
    setName("");
    setSelectedScopes(DEFAULT_SELECTED_SCOPES);
    setExpiresAt("");
    setAllowedIps("");
    setError("");
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (selectedScopes.length === 0) {
      setError(t("apiAccess.errors.scopeRequired"));
      return;
    }
    const ipList = allowedIps
      .split(/[\s,]+/)
      .map((s) => s.trim())
      .filter((s) => s.length > 0);
    setSubmitting(true);
    setError("");
    try {
      const created = await onCreate({
        name: name.trim(),
        scopes: selectedScopes,
        expires_at: expiresAt ? new Date(`${expiresAt}T23:59:59Z`).toISOString() : null,
        allowed_ips: ipList.length > 0 ? ipList : null
      });
      setCreatedToken(created);
      setCopied(false);
      setCreateOpen(false);
      resetForm();
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.errorOccurred"));
    } finally {
      setSubmitting(false);
    }
  };

  const handleRevoke = async (k: ApiKey) => {
    if (!await asyncConfirm(t("apiAccess.confirmRevoke", { name: k.name }))) return;
    try {
      await onRevoke(k.id);
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.errorOccurred"));
    }
  };

  const handlePurge = async (k: ApiKey) => {
    if (!onPurge) return;
    if (!await asyncConfirm(t("apiAccess.confirmPurge", { name: k.name }))) return;
    try {
      await onPurge(k.id);
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.errorOccurred"));
    }
  };

  const handleToggleActive = async (k: ApiKey) => {
    if (k.revoked_at) return;  // revoke'lu olan asla geri donmez
    try {
      await onToggleActive(k.id, !k.is_active);
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.errorOccurred"));
    }
  };

  const copyToClipboard = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // fallback: kullanici elle kopyalar
    }
  };

  const dateOrDash = (iso?: string | null) =>
    iso ? new Date(iso).toLocaleString() : "—";

  return (
    <section className="tab-panel api-access-panel">
      <div className="panel-head">
        <div>
          <h3>{t("apiAccess.title")}</h3>
          <p className="helper-text" style={{ marginTop: 4 }}>
            {t("apiAccess.intro")}
          </p>
        </div>
        <button className="add-user-btn" onClick={() => setCreateOpen(true)}>
          + {t("apiAccess.newToken")}
        </button>
      </div>

      {error ? <p className="error-text">{error}</p> : null}

      {/* ===== Token listesi ===== */}
      <div className="api-keys-table-wrap">
        <table className="values-table">
          <thead>
            <tr>
              <th>{t("apiAccess.cols.name")}</th>
              <th>{t("apiAccess.cols.tokenPrefix")}</th>
              <th>{t("apiAccess.cols.scopes")}</th>
              <th>{t("apiAccess.cols.created")}</th>
              <th>{t("apiAccess.cols.expires")}</th>
              <th>{t("apiAccess.cols.lastUsed")}</th>
              <th>{t("apiAccess.cols.status")}</th>
              <th className="actions-header">{t("apiAccess.cols.actions")}</th>
            </tr>
          </thead>
          <tbody>
            {sortedKeys.length === 0 && !loading ? (
              <tr>
                <td colSpan={8} className="helper-text" style={{ padding: 24, textAlign: "center" }}>
                  {t("apiAccess.empty")}
                </td>
              </tr>
            ) : null}
            {sortedKeys.map((k) => {
              const isRevoked = k.revoked_at != null;
              const isExpired =
                k.expires_at != null && new Date(k.expires_at).getTime() <= Date.now();
              const usable = !isRevoked && !isExpired && k.is_active;
              const statusLabel = isRevoked
                ? t("apiAccess.status.revoked")
                : isExpired
                ? t("apiAccess.status.expired")
                : !k.is_active
                ? t("apiAccess.status.disabled")
                : t("apiAccess.status.active");
              const statusClass = isRevoked
                ? "api-key-status--revoked"
                : isExpired
                ? "api-key-status--expired"
                : !k.is_active
                ? "api-key-status--disabled"
                : "api-key-status--active";
              return (
                <>
                  <tr key={k.id} className={!usable ? "outbound-row--inactive" : ""}>
                    <td className="api-key-name-cell">
                      <strong>{k.name}</strong>
                      {k.allowed_ips && k.allowed_ips.length > 0 ? (
                        <div className="helper-text" style={{ fontSize: 12 }}>
                          🔒 {k.allowed_ips.join(", ")}
                        </div>
                      ) : null}
                    </td>
                    <td>
                      <code className="api-key-prefix">{k.token_prefix}</code>
                    </td>
                    <td>
                      {k.scopes.map((s) => (
                        <span key={s} className="scope-chip">
                          {s}
                        </span>
                      ))}
                    </td>
                    <td>{dateOrDash(k.created_at)}</td>
                    <td>{dateOrDash(k.expires_at)}</td>
                    <td>{dateOrDash(k.last_used_at)}</td>
                    <td>
                      <span className={`api-key-status ${statusClass}`}>
                        <span className="status-dot" />
                        {statusLabel}
                      </span>
                    </td>
                    <td className="actions-cell">
                      <button
                        type="button"
                        className="secondary-btn action-btn"
                        onClick={() => setExampleTokenId(k.id)}
                      >
                        {t("apiAccess.showExamples")}
                      </button>
                      {!isRevoked ? (
                        <button
                          type="button"
                          className="edit-btn action-btn"
                          onClick={() => void handleToggleActive(k)}
                        >
                          {k.is_active
                            ? t("apiAccess.disable")
                            : t("apiAccess.enable")}
                        </button>
                      ) : null}
                      {!isRevoked ? (
                        <button
                          type="button"
                          className="danger-btn action-btn"
                          onClick={() => void handleRevoke(k)}
                        >
                          {t("apiAccess.revoke")}
                        </button>
                      ) : onPurge ? (
                        <button
                          type="button"
                          className="danger-btn action-btn"
                          onClick={() => void handlePurge(k)}
                          title={t("apiAccess.purgeTitle")}
                        >
                          <span className="material-symbols-outlined" style={{ fontSize: 16, verticalAlign: "-3px", marginRight: 4 }}>delete</span>
                          {t("apiAccess.purge")}
                        </button>
                      ) : null}
                    </td>
                  </tr>
                </>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* ===== Yeni token modal'i ===== */}
      {createOpen ? (
        <div className="settings-modal-backdrop">
          <form className="settings-modal api-key-create-modal" onSubmit={submit}>
            <h3>{t("apiAccess.modal.title")}</h3>

            <label>
              {t("apiAccess.fields.name")}
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
                placeholder={t("apiAccess.fields.namePlaceholder")}
              />
            </label>

            <fieldset className="scope-fieldset scope-fieldset--cards">
              <legend>{t("apiAccess.fields.scopes")}</legend>
              <p className="helper-text scope-fieldset-intro">
                {t("apiAccess.fields.scopesHint")}
              </p>
              <div className="scope-cards">
                {ALL_SCOPES.map((s) => {
                  const checked = selectedScopes.includes(s.id);
                  return (
                    <label
                      key={s.id}
                      className={`scope-card ${checked ? "is-selected" : ""}`}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleScope(s.id)}
                      />
                      <span className="scope-card-check">
                        <span className="material-symbols-outlined">
                          {checked ? "check_circle" : "radio_button_unchecked"}
                        </span>
                      </span>
                      <span className="scope-card-icon">
                        <span className="material-symbols-outlined">{s.icon}</span>
                      </span>
                      <span className="scope-card-text">
                        <code>{s.id}</code>
                        <small>
                          {t(`apiAccess.scopeDescriptions.${s.id}`, {
                            defaultValue: ""
                          })}
                        </small>
                      </span>
                    </label>
                  );
                })}
              </div>
              <div className="scope-card-bulk">
                <button
                  type="button"
                  className="scope-card-bulk-btn"
                  onClick={() => setSelectedScopes(ALL_SCOPES.map((s) => s.id))}
                  disabled={selectedScopes.length === ALL_SCOPES.length}
                >
                  {t("apiAccess.fields.scopesSelectAll")}
                </button>
                <button
                  type="button"
                  className="scope-card-bulk-btn"
                  onClick={() => setSelectedScopes([])}
                  disabled={selectedScopes.length === 0}
                >
                  {t("apiAccess.fields.scopesClear")}
                </button>
                <span className="scope-card-bulk-count">
                  {t("apiAccess.fields.scopesSelectedCount", {
                    count: selectedScopes.length
                  })}
                </span>
              </div>
            </fieldset>

            <div className="api-key-form-row">
              <label>
                {t("apiAccess.fields.expiresAt")}
                <input
                  type="date"
                  value={expiresAt}
                  onChange={(e) => setExpiresAt(e.target.value)}
                  min={new Date(Date.now() + 86400000).toISOString().slice(0, 10)}
                />
                <span className="helper-text">{t("apiAccess.fields.expiresHint")}</span>
              </label>

              <label>
                {t("apiAccess.fields.allowedIps")}
                <input
                  type="text"
                  value={allowedIps}
                  onChange={(e) => setAllowedIps(e.target.value)}
                  placeholder="1.2.3.4, 5.6.7.8"
                />
                <span className="helper-text">{t("apiAccess.fields.allowedIpsHint")}</span>
              </label>
            </div>

            {error ? <p className="error-text">{error}</p> : null}

            <div className="settings-actions">
              <button
                type="button"
                onClick={() => {
                  setCreateOpen(false);
                  resetForm();
                }}
              >
                {t("common.cancel")}
              </button>
              <button type="submit" disabled={submitting}>
                {submitting ? t("apiAccess.modal.creating") : t("apiAccess.modal.create")}
              </button>
            </div>
          </form>
        </div>
      ) : null}

      {/* ===== Plain token bir kerelik modal ===== */}
      {createdToken ? (
        <div className="settings-modal-backdrop">
          <div className="settings-modal api-key-created-modal">
            <header className="api-key-created-head">
              <div className="api-key-created-head-icon">
                <span className="material-symbols-outlined">vpn_key</span>
              </div>
              <div className="api-key-created-head-text">
                <h3>{t("apiAccess.created.title")}</h3>
                <p>{t("apiAccess.created.headSub")}</p>
              </div>
            </header>

            <div className="api-key-created-warning">
              <span className="material-symbols-outlined">warning</span>
              <span>{t("apiAccess.created.oneTimeWarning")}</span>
            </div>

            <label className="api-key-created-label">
              {t("apiAccess.created.tokenLabel")}
            </label>
            <div className="api-key-token-display">
              <code>{createdToken.token}</code>
              <button
                type="button"
                className="api-key-token-copy"
                onClick={() => void copyToClipboard(createdToken.token)}
                title={t("apiAccess.created.copy")}
              >
                <span className="material-symbols-outlined">
                  {copied ? "check" : "content_copy"}
                </span>
                {copied ? t("apiAccess.created.copied") : t("apiAccess.created.copy")}
              </button>
            </div>

            <div className="api-key-created-examples">
              <div className="api-key-created-examples-head">
                <span className="material-symbols-outlined">terminal</span>
                <h4>{t("apiAccess.created.firstCall")}</h4>
              </div>
              <CurlExamples apiBaseUrl={apiBaseUrl} tokenPrefix={createdToken.token} />
            </div>

            <div className="settings-actions api-key-created-actions">
              <button
                type="button"
                className="primary-btn"
                onClick={async () => {
                  if (
                    !copied &&
                    !(await asyncConfirm(t("apiAccess.created.closeWithoutCopy")))
                  ) {
                    return;
                  }
                  setCreatedToken(null);
                }}
              >
                {t("common.close")}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {/* ===== Ornekler modal'i — kullanici tabloda 'Ornekler' butonuna basinca ===== */}
      {exampleTokenId !== null ? (() => {
        const key = sortedKeys.find((k) => k.id === exampleTokenId);
        if (!key) return null;
        return (
          <div
            className="settings-modal-backdrop"
            onClick={() => setExampleTokenId(null)}
          >
            <div
              className="settings-modal api-key-examples-modal"
              onClick={(e) => e.stopPropagation()}
              role="dialog"
              aria-modal="true"
            >
              <header className="api-key-examples-head">
                <div className="api-key-examples-head-icon">
                  <span className="material-symbols-outlined">terminal</span>
                </div>
                <div className="api-key-examples-head-text">
                  <h3>{t("apiAccess.examplesModal.title", { name: key.name })}</h3>
                  <p>{t("apiAccess.examplesModal.subtitle")}</p>
                </div>
                <button
                  type="button"
                  className="api-key-examples-close"
                  onClick={() => setExampleTokenId(null)}
                  aria-label={t("common.close")}
                >
                  <span className="material-symbols-outlined">close</span>
                </button>
              </header>

              <div className="api-key-examples-body">
                <CurlExamples apiBaseUrl={apiBaseUrl} tokenPrefix={key.token_prefix} />
              </div>

              <div className="settings-actions api-key-examples-actions">
                <button
                  type="button"
                  className="primary-btn"
                  onClick={() => setExampleTokenId(null)}
                >
                  {t("common.close")}
                </button>
              </div>
            </div>
          </div>
        );
      })() : null}
    </section>
  );
}

// ===========================================================================
// CurlExamples — token icin hazir cURL kod ornekleri (4 senaryo).
// Tokeni yapistirip kopyalanir; mahremiyet icin tokenPrefix gosterimde sansurlu
// gelebilir (placeholder yontemi) ama "uretildi" modal'inda gercek token gelir.
// ===========================================================================

function CurlExamples({
  apiBaseUrl,
  tokenPrefix
}: {
  apiBaseUrl: string;
  tokenPrefix: string;
}) {
  const { t } = useTranslation();
  // apiBaseUrl uretimde "/api/v1" gibi rölatif gelir (ayni-origin). cURL
  // ornegini disaridan copy-paste eden kullanici icin scheme+host eklemek
  // zorundayiz; aksi halde "/api/v1/public/me" gibi URL kullanilamaz.
  const base = (() => {
    const trimmed = apiBaseUrl.replace(/\/+$/, "");
    if (trimmed.startsWith("/") && typeof window !== "undefined") {
      return `${window.location.origin}${trimmed}`;
    }
    return trimmed;
  })();
  // Token'in gercek mi placeholder mi oldugu: "hsl_pat_" ile basliyorsa ve
  // gercek uzunlukta ise (>20 char) direkt yapistirilabilir, aksi halde
  // kullanicidan tokenini yazmasini iste.
  const isRealToken =
    tokenPrefix.startsWith("hsl_pat_") && tokenPrefix.length > 20 && !tokenPrefix.includes("…");
  const tokenForCurl = isRealToken ? tokenPrefix : "$HSL_TOKEN";

  // Windows CMD '\' satir devamini DESTEKLEMEZ — iki satira boldugumuzde
  // Bearer ile URL arasi bosluk kayboluyor (kullanici sikayet ediyor).
  // Tek satir cURL hem Windows hem Linux/macOS hem PowerShell'de calisir.
  // Uzun komut olsalar bile kopyala butonu zaten saglandigi icin elle
  // yazim derdi yok.
  const examples = [
    {
      title: t("apiAccess.examples.me.title"),
      desc: t("apiAccess.examples.me.desc"),
      curl: `curl -H "Authorization: Bearer ${tokenForCurl}" "${base}/public/me"`
    },
    {
      title: t("apiAccess.examples.devices.title"),
      desc: t("apiAccess.examples.devices.desc"),
      curl: `curl -H "Authorization: Bearer ${tokenForCurl}" "${base}/public/devices?limit=50"`
    },
    {
      title: t("apiAccess.examples.telemetry.title"),
      desc: t("apiAccess.examples.telemetry.desc"),
      curl: `curl -H "Authorization: Bearer ${tokenForCurl}" "${base}/public/telemetry/DEV-001/latest"`
    },
    {
      title: t("apiAccess.examples.alarms.title"),
      desc: t("apiAccess.examples.alarms.desc"),
      curl: `curl -H "Authorization: Bearer ${tokenForCurl}" "${base}/public/alarms?open_only=true&level=critical"`
    }
  ];

  return (
    <div className="curl-examples">
      {!isRealToken ? (
        <p className="helper-text curl-examples-hint">
          {t("apiAccess.examples.placeholderHint")}{" "}
          <code>export HSL_TOKEN="hsl_pat_..."</code>
        </p>
      ) : null}
      {examples.map((ex, idx) => (
        <CurlBlock key={idx} title={ex.title} desc={ex.desc} code={ex.curl} />
      ))}
    </div>
  );
}

function CurlBlock({
  title,
  desc,
  code
}: {
  title: string;
  desc: string;
  code: string;
}) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* ignore */
    }
  };
  return (
    <div className="curl-block">
      <div className="curl-block-head">
        <div className="curl-block-head-text">
          <strong>{title}</strong>
          <span className="curl-block-head-desc">{desc}</span>
        </div>
        <button
          type="button"
          className={`curl-block-copy ${copied ? "is-copied" : ""}`}
          onClick={() => void copy()}
          title={t("apiAccess.created.copy")}
        >
          <span className="material-symbols-outlined">
            {copied ? "check" : "content_copy"}
          </span>
          {copied ? t("apiAccess.created.copied") : t("apiAccess.created.copy")}
        </button>
      </div>
      <pre className="curl-block-code">
        <code>{code}</code>
      </pre>
    </div>
  );
}
