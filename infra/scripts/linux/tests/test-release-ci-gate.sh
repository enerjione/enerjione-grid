#!/usr/bin/env bash
# RELEASE KAPISI — davranis testleri.
#
# NEDEN VAR
# ---------
# v2.95.0'da ana CI DUSTU ama Release akisi tamamlandi: dokuz imaj basildi
# ve GitHub Release yayinlandi. Sebep, Release'in CI sonucuna hic
# bakmamasiydi. Kapi eklendi; bu testler kapinin GERCEKTEN kapandigini
# dogruluyor — cunku "acilmayan kapi" ile "hic kapi yok" ayni seydir.
#
# Testler gercek GitHub API'sine CIKMAZ: betik `E1_GATE_QUERY_CMD` ile
# sorgu komutunu disaridan alacak sekilde yazildi, burada sabit JSON
# veriliyor.
set -euo pipefail

KOK="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)}"
BETIK="$KOK/infra/scripts/linux/release-ci-gate.sh"
SHA="e025147d5606421154741f022a917b257a62b0c2"
BASKA_SHA="1111111111111111111111111111111111111111"

# Testler de CALISAN Python'u cozer; `python3` Windows'ta Store kisayoluna
# duserse testler bos gecerdi (yasandi).
PY_BIN=""
for aday in python3 python; do
  if command -v "$aday" >/dev/null 2>&1 && "$aday" -c "import sys" >/dev/null 2>&1; then
    PY_BIN="$aday"; break
  fi
done
if [ -z "$PY_BIN" ]; then echo "Calisan Python yok"; exit 1; fi

gecti=0
basarisiz=0

bildir() {
  if [ "$1" -eq 0 ]; then
    echo "  OK   $2"; gecti=$((gecti + 1))
  else
    echo "  HATA $2"; basarisiz=$((basarisiz + 1))
  fi
}

# Sabit JSON uretmek icin gecici dosyalar.
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

json_yaz() {  # $1 dosya, $2 icerik
  printf '%s' "$2" > "$TMP/$1"
}

kosum() {  # status conclusion sha
  printf '{"workflow_runs":[{"status":"%s","conclusion":%s,"head_sha":"%s","run_started_at":"2026-08-14T18:00:00Z","html_url":"https://ornek/1"}]}' \
    "$1" "$2" "$3"
}

echo "== A) CI success -> kapi GECER =="
json_yaz a.json "$(kosum completed '"success"' "$SHA")"
if E1_GATE_QUERY_CMD="cat $TMP/a.json" E1_GATE_TIMEOUT_SEC=5 E1_GATE_POLL_SEC=1 \
     bash "$BETIK" "$SHA" >/dev/null 2>&1; then
  bildir 0 "CI success -> exit 0"
else
  bildir 1 "CI success -> exit 0"
fi

echo "== B) CI failure -> kapi KAPANIR =="
json_yaz b.json "$(kosum completed '"failure"' "$SHA")"
if E1_GATE_QUERY_CMD="cat $TMP/b.json" E1_GATE_TIMEOUT_SEC=5 E1_GATE_POLL_SEC=1 \
     bash "$BETIK" "$SHA" >/dev/null 2>&1; then
  bildir 1 "CI failure -> exit != 0"
else
  bildir 0 "CI failure -> exit != 0"
fi

echo "== C) CI calisiyor -> bekler, sonra success =="
# Ilk cagride 'in_progress', sonrakilerde 'success' donen bir sayac komutu.
cat > "$TMP/c.sh" <<CEOF
#!/usr/bin/env bash
n=\$(cat "$TMP/c.sayac" 2>/dev/null || echo 0)
echo \$((n + 1)) > "$TMP/c.sayac"
if [ "\$n" -lt 1 ]; then
  printf '{"workflow_runs":[{"status":"in_progress","conclusion":null,"head_sha":"$SHA","run_started_at":"2026-08-14T18:00:00Z","html_url":"u"}]}'
else
  printf '{"workflow_runs":[{"status":"completed","conclusion":"success","head_sha":"$SHA","run_started_at":"2026-08-14T18:00:00Z","html_url":"u"}]}'
