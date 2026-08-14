---
description: Tag'a hazır mıyız? Eksik iş, sürüm tutarlılığı, migration zinciri
argument-hint: [test] [tag "<özet>"]
allowed-tools: PowerShell, Bash(git status), Bash(git log:*), Bash(git diff:*), Read, Grep, Glob
---

Tag anındaki eksik, **sahaya çıkan eksiktir** — cihazlar main'i değil tag'i
takip eder. Bu akış "her şey main'de mi" sorusunu tek ekranda cevaplar.

Kullanıcının isteği: **$ARGUMENTS**

## 1. Durumu çıkar

```
tools\surum-hazir.ps1
```

Salt-okunur. Argümanda `test` geçiyorsa ya da gerçekten tag atılacaksa
`-Test` ekle (pytest + tsc + npm test, yavaştır).

## 2. Engelleri ÖZETLE, tabloyu yapıştırma

Kullanıcıya şunu söyle: kaç engel var, her biri **kimin işi** ve **ne
yapılması gerekiyor**. Sık çıkan engeller ve karşılıkları:

- **`<dal>`: main'de olmayan N commit** → o oturum işini teslim etmemiş.
  O oturuma mesaj at, teslim etmesini iste:
  `tools\oturum-mesaj.ps1 -Kime <ad> -Mesaj "tag hazirligi: /teslim calistir"`
  Oturum kapalıysa ana ağaçtan sen teslim edebilirsin:
  `tools\oturum-teslim.ps1 -Konu <ad>`
- **`<oturum>`: commit'lenmemiş N dosya** → o worktree'de kaydedilmemiş iş
  var; mesaj at. **Kendin commit'leme** — başkasının yarım işi olabilir.
- **main origin'in önünde/gerisinde** → push/ff. Tag uzağa gider; işaret
  ettiği commit uzakta yoksa deploy o commit'i çekemez.
- **sürüm numaraları ayrışık** → beş kaynak (VERSION, package.json,
  config.py `_FALLBACK_APP_VERSION`, CLAUDE.md, CHANGELOG) aynı olmalı.
- **migration zinciri iki başlı** → tek Postgres var; iki başlı zincir
  `alembic_version`'ı herkes için bozar. Zinciri birleştir.

Uyarılar (ölü dal, açık worktree) tag'i engellemez; ayrı söyle.

## 3. Sürüm yükseltme gerekiyorsa

Tag zaten varsa ve main'de yeni iş varsa sürüm yükseltilir. **Beş yerde
birden**: `VERSION`, `apps/frontend-web/package.json`,
`apps/backend-api/app/core/config.py` (`_FALLBACK_APP_VERSION`),
`CLAUDE.md` (Sürüm satırı), `CHANGELOG.md` (yeni başlık + `[Yayınlanmamış]`
altındaki maddeleri taşı).

CHANGELOG'a **kullanıcıyı etkileyen** değişiklikleri yaz, commit listesini
değil. Türkçe, kalın başlıkla, belirtisiyle birlikte — mevcut kayıtların
üslubuna bak.

## 4. Tag

Yalnızca kullanıcı açıkça isterse ve ekran `TAG'A HAZIR` diyorsa:

```
tools\surum-hazir.ps1 -Test -Tag -Ozet "<bir cümlelik özet>"
```

Tag'i atar ve push eder — **deploy bundan tetiklenir**. Özeti kullanıcıya
onaylat; sahaya çıkan sürümün adı budur.
