import xml.etree.ElementTree as ET
import datetime
import requests
import pandas as pd
import streamlit as st

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

# 1. 네이버 증권 HTS 공식 차트 데이터 엔드포인트 (차단 없음, 완벽 호환)
def get_daily_candles(ticker, count=120):
    url = f"https://fchart.stock.naver.com/sise.nhn?symbol={ticker}&timeframe=day&count={count}&requestType=0"
    try:
        res = requests.get(url, headers=HEADERS, timeout=5)
        if res.status_code != 200:
            return None
        
        root = ET.fromstring(res.text)
        items = root.findall('.//item')
        if not items:
            return None
            
        rows = []
        for item in items:
            # data format: "날짜|시가|고가|저가|종가|거래량"
            val = item.attrib.get('data', '')
            parts = val.split('|')
            if len(parts) >= 6:
                d_raw = parts[0].strip()
                d_str = f"{d_raw[:4]}.{d_raw[4:6]}.{d_raw[6:]}" if len(d_raw) == 8 else d_raw
                rows.append({
                    '날짜': d_str,
                    '시가': float(parts[1]),
                    '고가': float(parts[2]),
                    '저가': float(parts[3]),
                    '종가': float(parts[4]),
                    '거래량': float(parts[5])
                })
        df = pd.DataFrame(rows)
        # 최신 날짜가 위로 오도록 내림차순 정렬
        return df.sort_values(by='날짜', ascending=False).reset_index(drop=True)
    except Exception:
        return None

# 2. 보조지표 계산 (20/60선, 볼린저밴드 40선, 일목균형표 구름대)
def calculate_indicators(df):
    df_calc = df.iloc[::-1].copy().reset_index(drop=True)
    n = len(df_calc)
    
    # 이동평균선 (20선, 60선)
    df_calc['MA20'] = df_calc['종가'].rolling(window=min(20, n), min_periods=5).mean()
    df_calc['MA60'] = df_calc['종가'].rolling(window=min(60, n), min_periods=10).mean()
    
    # 볼린저밴드 (전자책 기준: Period 40, D1 1.5)
    bb_window = min(40, n)
    ma_bb = df_calc['종가'].rolling(window=bb_window, min_periods=5).mean()
    std_bb = df_calc['종가'].rolling(window=bb_window, min_periods=5).std().fillna(0)
    df_calc['BB_upper'] = ma_bb + (1.5 * std_bb)
    
    # 일목균형표
    h9 = df_calc['고가'].rolling(window=min(9, n), min_periods=3).max()
    l9 = df_calc['저가'].rolling(window=min(9, n), min_periods=3).min()
    tenkan = (h9 + l9) / 2
    
    h26 = df_calc['고가'].rolling(window=min(26, n), min_periods=5).max()
    l26 = df_calc['저가'].rolling(window=min(26, n), min_periods=5).min()
    kijun = (h26 + l26) / 2
    
    span1 = (tenkan + kijun) / 2
    h52 = df_calc['고가'].rolling(window=min(52, n), min_periods=10).max()
    l52 = df_calc['저가'].rolling(window=min(52, n), min_periods=10).min()
    span2 = (h52 + l52) / 2
    
    df_calc['Span1'] = span1.shift(26)
    df_calc['Span2'] = span2.shift(26)
    
    return df_calc.iloc[::-1].reset_index(drop=True)

