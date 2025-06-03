import time
import pandas as pd
from gate_api import Configuration, ApiClient, SpotApi
from gate_api.exceptions import ApiException
from ta.trend import EMAIndicator
import requests
from flask import Flask, request
import threading

app = Flask(__name__)

# === Token dan Chat ID Telegram ===
TELEGRAM_TOKEN = '7762444749:AAHMSa0eRMjY5BKQvS3usaObnadt53Se0FA'
BASE_URL = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}'
GATE_API_KEY = 'fad1b9969ca6bc9dc8532af1add67546'
GATE_SECRET_KEY = 'COINSTATS_GATEIO'

# === Koneksi ke Gate.io API ===
configuration = Configuration(
    key=GATE_API_KEY,
    secret=GATE_SECRET_KEY
)
api_client = ApiClient(configuration)
spot_api = SpotApi(api_client)

# === Status Proses Global ===
process_status = {"running": False}

# === Fungsi untuk Mengirim Notifikasi ke Telegram ===
def send_telegram_message(chat_id, message):
    url = f'{BASE_URL}/sendMessage'
    data = {'chat_id': chat_id, 'text': message}
    try:
        requests.post(url, data=data)
    except Exception as e:
        print(f"Error saat kirim pesan telegram: {e}")

# Fungsi kirim pesan secara asynchronous agar tidak blocking
def send_telegram_message_async(chat_id, message):
    threading.Thread(target=send_telegram_message, args=(chat_id, message)).start()

# === Fungsi untuk Mendapatkan Data Harga ===
def get_klines(symbol, interval, limit=100):
    try:
        candles = spot_api.list_candlesticks(
            currency_pair=symbol,
            interval=interval,
            limit=limit
        )
        processed_candles = [[
            c[0], float(c[5]), float(c[3]), float(c[4]), float(c[2]), float(c[1])
        ] for c in candles]
        
        df = pd.DataFrame(processed_candles, columns=[
            'open_time', 'open', 'high', 'low', 'close', 'volume'
        ])
        return df
    except ApiException as e:
        return None

# === Fungsi untuk Analisis EMA ===
def analyze_ema(data, ema_short=13, ema_long=21):
    ema13 = EMAIndicator(data['close'], window=ema_short).ema_indicator()
    ema21 = EMAIndicator(data['close'], window=ema_long).ema_indicator()
    data['EMA13'] = ema13
    data['EMA21'] = ema21

    is_uptrend = ema13.iloc[-1] > ema21.iloc[-1]
    cross_signal = None
    if ema13.iloc[-2] < ema21.iloc[-2] and ema13.iloc[-1] > ema21.iloc[-1]:
        cross_signal = "Golden Cross"
    elif ema13.iloc[-2] > ema21.iloc[-2] and ema13.iloc[-1] < ema21.iloc[-1]:
        cross_signal = "Death Cross"

    return is_uptrend, cross_signal

# === Fungsi untuk Menganalisis Koin ===
def analyze_coin(symbol, interval):
    data = get_klines(symbol, interval)
    if data is None or data.empty:
        return None

    is_uptrend, cross_signal = analyze_ema(data)
    last_close = data['close'].iloc[-1]
    target_price = last_close * (1.3 if is_uptrend else 0.7)  # 30% target profit/loss
    stop_loss = last_close * (0.98 if is_uptrend else 1.02)
    potential_gain = 30  # Target profit/loss percentage

    recommendation = {
        "symbol": symbol,
        "interval": interval,
        "trend": "Naik" if is_uptrend else "Turun",
        "signal": cross_signal,
        "volume": data['volume'].iloc[-1],
        "last_close": last_close,
        "target_price": target_price,
        "stop_loss": stop_loss,
        "potential_gain": potential_gain,
    }
    return recommendation

