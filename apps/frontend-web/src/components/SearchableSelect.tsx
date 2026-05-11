import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import { useTranslation } from "react-i18next";

export type SearchableOption = {
  /** Stabil deger — onChange'e gonderilir. */
  value: string;
  /** Ana etiket — listede kalin gosterilir, arama burada yapilir. */
  label: string;
  /** Opsiyonel ikincil bilgi — kod/etiket vb. listede kucuk metinde. */
  secondary?: string | null;
};

type Props = {
  value: string;
  onChange: (value: string) => void;
  options: SearchableOption[];
  /** Bos durum etiketi (orn. "Tum cihazlar"). value === allValue ise label
   *  bu olur; null gecilirse 'all' secenegi listelenmez. */
  allValue?: string;
  allLabel?: string;
  /** Trigger butonun title attribute (a11y). */
  title?: string;
  /** Input placeholder (arama). */
  searchPlaceholder?: string;
  /** Liste bos kaldiginda gosterilecek metin. */
  emptyText?: string;
  /** CSS className — secim trigger'i + dropdown wrapper'ina uygulanir. */
  className?: string;
  /** Liste maksimum yuksekligi (px). */
  maxListHeight?: number;
};

/**
 * Bagimliliksiz, aranabilir <select> yerine combobox.
 *
 * - 600+ secenekli listelerde native <select>'in arama eksikligini cozer.
 * - Klavye: ArrowUp/ArrowDown navigasyon, Enter seçim, Escape kapan.
 * - Disari tiklama kapatir.
 * - Arama label + secondary uzerinde case-insensitive + tum kelime kismi
 *   eslesmesiyle calisir (kullanici "tst" yazinca "TST-12" + "Hat TST"
 *   bulur).
 */
export function SearchableSelect({
  value,
  onChange,
  options,
  allValue = "all",
  allLabel,
  title,
  searchPlaceholder,
  emptyText,
  className,
  maxListHeight = 280,
}: Props) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIdx, setActiveIdx] = useState(0);
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const listRef = useRef<HTMLUListElement | null>(null);

  const resolvedAllLabel = allLabel ?? t("common.all", { defaultValue: "All" });
  const resolvedSearchPlaceholder =
    searchPlaceholder ?? t("common.search", { defaultValue: "Search…" });
  const resolvedEmptyText =
    emptyText ?? t("common.noResults", { defaultValue: "No results" });

  // 'all' opsiyonu listenin basina enjekte edilir (allLabel verilmemisse atla).
  const augmentedOptions = useMemo<SearchableOption[]>(() => {
    if (!allLabel && allLabel !== "") {
      // varsayilan: hep ekle (allValue = "all"). allLabel null degil.
    }
    return [
      { value: allValue, label: resolvedAllLabel },
      ...options,
    ];
  }, [allValue, resolvedAllLabel, options, allLabel]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return augmentedOptions;
    return augmentedOptions.filter((opt) => {
      const a = opt.label.toLowerCase();
      const b = (opt.secondary ?? "").toLowerCase();
      return a.includes(q) || b.includes(q);
    });
  }, [augmentedOptions, query]);

  const selected = useMemo(
    () => augmentedOptions.find((o) => o.value === value) ?? augmentedOptions[0],
    [augmentedOptions, value]
  );

  // Dropdown acildiginda arama kutusuna otomatik focus, query temizle.
  useEffect(() => {
    if (open) {
      setQuery("");
      setActiveIdx(0);
      // Focus, render bittikten sonra; setTimeout 0 yeterli.
      const id = window.setTimeout(() => {
        inputRef.current?.focus();
      }, 0);
      return () => window.clearTimeout(id);
    }
    return undefined;
  }, [open]);

  // Disari tiklama -> kapan.
  useEffect(() => {
    if (!open) return undefined;
    const onClick = (e: MouseEvent) => {
      if (!wrapperRef.current?.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  // Aktif satir gorunur kalsin diye scrollIntoView.
  useEffect(() => {
    if (!open) return;
    const el = listRef.current?.children[activeIdx] as HTMLElement | undefined;
    el?.scrollIntoView({ block: "nearest" });
  }, [activeIdx, open]);

  const handleSelect = (opt: SearchableOption) => {
    onChange(opt.value);
    setOpen(false);
  };

  const handleKeyDown = (e: ReactKeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIdx((i) => Math.min(filtered.length - 1, i + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIdx((i) => Math.max(0, i - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const opt = filtered[activeIdx];
      if (opt) handleSelect(opt);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
    }
  };

  return (
    <div
      ref={wrapperRef}
      className={`searchable-select ${className ?? ""} ${open ? "is-open" : ""}`}
    >
      <button
        type="button"
        className="searchable-select-trigger"
        onClick={() => setOpen((p) => !p)}
        title={title}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span className="searchable-select-trigger-label">
          {selected?.label ?? resolvedAllLabel}
        </span>
        <span className="material-symbols-outlined searchable-select-trigger-chev">
          {open ? "expand_less" : "expand_more"}
        </span>
      </button>
      {open ? (
        <div className="searchable-select-popover">
          <div className="searchable-select-search">
            <span className="material-symbols-outlined">search</span>
            <input
              ref={inputRef}
              type="text"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setActiveIdx(0);
              }}
              onKeyDown={handleKeyDown}
              placeholder={resolvedSearchPlaceholder}
            />
          </div>
          {filtered.length === 0 ? (
            <div className="searchable-select-empty">{resolvedEmptyText}</div>
          ) : (
            <ul
              ref={listRef}
              className="searchable-select-list"
              role="listbox"
              style={{ maxHeight: maxListHeight }}
            >
              {filtered.map((opt, idx) => {
                const isSelected = opt.value === value;
                const isActive = idx === activeIdx;
                return (
                  <li
                    key={opt.value}
                    role="option"
                    aria-selected={isSelected}
                    className={
                      "searchable-select-item" +
                      (isActive ? " is-active" : "") +
                      (isSelected ? " is-selected" : "")
                    }
                    onMouseEnter={() => setActiveIdx(idx)}
                    onClick={() => handleSelect(opt)}
                  >
                    <span className="searchable-select-item-label">{opt.label}</span>
                    {opt.secondary ? (
                      <span className="searchable-select-item-secondary">
                        {opt.secondary}
                      </span>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  );
}
