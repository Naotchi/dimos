# 環境

Docking Station (Jetson Orin NX) / aarch64・カーネル `5.10.104-tegra`

- 製品: TP-Link Archer TX20U Nano（Realtek RTL8852BU）
- 参考レビュー: https://review.kakaku.com/review/K0001676864/ReviewCD=2002697/
- ドライバ: tp-link 配布の Linux 用 `tx20unano_WiFi_linux_v1.19.21-86-g85f01e54fa.20250826`
  - ※ZIP/フォルダ名の綴りは配布物のまま（`tx20unano...`）

# インストール手順（Jetson / arm64 で実機確認済み）

## 1. 展開 & ビルドツール

```
unzip tx20unano_WiFi_linux_*.zip
cd tx20unano_WiFi_linux_v1.19.21-86-g85f01e54fa.20250826
sudo apt install build-essential dkms linux-headers-$(uname -r)
```

## 2. ビルド & インストール

arm64 では plain `make` だと arch 違いで怒られるので `ARCH=arm64` を渡す。

```
make ARCH=arm64 -j "$(nproc)"
sudo make ARCH=arm64 install
# install が .ko コピー + depmod まで自動でやる:
#   install -p -m 644 8852bu.ko /lib/modules/5.10.104-tegra/kernel/drivers/net/wireless/
#   /sbin/depmod -a 5.10.104-tegra
```

> `make` 中に `Clock skew detected` / `modification time ... in the future` が出るが、
> これは実機の時計がファイル mtime より過去にズレているだけで**無害**（.ko は生成される）。
> 気になれば `sudo timedatectl set-ntp true` で時計合わせ。

## 3. モジュールロード

```
sudo modprobe 8852bu      # USBドライバ名は rtl8852bu として登録される
sudo dmesg | tail
```

- 8852bu は `cfg80211` に依存。初回ロードで `Unknown symbol cfg80211_* (err -2)` が出ても、
  cfg80211 が入った後の再ロードで `module init ret=0` になれば成功。

## 4. 接続確認

```
ip -br link                 # wlan0（または wlx...）が出るか
ip -br addr show wlan0
nmcli device wifi list
sudo nmcli device wifi connect "SSID" password "PASS" ifname wlan0
ping -c3 1.1.1.1
```

実機確認時の成功状態:

```
$ ip -br addr show wlan0
wlan0  UP  192.168.11.128/24 2400:4050:...（DHCPv4 + SLAAC IPv6 取得済み = 接続成立）
```

---

# ハマりどころ（重要）

## ① ZeroCD（CD-ROMモード）で起動する

挿すと最初 `scsi ... CD-ROM RTK Driver Storage`（Windows ドライバ入り CD のフリ）として出て、
その後 USB ID `35bc:0108`（"Realtek 802.11ac WLAN Adapter"）に再列挙される。
`35bc:0108` は **driver が正式対応している WLAN モードの ID**（usb.ids でも WLAN 表示）。

## ② `option`（GSMモデムドライバ）が先取りすると wlan0 が出ない ← 最大の罠

`35bc:0108` は単一の Vendor Specific(ff) インターフェース。ブート時に
`option`（USBモデム用の汎用シリアルドライバ）が**先に掴むと**、`ttyUSB0` の GSM モデム扱いになり、
`rtl8852bu` が bind できず **wlan0 が生えない**。

```
$ lsusb -t
    Port 2: Dev 5, If 0, Class=Vendor Specific Class, Driver=option   ← これが出たらNG
$ sudo dmesg | grep option
    option 1-2:1.0: GSM modem (1-port) converter detected
    usb 1-2: GSM modem (1-port) converter now attached to ttyUSB0
```

これは **boot ごとの `option` vs `rtl8852bu` の取り合い（競合）**で、
**rtl8852bu が勝ったブートでは普通に wlan0 が出て接続できる**（＝挙動が不安定）。

### 対処

- **基本は「再起動」**。クリーンブートで rtl8852bu が掴めば wlan0 で接続できる。
- ⚠️ **やってはいけない**: `option` から手動 unbind して `rtl8852bu` に `new_id`/`bind` で強制バインド。
  option が触って状態が汚れたデバイスを re-probe すると **カーネル Oops（`Internal error: Oops: 96000004`）で driver がクラッシュ**する。実際に踏んだ。
  ```
  # ↓これは Oops を起こすので NG
  echo -n '1-2:1.0' | sudo tee /sys/bus/usb/drivers/option/unbind
  echo '35bc 0108' | sudo tee /sys/bus/usb/drivers/rtl8852bu/new_id   # ← ここで Oops
  ```
- 次ブートで wlan0 が出ない時は、まず `lsusb -t` の `Driver=option` を疑う。出ていたら再起動でやり直すのが安全。
- 恒久的に競合を断ちたい場合（このJetsonにモデムは無いので低リスク）、`option` を無効化する案がある：
  `echo "blacklist option" | sudo tee /etc/modprobe.d/blacklist-option.conf`（＋ initramfs 反映）。
  ※ **未検証**。Jetson の initramfs 反映が効くか要確認なので、適用前に検証すること。
