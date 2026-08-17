#!/usr/bin/env bash
# EnerjiOne Grid — install/update/uninstall icin ortak arayuz katmani.
# Bu dosya 'source' ile dahil edilir, bagimsiz calistirilmaz.
#
# Tasarim notlari:
#   * Tum ciktilar 74 karakterlik sabit bir govdeye hizalanir; adim satirlari,
#     kutular ve ozet ayni kenar cizgisini paylasir.
#   * Renk yalnizca stdout bir terminal ise kullanilir — `| tee`, journalctl
#     veya dosyaya yonlendirmede duz metin kalir.
#   * Sorular /dev/tty uzerinden okunur. `curl ... | sudo bash` kullaniminda
#     stdin BORU HATTIDIR; eski surum bu yuzden hicbir soru soramiyor ve
#     sessizce varsayilana dusuyordu (systemd kaydi bu yuzden atlaniyordu).

# ---------------------------------------------------------------------------
# Kaynak / registry konfigurasyonu — TEK YER
# ---------------------------------------------------------------------------
# Repo veya registry adresi degisince tek dosya duzeltilir. Hepsi env ile
# override edilebilir (test kurulumu, fork, ozel registry).
#
# Surum modeli: saha cihazlari DALI degil TAG'i takip eder. Yayin `v2.25.0`
# tag'i ile yapilir; install/update varsayilan olarak en son `v*` tag'ine
# gecer. Bir dala sabitlenmek istenirse E1_REF=main verilir (edge kanali).
E1_REPO_SLUG="${E1_REPO_SLUG:-enerjione/enerjione-grid}"
E1_REPO_URL="${E1_REPO_URL:-https://github.com/${E1_REPO_SLUG}.git}"
E1_RAW_BASE="${E1_RAW_BASE:-https://raw.githubusercontent.com/${E1_REPO_SLUG}}"
# curl | bash modunda _lib.sh'in cekilecegi ref. Tag'ler henuz bilinmedigi
# icin bootstrap her zaman ana daldan okunur.
E1_BOOTSTRAP_REF="${E1_BOOTSTRAP_REF:-main}"
# Imajlarin bulundugu registry yolu. compose bunu ${E1_REGISTRY} ile okur.
E1_REGISTRY_DEFAULT="${E1_REGISTRY_DEFAULT:-ghcr.io/${E1_REPO_SLUG}}"

# ---------------------------------------------------------------------------
# Renk paleti
# ---------------------------------------------------------------------------
if [[ -t 1 ]] && [[ -z "${NO_COLOR:-}" ]]; then
  E1_RED=$'\033[0;31m'
  E1_GREEN=$'\033[0;32m'
  E1_YELLOW=$'\033[1;33m'
  E1_BLUE=$'\033[0;34m'
  E1_CYAN=$'\033[0;36m'
  E1_WHITE=$'\033[97m'
  # Marka turuncusu (#e67c00). 256 renk paletinde en yakin ton 208; 8 renkli
  # eski terminallerde otomatik olarak sariya duser.
  E1_ORANGE=$'\033[38;5;208m'
  E1_BOLD=$'\033[1m'
  E1_DIM=$'\033[2m'
  E1_RESET=$'\033[0m'
else
  E1_RED='' E1_GREEN='' E1_YELLOW='' E1_BLUE='' E1_CYAN='' E1_WHITE='' E1_ORANGE=''
  E1_BOLD='' E1_DIM='' E1_RESET=''
fi

# Govde genisligi — kutular ve ayraclar bunu kullanir.
E1_WIDTH=74

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
# "ENERJI" BEYAZ, "ONE" ve "GRID" TURUNCU.
# ASCII sanatinda harfler bitisik oldugu icin ayirici bosluk sutunu yok;
# bolme noktasi (45. sutun) harf genislikleri sayilarak bulundu. Sanat
# degistirilirse bu satirlarin ikiye ayrilmasi da yeniden hesaplanmali.
e1_banner() {
  local w="${E1_WHITE}${E1_BOLD}" o="${E1_ORANGE}${E1_BOLD}" r="${E1_RESET}"
  echo
  printf '  %s███████╗███╗   ██╗███████╗██████╗      ██╗██╗%s%s ██████╗ ███╗   ██╗███████╗%s\n' "$w" "$r" "$o" "$r"
  printf '  %s██╔════╝████╗  ██║██╔════╝██╔══██╗     ██║██║%s%s██╔═══██╗████╗  ██║██╔════╝%s\n' "$w" "$r" "$o" "$r"
  printf '  %s█████╗  ██╔██╗ ██║█████╗  ██████╔╝     ██║██║%s%s██║   ██║██╔██╗ ██║█████╗  %s\n' "$w" "$r" "$o" "$r"
  printf '  %s██╔══╝  ██║╚██╗██║██╔══╝  ██╔══██╗██   ██║██║%s%s██║   ██║██║╚██╗██║██╔══╝  %s\n' "$w" "$r" "$o" "$r"
  printf '  %s███████╗██║ ╚████║███████╗██║  ██║╚█████╔╝██║%s%s╚██████╔╝██║ ╚████║███████╗%s\n' "$w" "$r" "$o" "$r"
  printf '  %s╚══════╝╚═╝  ╚═══╝╚══════╝╚═╝  ╚═╝ ╚════╝ ╚═╝%s%s ╚═════╝ ╚═╝  ╚═══╝╚══════╝%s\n' "$w" "$r" "$o" "$r"
  printf '  %s                       ██████╗ ██████╗ ██╗██████╗ %s\n' "$w" "$r"
  printf '  %s                      ██╔════╝ ██╔══██╗██║██╔══██╗%s\n' "$w" "$r"
  printf '  %s                      ██║  ███╗██████╔╝██║██║  ██║%s\n' "$w" "$r"
  printf '  %s                      ██║   ██║██╔══██╗██║██║  ██║%s\n' "$w" "$r"
  printf '  %s                      ╚██████╔╝██║  ██║██║██████╔╝%s\n' "$w" "$r"
  printf '  %s                       ╚═════╝ ╚═╝  ╚═╝╚═╝╚═════╝ %s\n' "$w" "$r"
  printf '%s             Industrial Grid Monitoring Platform%s\n' "${E1_DIM}" "${E1_RESET}"
}

# Adim basliklarinda tekrarlanan tek satirlik marka seridi.
# Surum numarasi E1_VERSION_LABEL set edildiginde gosterilir — install.sh
# repo klonlandiktan SONRA doldurur (oncesinde bilinmiyor).
E1_VERSION_LABEL="${E1_VERSION_LABEL:-}"
e1_brand() {
  # ENERJI beyaz · ONE turuncu · GRID beyaz
  printf '%s%sENERJI%s%s%sONE%s %s%sGRID%s' \
    "${E1_WHITE}" "${E1_BOLD}" "${E1_RESET}" \
    "${E1_ORANGE}" "${E1_BOLD}" "${E1_RESET}" \
    "${E1_WHITE}" "${E1_BOLD}" "${E1_RESET}"
  if [[ -n "$E1_VERSION_LABEL" ]]; then
    printf '%s  v%s%s' "${E1_DIM}" "$E1_VERSION_LABEL" "${E1_RESET}"
  fi
}

# ---------------------------------------------------------------------------
# Kutular ve satirlar
# ---------------------------------------------------------------------------
e1_rule() {
  local ch="${1:-─}" out=""
  local i
  for ((i = 0; i < E1_WIDTH; i++)); do out+="$ch"; done
  printf '  %s%s%s\n' "${E1_DIM}" "$out" "${E1_RESET}"
}

# Baslikli kutu acilisi: e1_box "KURULUM ÖZETİ"
e1_box() {
  local title="$1"
  echo
  e1_rule "─"
  printf '  %s%s%s\n' "${E1_BOLD}" "$title" "${E1_RESET}"
  e1_rule "─"
}

# Hizali anahtar/deger satiri: e1_kv "Hedef dizin" "/opt/enerjione-grid"
e1_kv() {
  printf '  %s%-16s%s %s\n' "${E1_DIM}" "$1" "${E1_RESET}" "$2"
}

# ---------------------------------------------------------------------------
# Adimlar — sure olcumu ile
# ---------------------------------------------------------------------------
E1_STEP_TOTAL=0
E1_STEP_CURRENT=0
E1_STEP_STARTED_AT=0
# Acik adim var mi? `E1_STEP_STARTED_AT > 0` ile kontrol ETMEYIN: ilk adim
# script'in 0. saniyesinde baslarsa degeri 0 kalir ve suresi hic basilmaz.
E1_STEP_ACTIVE=0
E1_RUN_STARTED_AT=$SECONDS

e1_set_steps() {
  E1_STEP_TOTAL="$1"
  E1_STEP_CURRENT=0
  E1_STEP_ACTIVE=0
  E1_RUN_STARTED_AT=$SECONDS
}

# Saniyeyi "1 dk 12 sn" gibi okunur hale getirir.
e1_fmt_duration() {
  local s="${1:-0}"
  if ((s < 60)); then
    printf '%d sn' "$s"
  else
    printf '%d dk %d sn' "$((s / 60))" "$((s % 60))"
  fi
}

