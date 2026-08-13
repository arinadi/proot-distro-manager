# Cheatsheet: Custom user.js Firefox untuk proot XFCE (Android)

## 1. Apa itu user.js?
File teks yang ditaruh di **folder profil Firefox**. Setiap baris `user_pref(...)` langsung menimpa nilai di `about:config` setiap Firefox dibuka — jadi kamu tidak perlu klik satu-satu di `about:config`.

## 2. Kenapa perlu tuning khusus di proot?
| Masalah di proot-distro | Dampak ke Firefox |
|---|---|
| Tidak ada akses GPU native (render lewat X11/VNC software) | Hardware acceleration malah bikin lag/crash |
| Storage lewat bind-mount Android (I/O lambat) | Disk cache Firefox jadi bottleneck |
| RAM dibagi dengan Termux + Android itu sendiri | Butuh proses konten (`dom.ipc.processCount`) dikit |
| Tanpa systemd, background service Firefox suka nyangkut | Auto-update & telemetry perlu dimatikan |

## 3. Cara pasang
```bash
# 1. Cari folder profil (jalankan sekali biar profil terbuat)
firefox &
sleep 5 && killall firefox

# 2. Temukan folder profilnya
ls ~/.mozilla/firefox/ | grep default

# 3. Salin user.js ke folder profil itu
cp user.js ~/.mozilla/firefox/xxxxxxxx.default-release/

# 4. Jalankan ulang Firefox
firefox
```
Cek berhasil: buka `about:config`, cari `browser.cache.disk.enable` → harus `false` (tulisan **bold**, tanda sudah dioverride user.js).

## 4. Ringkasan kategori setting

- **Rendering** — matikan hardware accel, paksa software WebRender. Wajib di proot karena GPU pass-through hampir selalu tidak ada.
- **Animasi UI** — dimatikan semua, murni buat ngirit CPU/render, tidak ngaruh ke privasi.
- **Memory** — proses konten dibatasi 2 (`dom.ipc.processCount`), cache diarahkan ke RAM bukan disk.
- **Disk cache** — dimatikan total, karena I/O ke storage Android lewat bind-mount lambat dan bikin macet lebih parah daripada hemat RAM sedikit.
- **Network** — matikan prefetch/preconnect/speculative connect → hemat request nganggur, bagus juga kalau jaringan mobile data.
- **Telemetry & background task** — dimatikan supaya tidak ada proses nempel di belakang yang makan CPU/RAM percuma.
- **Privasi dasar** — level moderat (tracking protection standar + cookie pihak ketiga diblokir + HTTPS-only). **Sengaja tidak** level arkenfox penuh karena itu didesain buat desktop kencang dan sering merusak login/website — di proot yang sudah terbatas resource, breakage tambahan cuma nambah beban troubleshoot.
- **Sandbox** — `security.sandbox.content.level` diset `0`. Kernel Android biasanya tidak izinkan *unprivileged user namespaces*, yang dibutuhkan sandbox content process Firefox — akibatnya di proot sandbox sering bikin tab/proses konten gagal jalan atau crash terus. Level `0` = paling longgar, paling stabil di proot, tapi proteksinya paling kecil. Trade-off: kalau ada exploit lewat halaman web, proses jahat itu punya akses lebih luas ke rootfs proot kamu (bukan ke Android host-nya langsung). Kalau device dipakai buka situs sembarangan, coba level `1` atau `2` dulu sebelum ke `0` — masih dapat proteksi minimal tanpa crash.
- **Update & startup** — auto-update dimatikan (proot gak ada service updater resmi), halaman awal `about:blank` biar start lebih cepat.

## 5. Kalau mau lebih privasi-ketat
Base ini aman dipakai harian. Kalau kamu memang mau privasi setara arkenfox penuh, ambil `user.js` resmi dari **github.com/arkenfox/user.js**, lalu tempel bagian **Rendering/Memory/Disk cache** dari file saya ini sebagai `user-overrides.js` — arkenfox otomatis baca file overrides itu di baris paling akhir, jadi setting performa saya tetap menang tanpa perlu edit file arkenfox aslinya.

## 6. Troubleshooting cepat
| Gejala | Penyebab kemungkinan | Fix |
|---|---|---|
| Firefox lag pas render halaman | Software WebRender belum kepakai | Cek `gfx.webrender.software=true` masih ke-apply, restart total (bukan cuma reload) |
| Video call / WebGL rusak | Hardware accel dimatikan | Tambahkan override khusus site itu, jangan ubah pref global |
| Login Google/situs bank gagal | Cookie pihak ketiga diblokir terlalu ketat | Ubah `network.cookie.cookieBehavior` ke `0` sementara, atau pakai container khusus |
| Firefox sering nge-freeze | Terlalu banyak tab + `dom.ipc.processCount=2` | Naikkan jadi `3` kalau RAM device ≥6GB |
| Update Firefox gak jalan | `app.update.auto=false` memang sengaja | Update manual lewat package manager proot (`apt/pkg update firefox`) |
| Firefox gagal start / tab langsung crash total | Sandbox butuh unprivileged user namespace yang tidak diizinkan kernel Android | Set `security.sandbox.content.level=0` (sudah ada di user.js), atau `export MOZ_DISABLE_SANDBOX=1` sebelum jalankan firefox kalau masih crash |

## 7. Catatan penting
- **Backup** `user.js` ini sebelum update Firefox versi baru — kadang ada pref yang deprecated dan perlu disesuaikan.
- File ini **ditimpa ulang setiap start Firefox**, jadi perubahan manual di `about:config` yang tercantum di sini akan otomatis balik ke nilai user.js tiap buka browser (ini memang tujuannya).
- Untuk XFCE session lewat VNC, tambahan opsional di luar Firefox: set env `LIBGL_ALWAYS_SOFTWARE=1` sebelum menjalankan Firefox kalau masih ada masalah render aneh.
