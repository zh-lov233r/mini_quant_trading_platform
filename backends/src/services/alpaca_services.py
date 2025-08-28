# 执行交易


import requests, os

ALPACA_KEY = os.getenv("ALPACA_KEY")
ALPACA_SECRET = os.getenv("ALPACA_SECRET")
BASE_URL = "https://paper-api.alpaca.markets/v2"

def place_order(symbol: str, qty: int, side: str="buy", type: str="market", tif: str="day"):
    url = f"{BASE_URL}/v2/orders"
    headers = {"APCA-API-KEY-ID":ALPACA_KEY,"APCA-API-SECRET-KEY":ALPACA_SECRET}
    order = {"symbol":symbol,"qty":qty,"side":side,"type":type,"time_in_force":tif}
    r = requests.post(url,json=order,headers=headers)
    return r.json()



