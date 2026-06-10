# MT5 Python Trading Bot

Bot live trading MetaTrader 5 berbasis Python dengan strategi awal EMA crossover + filter RSI.

Strategi ini hanya contoh awal, bukan jaminan profit. Uji di akun demo terlebih dahulu sebelum mengaktifkan live trading di akun real.

## Fitur

- Koneksi ke akun MT5 via package `MetaTrader5`
- Strategi EMA fast/slow crossover dengan filter RSI
- Stop loss dan take profit otomatis
- Lot dihitung dari persentase risiko per trade
- Filter spread maksimum
- Batas jumlah posisi terbuka berdasarkan `MAGIC_NUMBER`
- Mode proteksi default: `LIVE_TRADING=false`

## Persyaratan

- Python 3.10+
- Terminal MetaTrader 5 sudah terinstall dan login di mesin yang sama
- Package Python `MetaTrader5`

Catatan: package resmi `MetaTrader5` paling stabil di Windows karena harus terhubung ke terminal MT5 desktop. Jika memakai macOS, biasanya perlu menjalankan bot di Windows/VPS Windows atau setup MT5 via Wine yang kompatibel.

## Setup

1. Buat virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
```

Di Windows:

```powershell
python -m venv .venv
.venv\Scripts\activate
```

2. Install dependency:

```bash
pip install -r requirements.txt
```

3. Buat file `.env` dari contoh:

```bash
cp .env.example .env
```

4. Isi kredensial MT5 di `.env`:

```env
MT5_LOGIN=12345678
MT5_PASSWORD=password_akun_mt5
MT5_SERVER=NamaServerBroker
```

5. Jalankan bot:

```bash
python mt5_bot.py
```

## Mengaktifkan Live Trading

Default bot tidak mengirim order karena:

```env
LIVE_TRADING=false
```

Setelah diuji di akun demo, ubah menjadi:

```env
LIVE_TRADING=true
```

Pastikan AutoTrading di terminal MT5 aktif.

## Konfigurasi Utama

- `SYMBOL`: pair/instrumen, contoh `EURUSD`, `XAUUSD`, `GBPUSD`
- `TIMEFRAME`: `M1`, `M5`, `M15`, `M30`, `H1`, `H4`, `D1`
- `RISK_PERCENT`: risiko per trade dari equity
- `MAX_SPREAD_POINTS`: bot skip saat spread terlalu besar
- `MAX_OPEN_POSITIONS`: batas posisi aktif milik bot
- `FAST_EMA` dan `SLOW_EMA`: parameter crossover
- `RSI_BUY_MAX` dan `RSI_SELL_MIN`: filter momentum
- `STOP_LOSS_POINTS` dan `TAKE_PROFIT_POINTS`: jarak SL/TP dalam point broker
- `MAGIC_NUMBER`: penanda posisi milik bot

## Cara Kerja Strategi

- Buy saat EMA cepat cross ke atas EMA lambat dan RSI tidak terlalu overbought.
- Sell saat EMA cepat cross ke bawah EMA lambat dan RSI tidak terlalu oversold.
- Bot hanya membaca candle yang sudah close agar sinyal tidak berubah di tengah candle.

## Saran Awal

- Uji dulu di demo minimal 1-2 minggu.
- Mulai dari lot kecil dan risiko rendah, misalnya `RISK_PERCENT=0.25` atau `0.5`.
- Sesuaikan `STOP_LOSS_POINTS` dan `TAKE_PROFIT_POINTS` untuk tiap symbol. `XAUUSD` biasanya butuh jarak lebih besar dibanding `EURUSD`.
