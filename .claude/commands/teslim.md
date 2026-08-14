---
description: Bu oturumun işini doğrula ve main'e al (tag'a hazır hale getir)
argument-hint: [prova] [<oturum-adı>]
allowed-tools: PowerShell, Bash(git status), Bash(git log:*), Bash(git diff:*), Read, Grep, Glob
---

Bu oturumun işi bitti; **main'e alınacak**. Saha cihazları main'i değil tag'i
takip eder — main'e girmeyen iş, tag'a girmeyen iştir.

Kullanıcının isteği: **$ARGUMENTS**

## Önce: commit'lenmemiş iş var mı

`git status` ile bak. Varsa **teslim scriptini çalıştırma** — script zaten
durur. Önce işi commit'le (kendi worktree'nde geniş komut serbest):

```
git add -A
git commit -m "feat(scope): ..."   # Türkçe açıklama, conventional commit
```

Commit mesajını sen yaz; kullanıcıya sorma — ne yaptığını sen biliyorsun.

## Sonra: teslim

Argümanda `prova` geçiyorsa ya da kullanıcı emin değilse önce prova:

```
tools\oturum-teslim.ps1 -Prova
```

Prova hiçbir şeyi değiştirmez: kirlilik → rebase provası → migration borcu →
migration zinciri → testler. Temizse gerçek teslim:

```
tools\oturum-teslim.ps1
```

Ana ağaçtan çalıştırıyorsan `-Konu <ad>` ver. Oturumun kendi penceresindeysen
gerekmez.

## Script durursa

Her adım kendi çözümünü ekrana yazar. **Sen çöz, kullanıcıya devretme:**

- **Migration borcu** → `/migration` akışını çalıştır, migration'ı üret, gözden
  geçir, commit'le, tekrar teslim et.
- **Migration zinciri iki başlı** → başka bir oturum aynı ata üstüne migration
  yazmış. Seninkinin `down_revision`'ını onunkinin `revision`'ına bağla, dosya
  adındaki sırayı düzelt.
- **Test düştü** → düzelt. Testi atlamak (`-TestAtla`) son çare; kullanıcı
  açıkça istemeden kullanma.
- **Rebase çakışması** → çakışmayı ancak işi yazan çözer, yani sen. `cd` ile
  worktree'ye gir, `git rebase main`, çakışmaları çöz, `--continue`, tekrar
  teslim et.

`-MigrationGerekmiyor` yalnızca model dosyasına dokunulup **şema
değişmediğinde** (yorum, tip ipucu, docstring) kullanılır.

## Teslimden sonra

Ekrana ne olduğunu özetle: kaç commit main'e girdi, hangi testler koştu,
push edildi mi. Sonra worktree'yi kapatmayı öner:

```
tools\oturum-kapat.ps1 -Konu <ad>     # ANA AĞAÇTAN çalıştırılır
```

Kullanıcı "hepsi bitti mi" diye sorarsa `/surum` akışına yönlendir.