fi
CEOF
chmod +x "$TMP/c.sh"; rm -f "$TMP/c.sayac"
if E1_GATE_QUERY_CMD="bash $TMP/c.sh" E1_GATE_TIMEOUT_SEC=20 E1_GATE_POLL_SEC=1 \
     bash "$BETIK" "$SHA" >/dev/null 2>&1; then
  bildir 0 "calisiyor -> success -> exit 0"
else
  bildir 1 "calisiyor -> success -> exit 0"
fi

echo "== D) CI calisiyor -> bekler, sonra failure =="
cat > "$TMP/d.sh" <<DEOF
#!/usr/bin/env bash
n=\$(cat "$TMP/d.sayac" 2>/dev/null || echo 0)
echo \$((n + 1)) > "$TMP/d.sayac"
if [ "\$n" -lt 1 ]; then
  printf '{"workflow_runs":[{"status":"in_progress","conclusion":null,"head_sha":"$SHA","run_started_at":"2026-08-14T18:00:00Z","html_url":"u"}]}'
else
  printf '{"workflow_runs":[{"status":"completed","conclusion":"failure","head_sha":"$SHA","run_started_at":"2026-08-14T18:00:00Z","html_url":"u"}]}'
fi
DEOF
chmod +x "$TMP/d.sh"; rm -f "$TMP/d.sayac"
if E1_GATE_QUERY_CMD="bash $TMP/d.sh" E1_GATE_TIMEOUT_SEC=20 E1_GATE_POLL_SEC=1 \
     bash "$BETIK" "$SHA" >/dev/null 2>&1; then
  bildir 1 "calisiyor -> failure -> exit != 0"
else
  bildir 0 "calisiyor -> failure -> exit != 0"
fi

echo "== E) Kosum HIC bulunamadi -> FAIL CLOSED =="
json_yaz e.json '{"workflow_runs":[]}'
if E1_GATE_QUERY_CMD="cat $TMP/e.json" E1_GATE_TIMEOUT_SEC=3 E1_GATE_POLL_SEC=1 \
     bash "$BETIK" "$SHA" >/dev/null 2>&1; then
  bildir 1 "kosum yok -> exit != 0 (fail closed)"
else
  bildir 0 "kosum yok -> exit != 0 (fail closed)"
fi

echo "== F) BASKA commit'in yesil CI'i KABUL EDILMEZ =="
json_yaz f.json "$(kosum completed '"success"' "$BASKA_SHA")"
if E1_GATE_QUERY_CMD="cat $TMP/f.json" E1_GATE_TIMEOUT_SEC=3 E1_GATE_POLL_SEC=1 \
     bash "$BETIK" "$SHA" >/dev/null 2>&1; then
  bildir 1 "baska commit success -> exit != 0"
else
  bildir 0 "baska commit success -> exit != 0"
fi

echo "== G) Iptal edilmis CI de KAPIYI KAPATIR =="
json_yaz g.json "$(kosum completed '"cancelled"' "$SHA")"
if E1_GATE_QUERY_CMD="cat $TMP/g.json" E1_GATE_TIMEOUT_SEC=3 E1_GATE_POLL_SEC=1 \
     bash "$BETIK" "$SHA" >/dev/null 2>&1; then
  bildir 1 "cancelled -> exit != 0"
else
  bildir 0 "cancelled -> exit != 0"
fi

echo "== H) Release akisi kapiyi build'den ONCE kosturuyor =="
# Kapinin varligi yetmez; imajlar basildiktan SONRA kosarsa v2.95.0'daki
# ariza tekrarlanir (imajlar yayinlanmisti).
REL="$KOK/.github/workflows/release.yml"
if "$PY_BIN" - "$REL" <<'PYEOF'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
isler = d["jobs"]
kapi = "ci-gate"
if kapi not in isler:
    print("ci-gate isi yok"); sys.exit(1)
def ihtiyac(ad):
    n = isler[ad].get("needs") or []
    return [n] if isinstance(n, str) else list(n)
for hedef in ("build", "release", "deploy-vds"):
    if hedef in isler and kapi not in ihtiyac(hedef):
        print(f"{hedef} isi {kapi} kapisina bagli degil"); sys.exit(1)
sys.exit(0)
PYEOF
then
  bildir 0 "build/release/deploy ci-gate'e bagli"
else
  bildir 1 "build/release/deploy ci-gate'e bagli"
fi

echo
echo "gecti=$gecti basarisiz=$basarisiz"
[ "$basarisiz" -eq 0 ]