# Onceki adimin suresini kapatip yenisini acar.
e1_step() {
  if ((E1_STEP_ACTIVE == 1)); then
    printf '  %s└ %s%s\n' "${E1_DIM}" "$(e1_fmt_duration $((SECONDS - E1_STEP_STARTED_AT)))" "${E1_RESET}"
  fi
  E1_STEP_CURRENT=$((E1_STEP_CURRENT + 1))
  E1_STEP_STARTED_AT=$SECONDS
  E1_STEP_ACTIVE=1
  echo
  # Marka seridi her adimda tekrarlanir: uzun kurulumda ekranin neresine
  # bakarsa baksin kullanici hangi urunun kuruldugunu ve surumu gorur.
  # Adim sayaci saga yaslanir.
  local brand step_tag pad
  brand="$(e1_brand)"
  step_tag="[${E1_STEP_CURRENT}/${E1_STEP_TOTAL}]"
  # Gorunur uzunluk: renk kodlari haric. `e1_brand` sabit metin urettigi icin
  # dolgu hesabini ham uzunluktan degil bilinen etiketten yapiyoruz.
  local visible="ENERJIONE GRID"
  [[ -n "$E1_VERSION_LABEL" ]] && visible="${visible}  v${E1_VERSION_LABEL}"
  pad=$((E1_WIDTH - ${#visible} - ${#step_tag}))
  ((pad < 1)) && pad=1
  printf '  %s%*s%s%s%s\n' "$brand" "$pad" "" "${E1_BOLD}${E1_BLUE}" "$step_tag" "${E1_RESET}"
  printf '  %s%s%s\n' "${E1_BOLD}" "$*" "${E1_RESET}"
}

# Son adimin suresini kapat (ozet oncesi cagirilir).
e1_step_done() {
  if ((E1_STEP_ACTIVE == 1)); then
    printf '  %s└ %s%s\n' "${E1_DIM}" "$(e1_fmt_duration $((SECONDS - E1_STEP_STARTED_AT)))" "${E1_RESET}"
    E1_STEP_ACTIVE=0
  fi
}

e1_total_elapsed() { e1_fmt_duration $((SECONDS - E1_RUN_STARTED_AT)); }

# ---------------------------------------------------------------------------
# Log seviyeleri
# ---------------------------------------------------------------------------
e1_info() { printf '  %s·%s %s\n' "${E1_CYAN}" "${E1_RESET}" "$*"; }
e1_ok()   { printf '  %s✓%s %s\n' "${E1_GREEN}" "${E1_RESET}" "$*"; }
e1_warn() { printf '  %s!%s %s\n' "${E1_YELLOW}" "${E1_RESET}" "$*" >&2; }
e1_err()  { printf '  %s✗%s %s\n' "${E1_RED}" "${E1_RESET}" "$*" >&2; }

# Uzun surecek adimlar icin beklenti yonetimi — kullanici donduk sanmasin.
e1_hint() { printf '  %s  %s%s\n' "${E1_DIM}" "$*" "${E1_RESET}"; }

# ---------------------------------------------------------------------------
# Uzun komutlari CANLI sayacla calistir
# ---------------------------------------------------------------------------
# apt-get, git clone gibi adimlar cikti uretmeden dakikalarca surebiliyor ve
# kurulumcu ekrana bakip "kilitlendi" diyerek Ctrl+C yapiyordu. Bu sarmalayici
# komutu arka planda calistirip her 3 saniyede gecen sureyi ayni satira yazar;
# ekranda HER ZAMAN hareket olur. Komut cikti uretse bile onu loga alir, hata
# durumunda son satirlari gosterir.
#
#   e1_run "Kaynak kod indiriliyor" git clone --branch x URL DIR
E1_BAR_WIDTH=26

e1_run() {
  local label="$1"; shift
  local logf rc=0 secs=0 pid pos=0 dir=1 i bar

  logf="$(mktemp)"

  # `< /dev/null` KRITIK: `curl ... | sudo bash` kullaniminda script'in
  # kendisi stdin'den (boru hattindan) okunur. Arka plan komutu ayni stdin'i
  # devralirsa script metnini yiyebilir veya okumaya calisip kilitlenebilir.
  "$@" >"$logf" 2>&1 </dev/null &
  pid=$!

  # Yuzde bilinemez (apt/git ilerleme yuzdesi vermiyor), bu yuzden gidip
  # gelen belirsiz ilerleme cubugu: kullanici "calisiyor" oldugunu gorur.
  #
  # ONEMLI: `-t 1` kontrolune BAGLI KALINMAZ. Terminal algilanmadigi bazi
  # ortamlarda (sudo + boru hatti kombinasyonlari) hicbir ilerleme
  # basilmiyordu ve kurulum donmus gorunuyordu. Terminal varsa \r ile ayni
  # satir tazelenir; yoksa periyodik olarak YENI SATIR basilir. Her iki
  # durumda da ekranda hareket olur.
  local is_tty=0
  [[ -t 1 ]] && is_tty=1

  # Terminal yoksa ilk satiri hemen bas — kullanici adimin basladigini gorsun.
  ((is_tty == 0)) && printf '  · %s…\n' "$label"

  while kill -0 "$pid" 2>/dev/null; do
    if ((is_tty == 1)); then
      bar=""
      for ((i = 0; i < E1_BAR_WIDTH; i++)); do
        if ((i >= pos && i < pos + 5)); then bar+="█"; else bar+="░"; fi
      done
      printf '\r  %s%s%s  %s[%s]%s  %s%s%s  ' \
        "${E1_CYAN}" "$label" "${E1_RESET}" \
        "${E1_ORANGE}" "$bar" "${E1_RESET}" \
        "${E1_DIM}" "$(e1_fmt_duration $secs)" "${E1_RESET}"
      pos=$((pos + dir))
      # `((...)) && x` kalibi yerine if: kosul yanlis oldugunda satir 1
      # donuyor ve `set -e` + ERR trap altinda gereksiz risk olusturuyor.
      if ((pos <= 0)); then dir=1; fi
      if ((pos >= E1_BAR_WIDTH - 5)); then dir=-1; fi
    elif ((secs > 0 && secs % 15 == 0)); then
      # Terminal yok: 15 saniyede bir yeni satir. Log dosyasini sismeden
      # ilerlemeyi gosterir.
      printf '    … %s\n' "$(e1_fmt_duration $secs)"
    fi
    sleep 1
    secs=$((secs + 1))
  done
  wait "$pid" || rc=$?
  ((is_tty == 1)) && printf '\r%*s\r' $((E1_WIDTH + 24)) ''

  if ((rc != 0)); then
    e1_err "${label}: BASARISIZ (cikis kodu ${rc})"
    e1_err "Son satirlar:"
    tail -n 20 "$logf" | sed 's/^/      /' >&2
    rm -f "$logf"
    return "$rc"
  fi
  rm -f "$logf"
  e1_ok "${label} — $(e1_fmt_duration $secs)"
  return 0
}

# ---------------------------------------------------------------------------
# apt paket kurulumu — ALAKASIZ bozuk depolara ragmen
# ---------------------------------------------------------------------------
# SAHA VAKASI (Dell OEM Ubuntu 24.04): makinede birinin kurdugu Google Chrome
# deposu duruyordu ama imza anahtari yoktu:
#
#   Err:1 https://dl.google.com/linux/chrome/deb stable InRelease
#     NO_PUBKEY FD533C07C264648F
#   E: The repository '...' is not signed.
#
# `apt-get update` bu yuzden 100 dondu ve TUM kurulum orada oldu — oysa bizim
# ihtiyacimiz olan depolarin (archive.ubuntu.com, security.ubuntu.com) HEPSI
# basariliydi ve istenen paket pekala kurulabilirdi.
#
# Ubuntu 24.04 eski `apt-key` / `trusted.gpg` anahtar deposunu artik
# kullanmadigi icin eski yontemle eklenmis her ucuncu taraf deposu (Chrome,
# eski PPA'lar, kaldirilmis paketlerin arta kalan depolari) bu hatayi verir.
# Musteri makinesine kurulum yapan bir urunde bunun kurulumu bloke etmesi
# kabul edilemez.
#
# COZUM: `apt-get update`'in cikis kodu ARTIK OLUMCUL DEGIL. Basarisiz depolar
# operatore adiyla bildirilir; gercek karar `apt-get install`'da verilir —
# paket gercekten kurulamiyorsa ORADA duruyoruz. Boylece:
#   * alakasiz bozuk depo kurulumu durdurmaz,
#   * gercek bir sorun (ag yok, paket bulunamiyor) yine yakalanir,
#   * operator makinesinde bozuk bir depo oldugunu ogrenir.
#
#   e1_apt_install "Paketler kuruluyor (git curl)" git curl openssl
e1_apt_install() {
  local label="$1"; shift
  local logf broken

  logf="$(mktemp)"
  # TEK apt-get update. Ciktiyi $logf'e aliyoruz (hangi depo bozuk?) ve
  # `exit 0` ile cikis kodunu YUTUYORUZ (bkz. yukaridaki aciklama) — boylece
  # e1_run'in ilerleme gostergesi korunur ama kismi hata kurulumu durdurmaz.
  e1_run "Paket listesi guncelleniyor" \
    env DEBIAN_FRONTEND=noninteractive \
    sh -c "apt-get update -q >'$logf' 2>&1; exit 0"

  broken="$(grep -E '^(Err|E|W): ' "$logf" 2>/dev/null | head -8 || true)"
  rm -f "$logf"

  if [[ -n "$broken" ]]; then
    e1_warn "apt-get update kismen basarisiz — su depolar atlandi:"
    printf '%s\n' "$broken" | sed 's/^/        /' >&2
    e1_hint "Bu depolar EnerjiOne icin gerekli DEGIL; kuruluma devam ediliyor."
    e1_hint "Makinede kalici olarak duzeltmek icin bozuk depoyu devre disi birakin:"
    e1_hint "  sudo mv /etc/apt/sources.list.d/<depo>.list{,.disabled} && sudo apt-get update"
  fi

  # ASIL KARAR BURADA: paket kurulamiyorsa dur.
  if ! e1_run "$label" \
        env DEBIAN_FRONTEND=noninteractive apt-get install -y -q "$@"; then
    e1_err "Gerekli paketler kurulamadi: $*"
    if [[ -n "$broken" ]]; then
      e1_err "Yukaridaki bozuk depo(lar) sebep olabilir — once onlari duzeltin."
    fi
    e1_err "Kontrol icin:  sudo apt-get update"
    return 1
  fi
  return 0
}

# ---------------------------------------------------------------------------
# Docker servisi saglikli olana kadar bekle
# ---------------------------------------------------------------------------
# `docker compose up -d` bir bagimlilik saglikli degilse geriye tek satirlik
# "dependency failed to start: container X is unhealthy" birakir; kurulumcu
# ekranda SEBEBI goremez ve kurulum yarida kalir. Bu yardimci servisi tek tek
# bekler, ilerlemeyi ekrana yazar ve basarisiz olursa container'in son
# loglarini otomatik doker — sebep her zaman ekranda kalir.
#
#   e1_wait_healthy rabbitmq 240
#
# Healthcheck tanimlanmamis servisler icin "running" yeterli sayilir.
# Cagiran dizin compose dosyasinin bulundugu dizin olmali.
e1_wait_healthy() {
  local svc="$1" timeout="${2:-120}" secs=0 cid state health is_tty=0
  [[ -t 1 ]] && is_tty=1
  ((is_tty == 0)) && printf '  · %s hazir olmasi bekleniyor…\n' "$svc"

  while ((secs < timeout)); do
    cid="$(docker compose ps -q "$svc" 2>/dev/null || true)"
    if [[ -n "$cid" ]]; then
      state="$(docker inspect -f '{{.State.Status}}' "$cid" 2>/dev/null || echo bilinmiyor)"
      health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}yok{{end}}' "$cid" 2>/dev/null || echo yok)"
      if [[ "$health" == "healthy" ]] || { [[ "$health" == "yok" && "$state" == "running" ]]; }; then
        ((is_tty == 1)) && printf '\r%*s\r' $((E1_WIDTH + 24)) ''
        e1_ok "${svc} hazir — $(e1_fmt_duration $secs)"
        return 0
      fi
    else
      state="baslatilmadi"
      health="-"
    fi

    if ((is_tty == 1)); then
      printf '\r  %s%s%s bekleniyor  %s[%s/%s]%s  %s%s%s  ' \
        "${E1_CYAN}" "$svc" "${E1_RESET}" \
        "${E1_ORANGE}" "$state" "$health" "${E1_RESET}" \
        "${E1_DIM}" "$(e1_fmt_duration $secs)" "${E1_RESET}"
    elif ((secs > 0 && secs % 15 == 0)); then
      printf '    … %s (%s/%s)\n' "$(e1_fmt_duration $secs)" "$state" "$health"
    fi
    sleep 2
    secs=$((secs + 2))
  done

  ((is_tty == 1)) && printf '\r%*s\r' $((E1_WIDTH + 24)) ''
  e1_err "${svc}: $(e1_fmt_duration $timeout) icinde hazir olmadi (durum: ${state}/${health})."
  e1_err "Son loglar (${svc}):"
  docker compose logs --tail 40 --no-color "$svc" 2>&1 | sed 's/^/      /' >&2 || true
  return 1
}

# ---------------------------------------------------------------------------
# Saglikli olmayan servisi OTOMATIK onar
# ---------------------------------------------------------------------------
# Saha PC'sinin basinda teknik biri yok: kurulum "su komutlari calistirin"
# deyip durmamali, kendisi toparlamayi denemeli. Iki asamali:
#
#   1) Container'i sifirdan yarat. Yarim kalan kurulumdan artakalan bozuk
#      container/ag durumunu temizler; VERI ALANINA DOKUNMAZ.
#   2) Hala olmuyorsa servisin veri volume'unu silip sifirdan baslat.
#
# 2. asama YALNIZCA veri kaybi kabul edilebilir servisler icin yapilir
# (rabbitmq, nats). RabbitMQ'da kuyruklar gecicidir ve gateway kullanicilarini
# backend yeniden uretir; NATS JetStream stream'lerini backend acilista ensure
# eder. POSTGRES bu listede DEGILDIR — orada silinecek sey abonenin verisidir,
# asla otomatik silmeyiz.
#
#   e1_repair_service rabbitmq 240
E1_WIPEABLE_SERVICES=" rabbitmq nats "

e1_repair_service() {
  local svc="$1" timeout="${2:-180}" cid vol dest

  e1_warn "${svc} hazir olmadi — otomatik onarim (1/2): container yeniden yaratiliyor."
  docker compose up -d --force-recreate "$svc" >/dev/null 2>&1 || true
  if e1_wait_healthy "$svc" "$timeout"; then
    e1_ok "${svc} onarildi (container yeniden yaratildi)."
    return 0
  fi

  if [[ "$E1_WIPEABLE_SERVICES" != *" $svc "* ]]; then
    e1_err "${svc} icin veri alani sifirlama GUVENLI DEGIL — otomatik onarim burada duruyor."
    return 1
  fi

  # Volume adini tahmin etmek yerine container'in kendi mount tablosundan
  # okuruz; compose proje adi dizin adina gore degistigi icin sabit isim
  # varsaymak hataliydi.
  cid="$(docker compose ps -aq "$svc" 2>/dev/null | head -n1 || true)"
  vol=""
  if [[ -n "$cid" ]]; then
    vol="$(docker inspect -f '{{range .Mounts}}{{if eq .Type "volume"}}{{.Name}} {{end}}{{end}}' "$cid" 2>/dev/null | awk '{print $1}' || true)"
  fi
  if [[ -z "$vol" ]]; then
    vol="$(docker volume ls -q 2>/dev/null | grep -E "(^|_)${svc}-data$" | head -n1 || true)"
  fi
  if [[ -z "$vol" ]]; then
    e1_err "${svc} veri alani bulunamadi — otomatik onarim burada duruyor."
    return 1
  fi

  e1_warn "Otomatik onarim (2/2): ${svc} veri alani sifirlaniyor (${vol})."
  e1_hint "Bu alanda kalici veri yok — kuyruk/stream tanimlari yeniden uretilir."
  docker compose rm -sf "$svc" >/dev/null 2>&1 || true
  docker volume rm "$vol" >/dev/null 2>&1 || true
  docker compose up -d "$svc" >/dev/null 2>&1 || true
  if e1_wait_healthy "$svc" "$timeout"; then
    e1_ok "${svc} onarildi (veri alani sifirlandi)."
    return 0
  fi
  return 1
}

# ---------------------------------------------------------------------------
# Teshis raporu — tek dosya, destege gonderilecek
# ---------------------------------------------------------------------------
# Kurulumcudan ekrandaki loglari kopyalamasini beklemek gercekci degil.
# Basarisizlikta her seyi tek dosyaya yazip yolunu ekrana veririz.
# Stdout'a SADECE dosya yolunu basar (cagiran $(...) ile yakalar).
# ---------------------------------------------------------------------------
# Basarisiz servislerin loglarini EKRANDA goster
# ---------------------------------------------------------------------------
# Eskiden hata aninda yalnizca "loglara bakin: docker compose logs" deniyordu.
# Kurulumcu ekranda hicbir SEBEP gormeden komut kopyalamak zorunda kaliyordu;
# sahada telefonla destek isterken de "ne yaziyor?" sorusunun cevabi yoktu.
#
# Yalnizca SORUNLU servisler basilir (unhealthy / exited / created). Tum
# stack'in logunu dokmek ekrani doldurup asil satiri yine gizlerdi.
e1_show_failing_logs() {
  local kuyruk="${1:-40}"
  local isim durum bulundu=0

  while read -r isim; do
    [[ -z "$isim" ]] && continue
    durum="$(docker inspect --format '{{.State.Status}}{{if .State.Health}}/{{.State.Health.Status}}{{end}}' "$isim" 2>/dev/null || echo '')"
    case "$durum" in
      running|running/healthy|"") continue ;;
    esac
    bulundu=1
    e1_err "--- ${isim} (${durum}) son ${kuyruk} satir ---"
    docker logs --tail "$kuyruk" "$isim" 2>&1 | sed 's/^/      /' >&2 || true
  done < <(docker compose ps -a --format '{{.Name}}' 2>/dev/null || true)

  if [[ "$bulundu" -eq 0 ]]; then
    e1_err "--- tum servislerin son ${kuyruk} satiri ---"
    docker compose logs --tail "$kuyruk" 2>&1 | sed 's/^/      /' >&2 || true
  fi
}

e1_write_diag_report() {
  local dir="${1:-.}" failed="${2:-}" f svc
  f="${dir}/kurulum-hata-raporu.txt"
  {
    echo "EnerjiOne Grid — kurulum teshis raporu"
    echo "Surum      : ${E1_VERSION_LABEL:-bilinmiyor}"
    echo "Tarih      : $(date -Is 2>/dev/null || true)"
    echo "Basarisiz  :${failed}"
    echo
    echo "===== sistem ====="
    uname -a 2>&1 || true
    echo "--- bellek ---";  free -m 2>&1 || true
    echo "--- disk ---";    df -h "$dir" / 2>&1 || true
    echo "--- docker ---";  docker version 2>&1 || true
    echo
    echo "===== docker compose ps ====="
    docker compose ps -a 2>&1 || true
    for svc in $failed; do
      echo
      echo "===== ${svc} — son 200 satir log ====="
      docker compose logs --tail 200 --no-color "$svc" 2>&1 || true
      echo
      echo "===== ${svc} — healthcheck gecmisi ====="
      docker inspect -f '{{if .State.Health}}{{range .State.Health.Log}}[{{.ExitCode}}] {{.Output}}{{end}}{{else}}healthcheck yok{{end}}' \
        "$(docker compose ps -aq "$svc" 2>/dev/null | head -n1)" 2>&1 || true
    done
  } > "$f" 2>&1
  chmod 644 "$f" 2>/dev/null || true
  e1_chown_target "$f" 2>/dev/null || true
  printf '%s' "$f"
}

# ---------------------------------------------------------------------------
# Hata cikisi + beklenmeyen hata yakalayici
# ---------------------------------------------------------------------------
E1_DYING=0

e1_die() {
  E1_DYING=1
  e1_step_done
  echo >&2
  e1_rule "═" >&2
  printf '  %s%sKURULUM DURDU%s\n' "${E1_RED}" "${E1_BOLD}" "${E1_RESET}" >&2
  e1_rule "═" >&2
  echo >&2
  printf '  %b\n' "$*" >&2
  echo >&2
  if [[ -n "${E1_HELP_HINT:-}" ]]; then
    printf '  %s%s%s\n\n' "${E1_DIM}" "${E1_HELP_HINT}" "${E1_RESET}" >&2
  fi
  exit 1
}

# `set -e` altinda beklenmeyen bir komut patlarsa sessizce kapanmak yerine
# nerede kirildigini soyler. install.sh/update.sh basinda etkinlestirilir.
# ---------------------------------------------------------------------------
# GERI ALMA (rollback) — yarim kalan kurulum iz birakmasin
# ---------------------------------------------------------------------------
# Kurulum bir adimda duserse cihazda yarim bir kurulum kalir: container'lar,
# volume'ler, systemd unit'leri, uretilmis `.env`. Kullanici tekrar denedigi
# hangi parcanin eski hangisinin yeni oldugunu bilemez; en kotusu, YARIM bir
# kurulum ilk reboot'ta ayaga kalkmaya calisir.
#
# TEMEL KURAL: YALNIZCA BU KOSUMUN OLUSTURDUKLARI geri alinir.
#
# Bu kural pazarlik konusu degil. Var olan bir kurulumun uzerine yapilan
# denemede korlemesine temizlik, MUSTERI VERISINI (telemetri, olaylar,
# yedekler) silerdi. Her adim once "bu zaten var miydi?" diye sorar; varsa
# geri alma listesine HIC girmez.
E1_ROLLBACK_ACTIONS=()
E1_ROLLBACK_ARMED=0

e1_rollback_arm()    { E1_ROLLBACK_ARMED=1; }
e1_rollback_disarm() { E1_ROLLBACK_ARMED=0; E1_ROLLBACK_ACTIONS=(); }

# Geri alma adimi kaydet. Sirayla degil TERS sirayla calistirilir: en son
# olusturulan once kaldirilir (volume'den once container gibi).
e1_rollback_add() {
  E1_ROLLBACK_ACTIONS+=("$1")
}

e1_rollback_run() {
  [[ "$E1_ROLLBACK_ARMED" != "1" ]] && return 0
  local sayi="${#E1_ROLLBACK_ACTIONS[@]}"
  [[ "$sayi" -eq 0 ]] && return 0
  E1_ROLLBACK_ARMED=0   # geri alma sirasinda tekrar tetiklenmesin

  echo >&2
  e1_warn "Kurulum tamamlanmadi — bu kosumda yapilanlar geri aliniyor (${sayi} adim)."
  e1_hint "Onceden var olan veriler ve ayarlar KORUNUYOR."

  local i
  for (( i=sayi-1; i>=0; i-- )); do
    # Geri alma adiminin kendisi patlarsa DIGERLERI YINE CALISSIN: yarim
    # geri alma, hic geri almamaktan iyidir ama sessiz olmamali.
    if ! eval "${E1_ROLLBACK_ACTIONS[$i]}" >/dev/null 2>&1; then
      e1_warn "  geri alinamadi: ${E1_ROLLBACK_ACTIONS[$i]}"
    fi
  done
  E1_ROLLBACK_ACTIONS=()
  e1_ok "Geri alma tamamlandi. Cihaz kurulum oncesi haline yakin."
}

# Compose yigini icin geri alma kaydi — VERI KORUMASI BURADA.
#
# Mevcut bir kurulumun uzerine tekrar kurulum denendiginde volume'lerde
# MUSTERI VERISI vardir (telemetri, olaylar, yedekler). Yarim kalan kurulumu
# temizlerken onlari silmek, cozmeye calistigi sorundan kat kat pahali bir
# hataya donusur.
#
# Karar tek yerde ve TEST EDILEBILIR: onceden bir sey var miydi?
#   vardi  -> `down`      (container'lar iner, VERI DURUR)
#   yoktu  -> `down -v`   (bu kosumda olustu, iz birakmadan gider)
e1_rollback_register_compose() {
  local dizin="$1"
  if e1_compose_has_existing_state; then
    e1_rollback_add "cd '${dizin}' && docker compose down --remove-orphans"
  else
    e1_rollback_add "cd '${dizin}' && docker compose down -v --remove-orphans"
  fi
}

# "Bu projeye ait volume ya da container ZATEN var mi?"
# Ayri fonksiyon: testte taklit edilebilsin diye.
e1_compose_has_existing_state() {
  docker compose ps -aq 2>/dev/null | grep -q . && return 0
  docker volume ls -q 2>/dev/null | grep -q "^enerjione" && return 0
  return 1
}

e1__on_error() {
  local code="$1" line="$2" cmd="$3"
  [[ "$E1_DYING" == "1" ]] && return 0
  e1_step_done
  e1_rollback_run
  echo >&2
  e1_rule "═" >&2
  printf '  %s%sBEKLENMEYEN HATA%s\n' "${E1_RED}" "${E1_BOLD}" "${E1_RESET}" >&2
  e1_rule "═" >&2
  echo >&2
  printf '  %sSatir      :%s %s\n' "${E1_DIM}" "${E1_RESET}" "$line" >&2
  printf '  %sKomut      :%s %s\n' "${E1_DIM}" "${E1_RESET}" "$cmd" >&2
  printf '  %sCikis kodu :%s %s\n' "${E1_DIM}" "${E1_RESET}" "$code" >&2
  echo >&2
  printf '  Bu ciktinin tamamini teknik destege iletin.\n' >&2
  echo >&2
  exit "$code"
}

e1_enable_error_trap() {
  trap 'e1__on_error "$?" "$LINENO" "$BASH_COMMAND"' ERR
}

# ---------------------------------------------------------------------------
# Sorular — /dev/tty uzerinden (curl | bash ile de calisir)
# ---------------------------------------------------------------------------
# Okuma kaynagi: stdin terminal ise stdin, degilse /dev/tty. Ikisi de yoksa
# (systemd, cron, CI) soru sorulamaz; cagiran varsayilana duser.
_e1_read_src() {
  if [[ -t 0 ]]; then printf '/dev/stdin'; return 0; fi
  # `-r` yetmez: kontrol terminali olmayan ortamlarda (systemd, bazi
  # konteynerler) /dev/tty dosya olarak gorunur ama ACILAMAZ ve bash ham
  # bir "No such device or address" hatasi sizdirir. Gercekten acilabiliyor
  # mu diye deneriz.
  if [[ -r /dev/tty ]] && (: < /dev/tty) 2>/dev/null; then
    printf '/dev/tty'
    return 0
  fi
  return 1
}

# Cevap gelmezse sonsuza kadar beklemeyiz: /dev/tty erisilebilir ama kimsenin
# basinda olmadigi bir ortamda (konsola bagli cron/systemd) deploy asili
# kalirdi. Sure dolunca varsayilan uygulanir ve bu ekrana yazilir.
E1_PROMPT_TIMEOUT="${E1_PROMPT_TIMEOUT:-300}"

# Evet/Hayir — varsayilan HAYIR. "e/evet/y/yes" kabul edilir.
e1_confirm() {
  local prompt="${1:-Devam edilsin mi?}" src ans
  if [[ "${ASSUME_YES:-0}" == "1" ]]; then return 0; fi
  if ! src="$(_e1_read_src)"; then
    e1_warn "Terminal yok — soru sorulamadi, varsayilan kullanildi: HAYIR"
    return 1
  fi
  printf '  %s?%s %s %s[e/H]%s ' "${E1_YELLOW}" "${E1_RESET}" "$prompt" "${E1_DIM}" "${E1_RESET}"
  if ! read -r -t "$E1_PROMPT_TIMEOUT" ans < "$src"; then
    ans=""
    echo
    e1_warn "Sure doldu — varsayilan kullanildi: HAYIR"
  fi
  [[ "$ans" =~ ^([eE]([vV][eE][tT])?|[yY]([eE][sS])?)$ ]]
}

# Evet/Hayir — varsayilan EVET. Guclu bir otomatik tespit varken kullanilir.
e1_confirm_yes() {
  local prompt="${1:-Devam edilsin mi?}" src ans
  if [[ "${ASSUME_YES:-0}" == "1" ]]; then return 0; fi
  if ! src="$(_e1_read_src)"; then
    e1_warn "Terminal yok — soru sorulamadi, varsayilan kullanildi: EVET"
    return 0
  fi
  printf '  %s?%s %s %s[E/h]%s ' "${E1_YELLOW}" "${E1_RESET}" "$prompt" "${E1_DIM}" "${E1_RESET}"
  if ! read -r -t "$E1_PROMPT_TIMEOUT" ans < "$src"; then
    ans=""
    echo
    e1_warn "Sure doldu — varsayilan kullanildi: EVET"
  fi
  [[ ! "$ans" =~ ^([hH]([aA][yY][iI][rR])?|[nN]([oO])?)$ ]]
}

# Serbest metin sorusu — bos birakilirsa varsayilan doner.
#   ad="$(e1_ask 'Cihaz adi' 'e1-grid')"
e1_ask() {
  local prompt="$1" default="${2:-}" src ans
  if [[ "${ASSUME_YES:-0}" == "1" ]] || ! src="$(_e1_read_src)"; then
    printf '%s' "$default"
    return 0
  fi
  if [[ -n "$default" ]]; then
    printf '  %s?%s %s %s[%s]%s ' "${E1_YELLOW}" "${E1_RESET}" "$prompt" "${E1_DIM}" "$default" "${E1_RESET}" >&2
  else
    printf '  %s?%s %s ' "${E1_YELLOW}" "${E1_RESET}" "$prompt" >&2
  fi
  read -r -t "$E1_PROMPT_TIMEOUT" ans < "$src" || ans=""
  printf '%s' "${ans:-$default}"
}

# Bu calistirmada soru sorulabilir mi? Ozet metinlerinde kullanilir.
e1_can_prompt() { [[ "${ASSUME_YES:-0}" != "1" ]] && _e1_read_src >/dev/null; }

# ---------------------------------------------------------------------------
# Yayin (release) kanali — cihazlar dali degil TAG'i takip eder
# ---------------------------------------------------------------------------
# Repo icinde en son semver tag'ini bulur (v2.25.0 gibi). Tag yoksa bos doner
# ve cagiran ana dala duser — henuz hic yayin yapilmamis repolarda calisir.
# `--sort=-v:refname` semver'e gore siralar; alfabetik siralama v2.10.0'i
# v2.9.0'dan ONCE koyardi.
e1_latest_tag() {
  git tag -l 'v[0-9]*' --sort=-v:refname 2>/dev/null | head -n1
}

# Cihazin gececegi ref'i belirle:
#   E1_REF verilmisse aynen kullan (tag, dal veya commit)
#   verilmemisse en son yayin tag'i
#   hic tag yoksa ana dal
e1_target_ref() {
  if [[ -n "${E1_REF:-}" ]]; then printf '%s' "$E1_REF"; return 0; fi
  local t
  t="$(e1_latest_tag)"
  printf '%s' "${t:-$E1_BOOTSTRAP_REF}"
}

# ---------------------------------------------------------------------------
# Kurulum anahtari (token) — private depo + private imajlar icin TEK anahtar
# ---------------------------------------------------------------------------
# Ayni token iki yerde kullanilir: `git clone/fetch` ve `docker login ghcr.io`.
# Kurulumcuya bir kez sorulur, .env'e yazilir, sonraki update'lerde oradan
# okunur.
#
# Sirayla bakilir: ortam degiskeni -> .env -> (sorulabiliyorsa) kullaniciya sor
# Donus: token stdout'a basilir; bulunamazsa bos string.
e1_resolve_token() {
  local env_file="${1:-.env}" tok=""
  tok="${E1_GHCR_TOKEN:-${GHCR_TOKEN:-}}"
  if [[ -z "$tok" && -f "$env_file" ]]; then
    tok="$(sed -n 's/^GHCR_TOKEN=//p' "$env_file" | tail -n1 | tr -d '"'"'"'\r')"
  fi
  printf '%s' "$tok"
}

# Token'i git'e GUVENLI sekilde verir.
#
# Neden URL'e gomulmuyor: `https://user:token@github.com/...` bicimi token'i
# .git/config'e KALICI olarak yazar ve her `git remote -v` ciktisinda gosterir.
# Neden `-c credential.helper='!echo ...'` degil: o bicim token'i komut
# satirina koyar, makinedeki her kullanici `ps` ile gorebilir.
#
# GIT_ASKPASS bir script yolu bekler; token script'e ORTAM DEGISKENI ile
# gecer, yani ne diskte ne process listesinde gorunur.
#   e1_git_auth "$TOKEN" git clone "$URL" "$DIR"
e1_git_auth() {
  local tok="$1"; shift
  local ask rc=0
  if [[ -z "$tok" ]]; then
    # Token yoksa dogrudan calistir (public depo veya zaten yetkili remote).
    "$@"
    return $?
  fi
  ask="$(mktemp)"
  {
    printf '#!/bin/sh\n'
    printf 'case "$1" in\n'
    printf '  Username*) printf %%s "x-access-token" ;;\n'
    printf '  *)         printf %%s "$E1_GIT_TOKEN" ;;\n'
    printf 'esac\n'
  } > "$ask"
  chmod 700 "$ask"
  # GIT_TERMINAL_PROMPT=0: token gecersizse sessizce takilip beklemesin,
  # hemen hata versin (saha kurulumunda "donmus" gorunumunun sebebi budur).
  E1_GIT_TOKEN="$tok" GIT_ASKPASS="$ask" GIT_TERMINAL_PROMPT=0 "$@" || rc=$?
  rm -f "$ask"
  return $rc
}

# ---------------------------------------------------------------------------
# GHCR — private imajlar icin login
# ---------------------------------------------------------------------------
# Repo private oldugu icin paketler de private; cihaz salt-okunur bir token
# ile ghcr.io'ya login olmadan imaj CEKEMEZ. Token .env'de GHCR_TOKEN.
# Zaten login ise (~/.docker/config.json) tekrar denemez.
# Donus: 0 = imaj cekilebilir, 1 = cekilemez (cagiran build'e duser).
e1_ghcr_login() {
  local env_file="${1:-.env}" user="" token="" registry="ghcr.io"
  [[ -n "${GHCR_TOKEN:-}" ]] && token="$GHCR_TOKEN"
  [[ -n "${GHCR_USERNAME:-}" ]] && user="$GHCR_USERNAME"
  if [[ -z "$token" && -f "$env_file" ]]; then
    token="$(sed -n 's/^GHCR_TOKEN=//p' "$env_file" | tail -n1 | tr -d '"'"'"'\r')"
    user="$(sed -n 's/^GHCR_USERNAME=//p' "$env_file" | tail -n1 | tr -d '"'"'"'\r')"
  fi
  if [[ -z "$token" ]]; then
    return 1
  fi
  # Kullanici adi bos birakilabilir: GHCR token dogrulamasinda kullanici adi
  # onemsizdir, token belirleyicidir. Yine de bos gecilemedigi icin doldururuz.
  [[ -z "$user" ]] && user="x-access-token"
  if printf '%s' "$token" | docker login "$registry" -u "$user" --password-stdin >/dev/null 2>&1; then
    return 0
  fi
  e1_warn "ghcr.io girisi basarisiz — token gecersiz veya suresi dolmus olabilir."
  return 1
}

# ---------------------------------------------------------------------------
# Ortam tespiti
# ---------------------------------------------------------------------------
# Makinede WiFi arayuzu var mi? Appliance (mini PC) modunu otomatik acmak
# icin: VPS'lerde WiFi karti YOKTUR, saha mini PC'lerinde vardir. sysfs
# uzerinden bakariz — nmcli/iw bu asamada henuz kurulu olmayabilir.
e1_has_wifi() {
  local dir
  for dir in /sys/class/net/*/wireless; do
    [[ -d "$dir" ]] && return 0
  done
  # Bazi surucular 'wireless' yerine 'phy80211' symlink'i birakir.
  for dir in /sys/class/net/*/phy80211; do
    [[ -e "$dir" ]] && return 0
  done
  return 1
}

# Appliance modu bu makinede kurulu mu? (setup-appliance.sh calistirilmis mi)
e1_appliance_installed() {
  [[ -f /etc/systemd/system/e1-netd.path ]] || [[ -d /var/lib/e1-grid/net ]]
}

# Urun surumu — tek kanonik kaynak apps/frontend-web/package.json "version".
# Branch adi kullanici icin anlamsiz; ekranlarda numarali surum gosterilir.
# Argüman: repo koku (verilmezse calisilan dizin).
# Surumun TEK KAYNAGI kok dizindeki VERSION dosyasidir; release CI hem bu
# dosyayi hem git tag'ini hem de package.json'i ayni olmaya zorlar.
# package.json fallback'i eski kurulumlarda (VERSION dosyasi yokken klonlanmis)
# calismaya devam etsin diye duruyor.
e1_version() {
  local root="${1:-.}" vf="${1:-.}/VERSION" pkg="${1:-.}/apps/frontend-web/package.json" v=""
  if [[ -f "$vf" ]]; then
    v="$(tr -d ' \t\r\n' < "$vf")"
    [[ -n "$v" ]] && { echo "$v"; return 0; }
  fi
  [[ -f "$pkg" ]] || { echo "?"; return 0; }
  v="$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$pkg" | head -1)"
  echo "${v:-?}"
  unset root
}

