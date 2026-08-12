---
description: Paralel oturumlari yonet — durum, yeni oturum, birlestirme, panel
argument-hint: [durum|ac <ad>|birlestir [<ad>]|panel|kapat <ad>]
allowed-tools: PowerShell, Bash(git status), Bash(git log:*), Bash(git worktree list:*), Read, Grep, Glob
---

Bu depoda birden fazla Claude oturumu ayni anda calisiyor. Altyapinin tamami
`tools/oturum-*.ps1` ve `.claude/oturumlar.json` defteridir (bkz. CLAUDE.md >
Paralel oturumlar).

Kullanicinin istegi: **$ARGUMENTS**

Istege gore su akislardan birini yurut. Argüman bos ya da `durum` ise
**durum** akisini calistir.

## durum
`tools\oturum-kayit.ps1` calistir. Ciktiyi kullaniciya OZETLE:
- Kac oturum acik, hangileri canli, hangileri uykuda.
- **10'dan fazla commit geride kalan dal varsa** ayrica uyar: merge kavgasi
  buyuyor, `birlestir` onerilmeli.
- Carpisan dosya varsa hangi oturumlarin carpistigini soyle.
Tabloyu oldugu gibi yapistirma; ne yapilmasi gerektigini soyle.

## ac <ad>
`tools\oturum-ac.ps1 -Konu <ad> -VSCode -Aciklama "<isin bir cumlelik tarifi>"`
Ad yoksa kullanicidan iste (kucuk harf/tire, ornek: `pdf-rapor`).
`-VSCode` bilerek var: VSCode eklentisinde sekmeler ayni klasoru paylasir,
ayri pencere olmazsa editor ana agacta kalir.
Calistiktan sonra kullaniciya ACILAN YENI PENCEREDE Claude baslatmasini soyle.

## birlestir [<ad>]
Ad verilmisse `tools\oturum-birlestir.ps1 -Konu <ad>` (RAPOR kipi).
Ad yoksa `tools\oturum-birlestir.ps1 -Hepsi`.
Rapor temizse kullaniciya `-Uygula` ile devam etmeyi oner — **kendiliginden
`-Uygula` calistirma**, rebase baskasinin dalini degistirir.
Prova cakisma gosteriyorsa once cakisan dosyalari ozetle.

## panel
`tools\oturum-panel.ps1 -Ac` komutunu ARKA PLANDA baslat (run_in_background).
Sonra kullaniciya `http://localhost:7373/` adresini ver. Panel calisirken bu
oturum bloke olmamali.

## kapat <ad>
`tools\oturum-kapat.ps1 -Konu <ad>`. Commit'lenmemis is varsa script durur —
`-Zorla` ONERME, once isin commit'lenmesini soyle.

---

Not: `oturum-ac`, `oturum-kapat` ve `-Uygula` kalici sonuc dogurur; digerleri
salt-okunur. Salt-okunur olanlari sormadan calistir, digerlerini kullanicinin
istegi uzerine.