# 3. 기준일자 기반 급등주 4대 조건 & 10분 루틴 판별
def analyze_stock_by_date(ticker, target_date_str, account_seed=10000000):
    raw_df = get_daily_candles(ticker, count=120)
    if raw_df is None or len(raw_df) == 0:
        return None, f"[{ticker}] 데이터를 불러올 수 없습니다. 종목 코드를 확인해 주세요."
        
    filtered_df = raw_df[raw_df['날짜'] <= target_date_str].reset_index(drop=True)
    if len(filtered_df) < 5:
        return None, f"기준일({target_date_str}) 이전의 거래 데이터가 부족합니다."
        
    actual_base_date = filtered_df.iloc[0]['날짜']
    df = calculate_indicators(filtered_df)
    
    today = df.iloc[0]
    yesterday = df.iloc[1] if len(df) > 1 else today
    
    c_price = today['종가']
    o_price = today['시가']
    h_price = today['고가']
    l_price = today['저가']
    prev_close = yesterday['종가']
    prev_open = yesterday['시가']
    change_rate = ((c_price - prev_close) / prev_close) * 100 if prev_close > 0 else 0
    
    # 10분 루틴 지표 (5봉 최저점, 20봉 최고점)
    df_20 = df.head(min(20, len(df)))
    low_5 = df.head(min(5, len(df)))['저가'].min()
    high_20 = df_20['고가'].max()
    stop_loss = low_5
    target_price = round(high_20 * 0.99)
    
    loss_per_share = max(1.0, c_price - stop_loss)
    profit_per_share = target_price - c_price
    loss_rate = ((stop_loss - c_price) / c_price) * 100
    profit_rate = ((target_price - c_price) / c_price) * 100
    rr_ratio = round(profit_per_share / loss_per_share, 2) if loss_per_share > 0 else 0
    max_risk = account_seed * 0.01
    buy_qty = int(max_risk // loss_per_share)
    total_invest = int(buy_qty * c_price)
    
    # [급등주 4대 조건 검증]
    cond_results = []
    
    # 조건 1. 양봉+양봉 반등
    cond1 = (c_price > o_price) and (prev_close > prev_open)
    cond_results.append({
        "조건": "1. 양봉+양봉 반등",
        "달성": cond1,
        "설명": f"전일 {'양봉' if prev_close > prev_open else '음봉'}, 당일 {'양봉' if c_price > o_price else '음봉'} -> 단기 반등 추세 유지"
    })
    
    # 조건 2. 눌림 바닥 지지 (20선, 60선, 일목 구름대)
    ma20_val = today['MA20'] if not pd.isna(today['MA20']) else 0
    ma60_val = today['MA60'] if not pd.isna(today['MA60']) else 0
    span1_val = today['Span1'] if not pd.isna(today['Span1']) else 0
    span2_val = today['Span2'] if not pd.isna(today['Span2']) else 0
    
    cloud_top = max(span1_val, span2_val)
    
    supported_indicators = []
    if ma20_val > 0 and (l_price >= ma20_val * 0.98 and abs(l_price - ma20_val) / ma20_val <= 0.025):
        supported_indicators.append(f"20일선 지지 ({ma20_val:,.0f}원)")
    elif ma20_val > 0 and l_price >= ma20_val:
        supported_indicators.append("20일선 상단 안착")
        
    if ma60_val > 0 and (l_price >= ma60_val * 0.98 and abs(l_price - ma60_val) / ma60_val <= 0.025):
        supported_indicators.append(f"60일선 지지 ({ma60_val:,.0f}원)")
    elif ma60_val > 0 and l_price >= ma60_val:
        supported_indicators.append("60일선 상단 안착")
        
    if cloud_top > 0 and (l_price >= cloud_top * 0.98 and abs(l_price - cloud_top) / cloud_top <= 0.03):
        supported_indicators.append(f"일목 구름대 지지 ({cloud_top:,.0f}원)")
    elif cloud_top > 0 and l_price >= cloud_top:
        supported_indicators.append("일목 구름대 위 안착")

    exact_hits = [s for s in supported_indicators if "선 지지" in s or "구름대" in s]
    if exact_hits:
        cond2 = True
        desc2 = " / ".join(exact_hits)
    elif supported_indicators:
        cond2 = True
        desc2 = " / ".join(supported_indicators[:2])
    else:
        cond2 = False
        desc2 = "주요 지지선 미형성 또는 이탈 상태"
        
    cond_results.append({
        "조건": "2. 눌림 바닥 지지 (20이평/60이평/구름대)",
        "달성": cond2,
        "설명": f"확인된 지지선: {desc2}" if cond2 else desc2
    })
    
    # 조건 3. 볼린저밴드 상단과 이격 (5% 이상)
    bb_upper = today['BB_upper'] if not pd.isna(today['BB_upper']) else 0
    bb_dist = ((bb_upper - c_price) / c_price) * 100 if bb_upper > 0 else 0
    cond3 = bb_dist >= 5.0
    cond_results.append({
        "조건": "3. 볼린저밴드 상단과 이격 (5% 이상)",
        "달성": cond3,
        "설명": f"볼밴 상단({bb_upper:,.0f}원)까지 상승 여력: {bb_dist:.1f}% (5% 이상 확보 시 통과)"
    })
    
    # 조건 4. 눌림 조정봉 5봉 이내
    peak_idx = df_20['고가'].idxmax()
    pullback_days = peak_idx
    cond4 = pullback_days <= 5
    cond_results.append({
        "조건": "4. 눌림 조정봉 5봉 이내",
        "달성": cond4,
        "설명": f"최근 전고점 이후 조정 기간: {pullback_days}봉 경과 (5봉 이내 빠른 반등 요건)"
    })
    
    satisfied_count = sum(1 for c in cond_results if c['달성'])
    
    result = {
        "종목명": ticker,
        "기준일자": actual_base_date,
        "현재가": c_price,
        "등락률": change_rate,
        "손절가": stop_loss,
        "목표가": target_price,
        "예상손실율": loss_rate,
        "예상수익률": profit_rate,
        "손익비": rr_ratio,
        "매수가능수량": buy_qty,
        "예상투자금액": total_invest,
        "충족개수": satisfied_count,
        "조건목록": cond_results
    }
    return result, None

# 4. Streamlit UI
st.set_page_config(page_title="급등주 4대 조건 & 10분 루틴 판별기", layout="wide")
st.title("⚡ 급등주 선별 4대 조건 & 10분 루틴 자동 판별기")

st.sidebar.header("⚙️ 10분 루틴 자금 설정")
user_seed = st.sidebar.number_input("내 계좌 총 자산 (원)", min_value=100000, value=10000000, step=1000000)
st.sidebar.caption("단일 종목 손절 시 계좌 1% 손실 원칙에 맞춰 매수 수량을 계산합니다.")

c1, c2, c3 = st.columns([2, 2, 1.5])
with c1:
    ticker_input = st.text_input("종목코드 6자리 (예: 005830, 005930):")
with c2:
    selected_date = st.date_input("분석 기준 일자 선택 (기본: 오늘)", value=datetime.date.today())
with c3:
    st.write("")
    st.write("")
    run_btn = st.button("🔍 급등주 조건 분석", use_container_width=True)

if run_btn and ticker_input:
    ticker = ticker_input.strip()
    target_date_str = selected_date.strftime("%Y.%m.%d")
    
    with st.spinner(f"[{ticker}] 기준일({target_date_str}) 데이터를 고속 분석 중입니다..."):
        res, err_msg = analyze_stock_by_date(ticker, target_date_str, user_seed)
        if err_msg:
            st.error(err_msg)
        else:
            st.subheader(f"📌 종목코드: {ticker} - 기준일: {res['기준일자']}")
            
            cnt = res['충족개수']
            if cnt >= 3:
                st.success(f"🔥 **급등주 4대 조건 중 {cnt}개 충족! (당일 매수 강력 추천 자리)**")
            elif cnt == 2:
                st.warning(f"⚡ **급등주 4대 조건 중 {cnt}개 충족 (D+1 흐름 관망 또는 분할 접근)**")
            else:
                st.info(f"⚠️ **급등주 4대 조건 중 {cnt}개 충족 (급등 탄력 조건 미흡, 원칙 준수 필터링)**")
                
            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric(f"기준 종가 ({res['기준일자']})", f"{res['현재가']:,.0f}원", f"{res['등락률']:+.2f}%")
            m2.metric("손절가 (5봉 최저)", f"{res['손절가']:,.0f}원", f"{res['예상손실율']:.1f}%")
            m3.metric("목표가 (20봉 99%)", f"{res['목표가']:,.0f}원", f"+{res['예상수익률']:.1f}%")
            m4.metric("손익비", f"{res['손익비']}", "1.5 이상 권장" if res['손익비'] >= 1.5 else "손익비 부족")
            m5.metric("매수 가능 수량", f"{res['매수가능수량']}주", f"약 {res['예상투자금액']:,.0f}원")
            
            st.markdown("---")
            
            st.subheader(f"📋 {res['기준일자']} 시점 급등주 선별 4대 조건 체크리스트")
            for item in res['조건목록']:
                status_icon = "✅" if item['달성'] else "❌"
                status_text = "**충족**" if item['달성'] else "**미충족**"
                st.markdown(f"{status_icon} **{item['조건']}** : {status_text}")
                st.caption(f"&nbsp;&nbsp;&nbsp;&nbsp;↳ {item['설명']}")
                
            st.markdown("---")
            with st.expander("📌 10분 루틴 핵심 매매 원칙 (손절/익절/D+5 룰)", expanded=True):
                st.write(f"- **손절 기준:** 주가가 5봉 최저점({res['손절가']:,.0f}원) 이탈 시 감정 없이 즉시 기계적 손절.")
                st.write(f"- **목표가 도달:** 20봉 전고점 부근({res['목표가']:,.0f}원) 도달 시 익절.")
                st.write("- **5% 조기 달성:** 매수 후 하루이틀 만에 +5% 이상 도달 시 50% 분할 익절 후 손절가를 매수가로 상향.")
                st.write("- **D+5 원칙:** 5영업일 동안 손절가나 목표가에 닿지 못하면 D+6일에 기회비용 고려 전량 정리.")