# Root kontrolu — install/update/uninstall icin zorunlu.
e1_require_root() {
  if [[ $EUID -ne 0 ]]; then
    e1_die "Bu komut yonetici (root) yetkisi ile calismali:\n\n      sudo bash $0 $*"
  fi
}

# Cagiran kullanici (SUDO_USER) — sahiplenme icin. Yoksa root kalir.
e1_target_user() {
  local u="${INSTALL_USER:-${SUDO_USER:-}}"
  if [[ -n "$u" ]] && id -u "$u" >/dev/null 2>&1; then
    echo "$u"
  fi
}
# ---------------------------------------------------------------------------
# .git SAHIPLIGINI CALISMA AGACIYLA HIZALA
# ---------------------------------------------------------------------------
# YASANAN ARIZA
# -------------
# `install.sh` ve `update.sh` root olarak calisir (`e1_require_root`) ve git'i
# de root olarak cagirir. Root her dosyaya yazabildigi icin bu komutlar HATA
# VERMEZ — sessizce `.git` icinde root'a ait nesneler birakirlar.
#
# Fatura sonra kesilir: depoyu normal kullanici olarak kullanmak isteyen
# (ya da bir CI/ajan) `git fetch` dedigi anda
#
#     error: insufficient permission for adding an object to repository
#     database .git/objects
#     fatal: failed to write object
#
# ile duser. Olcum: tek bir saha kurulumunda 834 root sahipli dosya birikmisti.
# Kendiliginden duzelmez ve her `sudo bash update.sh` biraz daha buyutur.
#
# NEDEN "CALISMA AGACININ SAHIBI", SUDO_USER DEGIL
# ------------------------------------------------
# `e1_chown_target` cagiran kullaniciyi (SUDO_USER) hedefler; burada dogru
# olcut o degil. Depo root tarafindan kurulduysa `.git` de root'un kalmali —
# baska bir kullanici `sudo -u` ile bir kez update calistirdi diye sahiplik
# el degistirmemeli. Bu yuzden hedef, calisma agaci dizininin KENDI sahibi:
# ne ise o korunur, yalnizca tutarsizlik giderilir.
#
# Idempotent: sahiplik zaten dogruysa hicbir sey degismez.
e1_git_sahipligini_hizala() {
  local dir="${1:-.}" sahip
  [[ -d "$dir/.git" ]] || return 0
  sahip="$(stat -c '%u:%g' "$dir" 2>/dev/null || echo '')"
  [[ -n "$sahip" ]] || return 0
  chown -R "$sahip" "$dir/.git" 2>/dev/null || true
}