# === Fungsi untuk Screening ===
def screen_coins(chat_id, interval):
    global process_status
    coins = spot_api.list_tickers()
    potential_coins = [
        coin.currency_pair for coin in coins if coin.currency_pair.endswith('USDT')
    ]

    long_coins = []
    short_coins = []

    for symbol in potential_coins:
        if not process_status["running"]:
            send_telegram_message(chat_id, "Proses telah dihentikan.")
            return

        analysis = analyze_coin(symbol, interval)
        if analysis is not None:
            if analysis["trend"] == "Naik" and analysis["potential_gain"] >= 30:
                long_coins.append(analysis)
            elif analysis["trend"] == "Turun" and analysis["potential_gain"] >= 30:
                short_coins.append(analysis)

    # Sort by potential gain and take top 10
    long_coins = sorted(long_coins, key=lambda x: x["potential_gain"], reverse=True)[:10]
    short_coins = sorted(short_coins, key=lambda x: x["potential_gain"], reverse=True)[:10]

    long_message = "Berikut top 10 koin yang berpotensi Naik untuk LONG:\n\n"
    for coin in long_coins:
        long_message += f"""
{coin['symbol']} ({coin['interval']}) (LONG)\n
Tren: {coin['trend']}\n
Sinyal EMA: {coin['signal']}\n
Volume: {coin['volume']:.2f} USD\n
Rekomendasi:\n
Harga menunjukkan potensi kenaikan lebih lanjut. 
Disarankan untuk melakukan Long pada harga {coin['last_close']:.2f} USD, 
Close pada harga {coin['target_price']:.2f} USD dengan stop-loss di {coin['stop_loss']:.2f} USD.
Potensi kenaikan {coin['potential_gain']}%.\n\n"""

    short_message = "Berikut top 10 koin yang berpotensi Turun untuk SHORT:\n\n"
    for coin in short_coins:
        short_message += f"""
{coin['symbol']} ({coin['interval']}) (SHORT)\n
Tren: {coin['trend']}\n
Sinyal EMA: {coin['signal']}\n
Volume: {coin['volume']:.2f} USD\n
Rekomendasi:\n
Harga menunjukkan potensi penurunan lebih lanjut. 
Disarankan untuk melakukan Short pada harga {coin['last_close']:.2f} USD, 
Close pada harga {coin['target_price']:.2f} USD dengan stop-loss di {coin['stop_loss']:.2f} USD.
Potensi penurunan {coin['potential_gain']}%.\n\n"""

    send_telegram_message(chat_id, long_message + short_message + "Apakah anda ingin menganalisa lagi? 😊")

# === Handler Telegram ===
@app.route(f'/{TELEGRAM_TOKEN}', methods=['POST'])
def telegram_webhook():
    global process_status
    update = request.json
    message = update.get('message', {})
    chat_id = message.get('chat', {}).get('id')
    text = message.get('text', '').strip()

    if text == "/start":
        send_telegram_message(chat_id, "Selamat datang! Pilih menu:\n/analyze <Ticker_USDT> <Timeframe>\n/screen <Timeframe>.")

    elif text.startswith('/screen'):
        _, interval = text.split()
        interval = interval.lower()

        process_status["running"] = True
        send_telegram_message_async(chat_id, "Sedang memproses, ketik /stop untuk menghentikan.")
        threading.Thread(target=screen_coins, args=(chat_id, interval)).start()

    elif text.startswith('/analyze'):
        try:
            _, symbol, interval = text.split()
            analysis = analyze_coin(symbol.upper(), interval.lower())
            if analysis:
                result_message = f"""
Analisis {symbol.upper()} ({interval}):\n
Tren: {analysis['trend']}\n
Sinyal EMA: {analysis['signal']}\n
Volume: {analysis['volume']:.2f} USD\n
Rekomendasi:\n
Harga terakhir: {analysis['last_close']:.2f} USD\n
Target: {analysis['target_price']:.2f} USD\n
Stop Loss: {analysis['stop_loss']:.2f} USD.\n
Potensi: {analysis['potential_gain']}%.\n\n
Apakah anda ingin menganalisa lagi? 😊"""
                send_telegram_message(chat_id, result_message)
            else:
                send_telegram_message(chat_id, "Data tidak tersedia atau analisis gagal.")
        except Exception as e:
            send_telegram_message(chat_id, f"Terjadi kesalahan: {e}")

    elif text == "/stop":
        process_status["running"] = False
        send_telegram_message(chat_id, "Proses dihentikan.")

    else:
        send_telegram_message(chat_id, "Perintah tidak dikenal. Gunakan /start untuk melihat menu.")

    return '', 200

# === Jalankan Server ===
if __name__ == '__main__':
    app.run(port=5000)
