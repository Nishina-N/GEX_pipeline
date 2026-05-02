import sys, yfinance as yf, pandas as pd, json, os

def test_types(symbol):
    print(f"Testing {symbol}")
    with open(f"data/levels/{symbol}.json") as f:
        gex = json.load(f)
    ticker = yf.Ticker(symbol)
    df = ticker.history(period="1y").tail(100)
    df.index = df.index.tz_localize(None)
    last_date = df.index[-1]
    future_dates = pd.bdate_range(start=last_date + pd.Timedelta(days=1), periods=30)
    df_future = pd.DataFrame(index=future_dates, columns=df.columns)
    df = pd.concat([df, df_future])
    candle_high = df['High'].max()
    candle_low = df['Low'].min()
    print(f"candle_high type: {type(candle_high)}")
    print(f"candle_low type: {type(candle_low)}")
    
    levels = gex['levels']
    call_prices = []
    put_prices = []
    gex_lines = []
    if levels.get('callWall'): gex_lines.append({'price': levels['callWall'], 'label': 'Call Wall'})
    if levels.get('putWall'): gex_lines.append({'price': levels['putWall'], 'label': 'Put Wall'})
    for wall in levels.get('callWalls', []): gex_lines.append({'price': wall['strike'], 'label': 'Call Wall'})
    for wall in levels.get('putWalls', []): gex_lines.append({'price': wall['strike'], 'label': 'Put Wall'})
    
    for line in gex_lines:
        lbl = line['label'].lower()
        if 'call' in lbl: call_prices.append(line['price'])
        elif 'put' in lbl: put_prices.append(line['price'])
        
    y_max_candidate = max(call_prices) if call_prices else candle_high
    y_min_candidate = min(put_prices)  if put_prices  else candle_low
    print(f"y_max_candidate type: {type(y_max_candidate)}")
    print(f"y_min_candidate type: {type(y_min_candidate)}")

test_types("SOXX")