e1_chown_target() {
  local u
  u="$(e1_target_user)"
  if [[ -n "$u" ]]; then
    chown "$(id -u "$u"):$(id -g "$u")" "$@" 2>/dev/null || true
  fi
}
e1_chown_target_recursive() {
  local u
  u="$(e1_target_user)"
  if [[ -n "$u" ]]; then
    chown -R "$(id -u "$u"):$(id -g "$u")" "$@" 2>/dev/null || true
  fi
}

# Bir .env secret'ini YALNIZCA eksik/bos/placeholder ise doldurur.
#
# NEDEN VAR: `.env.example` bazi secret'lari BOS birakiyor ve bunlarin
# uretilmesi install.sh'a bagli. Listede unutulan bir degisken (FTP_PASSWORD
# boyleydi) sahada su zinciri uretir: container acilista "deger bos" deyip
# oluyor -> `restart: unless-stopped` onu sonsuza kadar yeniden baslatiyor ->
# hicbir kurulum ciktisinda gorunmuyor. Bu fonksiyon hem install.sh'ta hem
# update.sh'ta cagriliyor; ikincisi ZATEN KURULU cihazlari da onarir
# (update.sh'in baska bir .env onarim adimi yok).
#
# DOLU BIR DEGERE ASLA DOKUNMAZ: mevcut kurulumun parolasini degistirmek
# (or. POSTGRES_PASSWORD) veri kaybina yakin bir hatadir.
#
# Placeholder kaliplari install.sh'taki `_ensure_env_var` ile AYNI tutulmali.
#
# Kullanim:  e1_env_ensure_secret FTP_PASSWORD 16 [.env yolu]
# Donus:     0 = yeni deger yazildi, 1 = dokunulmadi/yazilamadi
e1_env_ensure_secret() {
  local key="$1"
  local len="${2:-24}"
  local env_file="${3:-.env}"
  local cur="" val=""

  [[ -f "$env_file" ]] || return 1

  if grep -qE "^${key}=" "$env_file"; then
    cur="$(grep -E "^${key}=" "$env_file" | head -1 | cut -d= -f2-)"
    case "$cur" in
      ""|please-change-me*|change-me*|change-this*) ;;
      *) return 1 ;;  # operator/onceki kurulum doldurmus — KORU
    esac
  fi

  # Yalnizca harf+rakam: parola sahada cihazin kendi ekranindan ELLE
  # giriliyor; `/+=` gibi karakterler hem yazimi zorlastiriyor hem de bazi
  # gomulu FTP istemcilerinde sorun cikariyor.
  val="$(openssl rand -base64 48 2>/dev/null | tr -dc 'A-Za-z0-9' | cut -c1-"$len")"
  [[ ${#val} -eq $len ]] || return 1

  if grep -qE "^${key}=" "$env_file"; then
    sed -i "s|^${key}=.*|${key}=${val}|" "$env_file"
  else
    printf '%s=%s\n' "$key" "$val" >> "$env_file"
  fi
  return 0
}

# Yedege VERISI alinmayacak tablolar (schema yine dump'a girer).
#
# Backend'deki `backup_service.EXCLUDED_DATA_TABLES` + `_EXCLUDED_DATA_PATTERNS`
# ile AYNI olmali; senkron kalmasini `test_pre_update_backup_excludes.py`
# dogruluyor. Liste burada ELLE duruyor cunku paket (.deb) kurulumunda cihazda
# backend KAYNAGI YOK — uygulama kodu imajlarin icinde.
#
# TEK KAYNAK: hem `update.sh` (guncelleme oncesi otomatik yedek) hem
# `enerjione-grid backup` (elle yedek) bunu kullanir. Ayri ayri tanimlandiginda
# ikincisi tam olarak bu listeden yoksundu ve 90 gunluk historian'in TAMAMINI
# dump ediyordu.
#
# Hypertable satirlari asil tabloda DEGIL `_timescaledb_internal` chunk'larinda
# durur; tablo adiyla haric tutmak YETMEZ — son iki kalip onun icin.
E1_DUMP_EXCLUDE=(
  telemetry
  outbox_events
  processed_messages
  gateway_ingest_batches
  backup_jobs
  backup_schedule
  telemetry_history
  telemetry_history_1m
  telemetry_history_1h
  '_timescaledb_internal._hyper_*'
  '_timescaledb_internal._materialized_hypertable_*'
)

# Yukaridaki listeyi pg_dump argumanlarina cevirip verilen dizi degiskenine
# ekler.  Kullanim:  e1_dump_exclude_into DUMP_ARGS
e1_dump_exclude_into() {
  local -n _hedef="$1"
  local _t
  for _t in "${E1_DUMP_EXCLUDE[@]}"; do
    _hedef+=(--exclude-table-data "$_t")
  done
}

# ===========================================================================
# UPDATE BACKUP GATE — guncelleme oncesi yedek ZORUNLU
# ===========================================================================
#
# KAPATILAN ARIZA
# ---------------
# Yedek adimi FAIL-OPEN'di: uc ayri yolda yedek alinamiyor ama guncelleme
# YINE DE devam ediyordu.
#
#   * diskte 2048 MB'tan az yer   -> "yedek ATLANDI",      update devam
#   * pg_dump basarisiz           -> "update yine de devam ediyor"
#   * Postgres ayakta degil       -> "yedek atlandi",      update devam
#
# Ustelik alinan dosya HIC DOGRULANMIYORDU: `pg_dump` cikis kodu 0 ise yeterli
# sayiliyordu. Bos, kirpilmis ya da okunamayan bir arsiv "yedek" olarak
# raporlanabiliyordu — yani geri donus noktasi oldugu SANILAN bir dosya.
#
# Sonuc: veri tasiyan bir saha cihazi, elinde GERI DONULEBILIR HICBIR NOKTA
# OLMADAN migration'a giriyordu. Guncelleme yarida kalirsa geri donecek yer
# yok.
#
# YENI SOZLESME (fail-closed)
# ---------------------------
#   yedek ALINDI ve DOGRULANDI  ->  guncelleme baslayabilir
#   digerlerinin HEPSI          ->  guncelleme BASLAMAZ (cikis 42)
#
# "Guncelleme baslamadi" iddiasi ancak kapi TUM mutasyonlardan once kosarsa
# dogru olur; bu yuzden `update.sh` icinde `.env` onarimi dahil her yazma
# isleminden ONCE cagriliyor.

#: Yedek kapisi cikis kodu. Genel hatalardan (1) AYIRT EDILEBILIR olmasi
#: onemli: saha operatoru ve otomasyon "guncelleme baslamadi" durumunu
#: digerlerinden ayirabilmeli.
E1_EXIT_BACKUP_GATE=42

#: Yedek disinda tutulacak asgari bos alan (MB). Diski son bayta kadar
#: doldurmak CALISAN sistemi bozar; postgres-data ayni dosya sisteminde.
E1_BACKUP_RESERVE_MB="${E1_BACKUP_RESERVE_MB:-1024}"

#: Tahmin edilemedigi durumda kullanilacak asgari gereksinim (MB).
E1_BACKUP_MIN_MB="${E1_BACKUP_MIN_MB:-512}"

# Bu kurulum VERI TASIYOR mu?
#
# Uc durum var ve UCU DE FARKLI davranmali:
#   0 = veri tasiyor, Postgres AYAKTA        -> yedek ZORUNLU
#   1 = veri tasiyor ama Postgres KAPALI     -> yedek alinamaz, DUR
#   2 = veri yok (ilk kurulum)               -> yedek gerekmiyor
#
# Ikinci durumu ucuncuden ayirmak sart: "Postgres kapali" gorup yedegi
# atlamak, tam da dolu bir veritabaniyla migration'a girmek demekti.
e1_kurulum_veri_durumu() {
  if docker compose ps postgres --status running --quiet 2>/dev/null | grep -q .; then
    return 0
  fi
  # Postgres kapali. Veri hacmi VAR MI? Varsa bu bir ilk kurulum DEGILDIR.
  if docker volume ls --format '{{.Name}}' 2>/dev/null | grep -qE '(^|_)postgres-data$'; then
    return 1
  fi
  return 2
}

# Yedek icin gereken bos alani TAHMIN eder (MB, stdout).
#
# Sabit 2048 MB yerine veritabaninin GERCEK boyutundan turetiliyor: kucuk bir
# kurulumda 2 GB gereksiz yere engel, buyuk bir kurulumda ise YETERSIZ olurdu.
#
# Tahmin `pg_database_size` eksi yedek DISI tablolar uzerinden yapilir —
# `--exclude-table-data` ile atilan historian arsivi dump'a girmiyor.
# Custom format ustelik SIKISTIRILMIS oldugu icin bu tahmin fazlasiyla
# guvenli taraftadir (gercek dump daha kucuk cikar).
#
# Sorgu basarisiz olursa tahmin yapilmaz ve asgari degere dusulur; sessizce
# "yer var" demeyiz.
e1_backup_gerekli_mb() {
  local pg_user="$1" pg_db="$2"
  local bayt kalan_mb

  bayt="$(docker compose exec -T postgres psql -U "$pg_user" -d "$pg_db" -tAc "
    SELECT GREATEST(
      pg_database_size(current_database())
      - COALESCE((
          SELECT SUM(pg_total_relation_size(c.oid))
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
          WHERE n.nspname IN ('public','_timescaledb_internal')
            AND (c.relname LIKE 'telemetry%'
                 OR c.relname LIKE '_hyper_%'
                 OR c.relname LIKE '_materialized_hypertable_%'
                 OR c.relname IN ('outbox_events','processed_messages',
                                  'gateway_ingest_batches','backup_jobs',
                                  'backup_schedule'))
        ), 0), 0)" 2>/dev/null | tr -d '[:space:]')"

  if [[ ! "$bayt" =~ ^[0-9]+$ ]]; then
    # Olcemedik. Tahmin uydurmak yerine asgari degeri kullan.
    printf '%s' "$(( E1_BACKUP_MIN_MB + E1_BACKUP_RESERVE_MB ))"
    return 0
  fi

  kalan_mb=$(( bayt / 1048576 ))
  (( kalan_mb < E1_BACKUP_MIN_MB )) && kalan_mb="$E1_BACKUP_MIN_MB"
  printf '%s' "$(( kalan_mb + E1_BACKUP_RESERVE_MB ))"
}

# Uretilen arsivi DOGRULAR. `pg_dump` cikis kodu TEK BASINA yeterli degil.
#
# Sirayla: dosya var mi -> duz dosya mi -> boyut > 0 -> `pg_restore --list`
# arsivi okuyabiliyor mu. Sonuncusu belirleyici: kirpilmis ya da bozuk bir
# arsiv burada duser. Ayni dogrulayici mantik backend'in Safe Restore
# akisinda da var (`arsiv_on_dogrula`); burada kabuk tarafinda ayni sozlesme
# uygulaniyor cunku updater backend'in ayakta olmasina BAGLI OLMAMALI.
e1_backup_dogrula() {
  local dosya="$1"

  [[ -e "$dosya" ]]  || { printf 'dosya olusmadi'; return 1; }
  [[ -f "$dosya" ]]  || { printf 'duz dosya degil'; return 1; }
  [[ -s "$dosya" ]]  || { printf 'dosya bos (0 bayt)'; return 1; }

  # Arsiv okunabiliyor mu? pg_restore postgres container'inin icinde;
  # istemci/sunucu surumu tanim geregi ayni.
  #
  # `/dev/stdin` ARGUMAN OLARAK VERILMEZ. pg_restore bir DOSYA argumani
  # aldiginda arsivde geri-sarma (seek) yapar; `docker compose exec -T`
  # stdin'i BORU olarak ilettigi icin container icindeki /dev/stdin seek
  # edilemez ve pg_restore "did not find magic string in file header" der.
  # Arsiv SAGLAMDIR, okuma bicimi yanlistir. Argumansiz cagride pg_restore
  # akisi sirayla okur ve boru ile sorunsuz calisir.
  #
  # SAHADA OLCULDU (2026-08-17, 2.98.0 -> 2.99.0): kapinin ILK gercek
  # kosumuydu ve kendi urettigi saglam yedegi dogrulayamadigi icin
  # guncellemeyi TAMAMEN blokladi.
  if ! docker compose exec -T postgres pg_restore --list \
         < "$dosya" >/dev/null 2>&1; then
    printf 'arsiv okunamiyor (pg_restore --list basarisiz)'
    return 1
  fi
  return 0
}

# Guncelleme oncesi yedegi ALIR, DOGRULAR ve sonucu raporlar.
#
# Basarisizlikta 1 doner; cagiran taraf `e1_die` ile 42 kodunda durur.
# Basarida yedek yolunu `E1_BACKUP_GATE_FILE` degiskeninde birakir.
#
# Kullanim: e1_pre_update_backup_gate <kaynak_surum> <hedef_surum>
e1_pre_update_backup_gate() {
  local kaynak="${1:-bilinmiyor}" hedef="${2:-bilinmiyor}"
  local keep pg_user pg_db ts tmp final gerekli_mb bos_mb sebep t0 sure
  local boyut sha

  E1_BACKUP_GATE_FILE=""

  keep="${E1_PRE_UPDATE_KEEP:-3}"
  [[ "$keep" =~ ^[0-9]+$ ]] && [[ "$keep" -ge 1 ]] || keep=3

  e1_kurulum_veri_durumu
  case "$?" in
    2)
      # Gercek ilk kurulum: veritabani hic olusmamis. Yedeklenecek bir sey yok.
      e1_info "Veritabani henuz olusturulmamis — guncelleme oncesi yedek gerekmiyor."
      printf 'pre_update_backup_skipped reason=fresh_install source=%s target=%s\n' \
        "$kaynak" "$hedef" >&2
      return 0
      ;;
    1)
      e1_err "Veritabani hacmi mevcut ama Postgres AYAKTA DEGIL — yedek alinamaz."
      printf 'pre_update_backup_failed reason=postgres_unavailable source=%s target=%s\n' \
        "$kaynak" "$hedef" >&2
      return 1
      ;;
  esac

  mkdir -p backups || {
    e1_err "backups/ dizini olusturulamadi."
    printf 'pre_update_backup_failed reason=backup_dir_unwritable source=%s target=%s\n' \
      "$kaynak" "$hedef" >&2
    return 1
  }
  if [[ ! -w backups ]]; then
    e1_err "backups/ dizinine yazilamiyor."
    printf 'pre_update_backup_failed reason=backup_dir_unwritable source=%s target=%s\n' \
      "$kaynak" "$hedef" >&2
    return 1
  fi

  # Once birikmis eski dosyalari temizle (sahadaki GB'larca .sql.gz dahil).
  # Dump ONCESI budama, yer acmanin da en ucuz yolu.
  e1_prune_pre_update_backups "$keep"

  pg_user="$(e1_env_get POSTGRES_USER)"; pg_user="${pg_user:-enerjione_grid}"
  pg_db="$(e1_env_get POSTGRES_DB)";     pg_db="${pg_db:-enerjione_grid}"

  # ---- disk on kontrolu -------------------------------------------------
  gerekli_mb="$(e1_backup_gerekli_mb "$pg_user" "$pg_db")"
  bos_mb="$(df -Pm . 2>/dev/null | awk 'NR==2 {print $4}')"
  bos_mb="${bos_mb:-0}"
  e1_info "Yedek icin gereken: ${gerekli_mb} MB — diskte bos: ${bos_mb} MB"

  if [[ "$bos_mb" -lt "$gerekli_mb" ]]; then
    e1_err "Diskte yeterli yer yok (gereken ${gerekli_mb} MB, bos ${bos_mb} MB)."
    e1_hint "Yer acin (docker system prune, eski yedekler) ve tekrar deneyin."
    printf 'pre_update_backup_failed reason=insufficient_disk required_mb=%s free_mb=%s source=%s target=%s\n' \
      "$gerekli_mb" "$bos_mb" "$kaynak" "$hedef" >&2
    return 1
  fi

  # ---- dump -------------------------------------------------------------
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  final="backups/auto-pre-update-$(e1_surum_sanitize "$kaynak")_to_$(e1_surum_sanitize "$hedef")-${ts}.dump"

  # GECICI ADA YAZ, SONRA ATOMIK TASI.
  #
  # Eskiden dogrudan nihai ada yaziliyordu: `pg_dump` yarida oldurulurse
  # (SIGKILL, elektrik) diskte NIHAI ADI TASIYAN yarim bir dosya kaliyordu.
  # Bir sonraki operatorun "yedegim var" diye guvenecegi dosya tam da o.
  tmp="backups/.backup.tmp.$$"
  rm -f "$tmp"

  local dump_args=(-U "$pg_user" -d "$pg_db" -F c --no-owner --no-acl)
  e1_dump_exclude_into dump_args

  local errlog; errlog="$(mktemp)"
  t0="$(date +%s)"
  printf 'pre_update_backup_started source=%s target=%s required_mb=%s free_mb=%s\n' \
    "$kaynak" "$hedef" "$gerekli_mb" "$bos_mb" >&2

  # pg_dump postgres container'inin ICINDEN kosuyor: istemci ve sunucu surumu
  # tanim geregi ayni, surum uyusmazligi olamaz (backend tarafindaki
  # `istemci_sunucu_surum_uyumu` kontrolunun karsiligi burada gereksiz).
  if ! docker compose exec -T postgres pg_dump "${dump_args[@]}" \
        > "$tmp" 2>"$errlog"; then
    e1_err "Veritabani yedegi alinamadi (pg_dump basarisiz)."
    if [[ -s "$errlog" ]]; then
      tail -n 5 "$errlog" | while IFS= read -r _l; do e1_hint "$_l"; done
    fi
    rm -f "$tmp" "$errlog"
    printf 'pre_update_backup_failed reason=pg_dump_failed source=%s target=%s\n' \
      "$kaynak" "$hedef" >&2
    return 1
  fi
  rm -f "$errlog"

  # ---- dogrulama --------------------------------------------------------
  if ! sebep="$(e1_backup_dogrula "$tmp")"; then
    e1_err "Yedek dogrulanamadi: ${sebep}"
    rm -f "$tmp"
    printf 'pre_update_backup_validation_failed reason=%s source=%s target=%s\n' \
      "${sebep// /_}" "$kaynak" "$hedef" >&2
    return 1
  fi

  # ---- atomik yayina alma ----------------------------------------------
  if ! mv -f "$tmp" "$final"; then
    e1_err "Yedek dosyasi yerine tasinamadi."
    rm -f "$tmp"
    printf 'pre_update_backup_failed reason=rename_failed source=%s target=%s\n' \
      "$kaynak" "$hedef" >&2
    return 1
  fi

  boyut="$(stat -c %s "$final" 2>/dev/null || echo 0)"
  sha="$(sha256sum "$final" 2>/dev/null | awk '{print $1}')"
  sha="${sha:-hesaplanamadi}"
  sure=$(( $(date +%s) - t0 ))

  e1_chown_target "$final"
  e1_ok "Yedek: ${final} ($(( boyut / 1048576 )) MB)"
  e1_hint "SHA256: ${sha}"
  e1_hint "Geri yukleme: Muhendislik > Sistem > Yedekler > Yedek Yukle"

  # Yenisi eklendi; tavani yeniden uygula. Budama YALNIZCA basarili yedekten
  # sonra kosar, boylece elde hic yedek kalmayan bir an olusmaz.
  e1_prune_pre_update_backups "$keep"

  printf 'pre_update_backup_succeeded path=%s size=%s sha256=%s source=%s target=%s duration_sec=%s\n' \
    "$final" "$boyut" "$sha" "$kaynak" "$hedef" "$sure" >&2

  E1_BACKUP_GATE_FILE="$final"
  return 0
}

