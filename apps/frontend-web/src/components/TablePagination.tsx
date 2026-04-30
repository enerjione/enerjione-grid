import { useMemo } from "react";

type Props = {
  totalItems: number;
  page: number;
  pageSize: number;
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
  pageSizeOptions?: number[];
  itemLabel?: string;
};

const DEFAULT_OPTIONS = [50, 100, 200, 500];

export function TablePagination({
  totalItems,
  page,
  pageSize,
  onPageChange,
  onPageSizeChange,
  pageSizeOptions = DEFAULT_OPTIONS,
  itemLabel = "kayıt"
}: Props) {
  const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
  const safePage = Math.min(Math.max(1, page), totalPages);
  const startIndex = totalItems === 0 ? 0 : (safePage - 1) * pageSize + 1;
  const endIndex = Math.min(totalItems, safePage * pageSize);

  const pageNumbers = useMemo(() => buildPageNumbers(safePage, totalPages), [safePage, totalPages]);

  return (
    <div className="table-pagination">
      <div className="table-pagination-info">
        {totalItems === 0
          ? `0 ${itemLabel}`
          : `${startIndex.toLocaleString("tr-TR")}–${endIndex.toLocaleString("tr-TR")} / ${totalItems.toLocaleString("tr-TR")} ${itemLabel}`}
      </div>
      <div className="table-pagination-controls">
        <label className="table-pagination-size">
          Sayfa boyutu
          <select
            value={pageSize}
            onChange={(event) => onPageSizeChange(Number(event.target.value))}
          >
            {pageSizeOptions.map((opt) => (
              <option key={opt} value={opt}>
                {opt}
              </option>
            ))}
          </select>
        </label>

        <div className="table-pagination-buttons">
          <button
            type="button"
            className="pagination-btn"
            onClick={() => onPageChange(1)}
            disabled={safePage <= 1}
            aria-label="İlk sayfa"
            title="İlk sayfa"
          >
            «
          </button>
          <button
            type="button"
            className="pagination-btn"
            onClick={() => onPageChange(safePage - 1)}
            disabled={safePage <= 1}
            aria-label="Önceki sayfa"
            title="Önceki sayfa"
          >
            ‹
          </button>

          {pageNumbers.map((entry, idx) =>
            entry === "…" ? (
              <span key={`gap-${idx}`} className="pagination-gap">
                …
              </span>
            ) : (
              <button
                key={entry}
                type="button"
                className={`pagination-btn ${entry === safePage ? "pagination-btn-active" : ""}`}
                onClick={() => onPageChange(entry)}
              >
                {entry}
              </button>
            )
          )}

          <button
            type="button"
            className="pagination-btn"
            onClick={() => onPageChange(safePage + 1)}
            disabled={safePage >= totalPages}
            aria-label="Sonraki sayfa"
            title="Sonraki sayfa"
          >
            ›
          </button>
          <button
            type="button"
            className="pagination-btn"
            onClick={() => onPageChange(totalPages)}
            disabled={safePage >= totalPages}
            aria-label="Son sayfa"
            title="Son sayfa"
          >
            »
          </button>
        </div>
      </div>
    </div>
  );
}

function buildPageNumbers(current: number, total: number): (number | "…")[] {
  if (total <= 7) {
    return Array.from({ length: total }, (_, i) => i + 1);
  }
  const result: (number | "…")[] = [1];
  const start = Math.max(2, current - 1);
  const end = Math.min(total - 1, current + 1);
  if (start > 2) result.push("…");
  for (let i = start; i <= end; i += 1) result.push(i);
  if (end < total - 1) result.push("…");
  result.push(total);
  return result;
}
