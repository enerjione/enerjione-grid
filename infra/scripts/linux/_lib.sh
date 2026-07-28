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
e1__on_error() {
  local code="$1" line="$2" cmd="$3"
  [[ "$E1_DYING" == "1" ]] && return 0
  e1_step_done
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
e1_version() {
  local root="${1:-.}" pkg="${1:-.}/apps/frontend-web/package.json" v=""
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