# Surum metnini dosya adinda GUVENLE kullanilabilir hale getirir.
# Yalnizca [A-Za-z0-9._-] birakilir: kabuk enjeksiyonu ve dizin gecisi
# (`../`) icin yuzey birakmaz. Bos kalirsa "bilinmiyor".
e1_surum_sanitize() {
  local s="${1:-}"
  s="${s//[^A-Za-z0-9._-]/_}"
  s="${s#.}"           # basta nokta -> gizli dosya olmasin
  printf '%s' "${s:-bilinmiyor}"
}

# Update oncesi otomatik DB yedeklerini en yeni <keep> tanesi kalacak sekilde
# budar. `auto-pre-update-*` kaliba uyan HER dosya kapsanir; eski surumlerin
# biraktigi `.sql.gz` yigini da bu sayede temizlenir.
#
# NEDEN VAR: bu dosyalar hicbir zaman silinmiyordu. `./backups` compose proje
# dizinindedir, BACKUP_DIR volume'u DEGIL; ne disk_guard, ne apply_retention,
# ne de UI onlari gorur. Birkac guncelleme sonra cihazin diski doluyor ve
# postgres-data ayni dosya sisteminde oldugu icin Postgres yazamaz hale
# geliyordu.
#
# Kullanim: e1_prune_pre_update_backups <keep> [dizin]
e1_prune_pre_update_backups() {
  local keep="${1:-3}"
  local dir="${2:-backups}"
  local f

  [[ "$keep" =~ ^[0-9]+$ ]] || keep=3
  [[ -d "$dir" ]] || return 0

  while IFS= read -r f; do
    [[ -n "$f" ]] || continue
    if rm -f -- "$f"; then
      e1_info "Eski yedek silindi: $(basename -- "$f")"
    fi
  done < <(
    find "$dir" -maxdepth 1 -type f -name 'auto-pre-update-*' \
         -printf '%T@\t%p\n' 2>/dev/null \
      | sort -rn | tail -n +$((keep + 1)) | cut -f2-
  )
}

# Bir .env degiskeninin degerini okur (yoksa bos doner).
e1_env_get() {
  local key="$1" env_file="${2:-.env}"
  [[ -f "$env_file" ]] || return 0
  grep -E "^${key}=" "$env_file" 2>/dev/null | head -1 | cut -d= -f2- || true
}

# VDS IP'sini bul — once `hostname -I` (local interface, instant, offline-safe),
# yoksa veya invalid ise public IP icin curl ifconfig.me. Bash `||` zincirinde
# bos string'in exit=0 dondurmesi tuzak; her adimi explicit kontrol et.
e1_detect_ip() {
  local ip=""
  ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  if [[ -n "$ip" ]] && [[ "$ip" != "127.0.0.1" ]] && [[ "$ip" != "<vds-ip>" ]]; then
    echo "$ip"
    return 0
  fi
  ip="$(curl -fsS --max-time 3 ifconfig.me 2>/dev/null || true)"
  if [[ -n "$ip" ]]; then
    echo "$ip"
    return 0
  fi
  echo "<vds-ip>"
}

# ---------------------------------------------------------------------------
# NATS TLS — acik/kapali her iki durumda da TUTARLI bir yapilandirma uretir.
#
# NEDEN BU FONKSIYON VAR: TLS'in calismasi UC seyin AYNI ANDA dogru olmasina
# bagli —
#   (1) infra/nats/certs/ altinda sunucu sertifikasi + CA,
#   (2) istemcilerin okudugu NATS_CA_FILE,
#   (3) NATS_URL* semasinin `tls://` olmasi.
# Ucunden biri eksik kalirsa ariza SESSIZ olur: NATS'in kendi healthcheck'i
# TLS'siz izleme portunu (8222) prob ettigi icin container "healthy" gorunur,
# ama backend el sikisamaz. Eskiden bu /health'i 503'e dusurup frontend-web'in
# `depends_on: service_healthy` zincirini kilitliyordu — yani ARAYUZ HIC
# ACILMIYORDU. /health artik NATS'i kritik saymiyor, ama yine de yarim
# yapilandirmaya hic izin vermemek gerekiyor: bu fonksiyon ya tam tutarli bir
# durum birakir ya da acik bir hata ile durur.
#
# Kullanim:
#   e1_nats_tls_prepare [.env yolu]        # $E1_NATS_TLS_BLOCK'u doldurur
#   e1_nats_conf_render_tls <conf yolu>    # {{NATS_TLS_BLOCK}} isaretini yazar
# ---------------------------------------------------------------------------

E1_NATS_TLS_BLOCK=""
# `prepare` gercekten kostu mu? Fonksiyon bir boru hattinda cagrilirsa
# (`e1_nats_tls_prepare | tee ...`) subshell'de calisir ve global degisken
# CAGIRANA DONMEZ; o durumda blok bos kalir ve render sessizce TLS'SIZ bir
# conf uretir. Sessiz yanlis yapilandirma tam da bu dosyanin onlemeye
# calistigi sey — bu yuzden render bayragi kontrol eder ve bos blokla devam
# etmez. Bayrak ayni subshell mantigiyla kaybolur, yani koruma dogru tarafta
# hata verir.
E1_NATS_TLS_PREPARED=""

# TLS acilinca/kapaninca semasi degismesi gereken .env anahtarlari.
# docker-compose.yml sirasiyla NATS_URL_BACKEND / NATS_URL_WORKER'i, yoksa
# NATS_URL'i kullanir; uc anahtar da hizada tutulmali.
e1_nats_url_keys() { printf '%s\n' "NATS_URL" "NATS_URL_BACKEND" "NATS_URL_WORKER"; }

# .env'deki bir NATS URL'inin semasini degistirir (deger yoksa dokunmaz).
# Parolayi ICERDIGI icin deger asla log'a basilmaz; yalnizca anahtar adi.
_e1_nats_url_scheme() {
  local key="$1" want="$2" env_file="$3" cur=""
  cur="$(e1_env_get "$key" "$env_file")"
  [[ -n "$cur" ]] || return 0
  case "$want" in
    tls) [[ "$cur" == nats://* ]] || return 0
         sed -i "s|^${key}=nats://|${key}=tls://|" "$env_file"
         e1_info "${key} semasi 'tls://' olarak hizalandi." ;;
    plain) [[ "$cur" == tls://* ]] || return 0
         sed -i "s|^${key}=tls://|${key}=nats://|" "$env_file"
         e1_warn "${key} 'tls://' idi; TLS kapali oldugu icin 'nats://' yapildi." ;;
  esac
}

e1_nats_tls_prepare() {
  local env_file="${1:-.env}"
  local enabled="" ca="" key=""
  enabled="$(e1_env_get NATS_TLS_ENABLED "$env_file")"
  enabled="$(printf '%s' "$enabled" | tr '[:upper:]' '[:lower:]')"

  if [[ "$enabled" != "true" ]]; then
    # KAPALI. Onceki bir denemeden kalan `tls://` semalari sistemi ayaga
    # kaldirmaz; sessizce birakmak yerine geri aliyoruz ki "TLS'i kapattim
    # ama hala calismiyor" durumu olusmasin.
    for key in $(e1_nats_url_keys); do
      _e1_nats_url_scheme "$key" plain "$env_file"
    done
    E1_NATS_TLS_BLOCK="# TLS kapali (NATS_TLS_ENABLED=true ile acilir)"
    E1_NATS_TLS_PREPARED=1
    return 0
  fi

  # ACIK. Sertifika yoksa devam etmek NATS'i hic ayaga kaldirmaz.
  if [[ ! -f infra/nats/certs/server.crt || ! -f infra/nats/certs/server.key ]]; then
    e1_warn "NATS_TLS_ENABLED=true ama sunucu sertifikasi yok (infra/nats/certs/)."
    e1_warn "Once calistirin: sudo bash infra/scripts/linux/nats-tls-setup.sh"
    e1_warn "TLS'e ihtiyaciniz yoksa .env'de NATS_TLS_ENABLED=false yapin."
    e1_die "Sertifikasiz TLS ile NATS ayaga kalkmaz."
  fi

  # CA: istemciler bu dosyayi CONTAINER ICINDEN okur. Dizin compose'da
  # `./infra/nats/certs:/etc/nats/certs:ro` olarak NATS'a BAGLANAN her
  # servise mount edilir (x-nats-certs anchor'i).
  if [[ ! -f infra/nats/certs/ca.crt ]]; then
    e1_warn "infra/nats/certs/ca.crt yok — istemciler sunucuyu dogrulayamaz."
    e1_die "nats-tls-setup.sh'i calistirip CA uretin."
  fi
  ca="$(e1_env_get NATS_CA_FILE "$env_file")"
  if [[ -z "$ca" ]]; then
    if grep -qE '^NATS_CA_FILE=' "$env_file" 2>/dev/null; then
      sed -i "s|^NATS_CA_FILE=.*|NATS_CA_FILE=/etc/nats/certs/ca.crt|" "$env_file"
    else
      printf 'NATS_CA_FILE=%s\n' "/etc/nats/certs/ca.crt" >> "$env_file"
    fi
    e1_info "NATS_CA_FILE bos idi; /etc/nats/certs/ca.crt olarak dolduruldu."
  elif [[ "$ca" != /etc/nats/certs/* ]]; then
    # Container icindeki mount noktasi disinda bir yol = istemcide bulunamaz.
    e1_warn "NATS_CA_FILE='${ca}' container icinde bulunmayabilir."
    e1_warn "Beklenen yol: /etc/nats/certs/ca.crt (compose bu dizini mount eder)."
  fi

  # URL semalari: `nats://` ile TLS dinleyen bir sunucuya baglanilmaz.
  for key in $(e1_nats_url_keys); do
    _e1_nats_url_scheme "$key" tls "$env_file"
  done

  E1_NATS_TLS_BLOCK='tls {
  cert_file: "/etc/nats/certs/server.crt"
  key_file:  "/etc/nats/certs/server.key"
  timeout:   5
}'
  E1_NATS_TLS_PREPARED=1
  e1_ok "NATS TLS etkin (sertifika + CA + URL semalari hizali)."
  return 0
}

# {{NATS_TLS_BLOCK}} yer tutucusunu $E1_NATS_TLS_BLOCK ile degistirir.
#
# NEDEN sed DEGIL: blok cok satirli ve `/` iceriyor.
# NEDEN python DEGIL: bash'in `${var//ara/koy}` genislemesi tam bu isi yapar
# ve degistirilen metin LITERAL'dir (regex yok), yani `/`, `&`, `\` ve satir
# sonlari ozel anlam tasimaz. Kurulum betigine ayri bir python3 bagimliligi
# eklemenin gerekcesi kalmiyordu; ustelik python3 her ortamda PATH'te degil.
e1_nats_conf_render_tls() {
  local conf="${1:-infra/nats/nats-server.conf}"
  if [[ -z "$E1_NATS_TLS_PREPARED" ]]; then
    e1_die "e1_nats_conf_render_tls: once e1_nats_tls_prepare cagrilmali (boru hattinda mi cagrildi?)."
  fi
  [[ -f "$conf" ]] || e1_die "e1_nats_conf_render_tls: dosya yok: $conf"

  local icerik
  icerik="$(<"$conf")"
  printf '%s\n' "${icerik//\{\{NATS_TLS_BLOCK\}\}/$E1_NATS_TLS_BLOCK}" > "$conf"

  # SONUCU DOGRULA. 2026-08-01 arizasinin cekirdegi tam buydu: render adimi
  # patladi, `{{NATS_TLS_BLOCK}}` yer tutucusu dosyada HAM kaldi ve NATS o
  # dosyayi ayristiramayip crash-loop'a girdi. Hata render sirasinda degil
  # saatler sonra "arayuz acilmiyor" olarak goruldu. Artik yer tutucu
  # kaldiysa burada, sebebi belliyken duruyoruz.
  if grep -q '{{NATS_TLS_BLOCK}}' "$conf" 2>/dev/null; then
    e1_warn "NATS conf render edilemedi: '{{NATS_TLS_BLOCK}}' yer tutucusu duruyor."
    e1_warn "python3 calisiyor mu? (bcrypt adimi da ayni yorumlayiciyi kullanir)"
    e1_die "Yer tutuculu bir nats-server.conf ile NATS ayaga kalkmaz."
  fi
}
