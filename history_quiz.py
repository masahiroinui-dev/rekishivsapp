import streamlit as st
from streamlit_drawable_canvas import st_canvas
import pandas as pd
from PIL import Image
import random
import os
from google import genai

# --- 1. アプリの設定 ---
st.set_page_config(page_title="歴史・手書き早書きバトル", layout="centered")

# --- 2. API初期化 (Gemini API) ---
# Streamlit Cloudの Secrets に GEMINI_API_KEY を設定してください
try:
    client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])
except Exception:
    st.error("APIキーが設定されていません。StreamlitのSecretsに 'GEMINI_API_KEY' を設定してください。")
    st.stop()

# --- 3. デザイン（CSS） ---
st.markdown("""
    <style>
    .block-container { padding-top: 1rem !important; }
    .stCanvasContainer { border: 3px solid #4a4a4a; border-radius: 12px; background-color: #ffffff; }
    .status-box { 
        padding: 10px; 
        border-radius: 8px; 
        margin-bottom: 15px; 
        text-align: center; 
        font-weight: bold;
        background-color: #f0f2f6;
        border: 1px solid #d1d5db;
    }
    .question-box {
        background-color: #e1f5fe;
        padding: 20px;
        border-radius: 10px;
        border-left: 5px solid #0288d1;
        margin-bottom: 20px;
        font-size: 1.2rem;
    }
    </style>
    """, unsafe_allow_html=True)

# --- 4. データ読み込み（既存のCSVを使用） ---
@st.cache_data
def load_data():
    csv_file = "rekishi_questions.xlsx - Sheet1.csv"
    if os.path.exists(csv_file):
        try:
            # ヘッダーなしを想定し、1列目を問題、2列目を答えとする
            df = pd.read_csv(csv_file, header=None, names=["question", "answer"])
            return df.dropna()
        except Exception as e:
            st.error(f"CSVの読み込みに失敗しました: {e}")
    
    # ファイルがない場合のサンプル
    sample_data = {
        "question": ["聖徳太子が派遣した使節を何という？", "日本最古の貨幣と言われるものは？", "卑弥呼が治めた国は？"],
        "answer": ["遣隋使", "富本銭", "邪馬台国"]
    }
    return pd.DataFrame(sample_data)

df = load_data()

# --- 5. セッション状態の初期化 ---
if "user_role" not in st.session_state: st.session_state.user_role = "player"
if "room_id" not in st.session_state: st.session_state.room_id = ""
if "current_q_idx" not in st.session_state: st.session_state.current_q_idx = 0
if "canvas_key" not in st.session_state: st.session_state.canvas_key = 0
if "feedback" not in st.session_state: st.session_state.feedback = ""

# --- 6. サイドバー（管理・入室設定） ---
with st.sidebar:
    st.header("🎮 対戦管理")
    mode = st.radio("あなたの役割", ["プレイヤーとして参加", "オーナー（出題者）"])
    
    if mode == "オーナー（出題者）":
        admin_pw = st.text_input("管理パスワード", type="password")
        # デフォルトパスワードは admin123 (Secretsで設定可能)
        if admin_pw == st.secrets.get("ADMIN_PASSWORD", "admin123"):
            st.session_state.user_role = "owner"
            st.success("オーナーログイン完了")
            if st.button("新しいルームを作成"):
                st.session_state.room_id = str(random.randint(1000, 9999))
        else:
            st.session_state.user_role = "player"
    else:
        st.session_state.user_role = "player"
        st.session_state.room_id = st.text_input("部屋番号を入力", value=st.session_state.room_id)

# --- 7. メインUI ---
st.title("⚔️ 歴史手書きバトル")

if not st.session_state.room_id:
    st.info("サイドバーから部屋を作成するか、部屋番号を入力して開始してください。")
    st.stop()

# ステータス表示
role_label = "👑 オーナー" if st.session_state.user_role == "owner" else "👤 プレイヤー"
st.markdown(f"<div class='status-box'>ROOM: {st.session_state.room_id} | {role_label}</div>", unsafe_allow_html=True)

# 問題の取得
q_data = df.iloc[st.session_state.current_q_idx % len(df)]
current_question = q_data["question"]
correct_answer = str(q_data["answer"])

st.markdown(f"<div class='question-box'><b>問題:</b><br>{current_question}</div>", unsafe_allow_html=True)

# 手書きキャンバス
canvas_result = st_canvas(
    fill_color="rgba(255, 165, 0, 0.3)",
    stroke_width=6,
    stroke_color="#000000",
    background_color="#ffffff",
    height=300,
    width=700,
    drawing_mode="freedraw",
    key=f"canvas_{st.session_state.canvas_key}",
    update_streamlit=True,
)

col1, col2 = st.columns(2)

with col1:
    if st.button("✅ 回答を送信", use_container_width=True, type="primary"):
        if canvas_result.image_data is not None:
            with st.spinner("AI採点中..."):
                # 画像処理（Geminiに投げるために変換）
                img = Image.fromarray(canvas_result.image_data.astype('uint8'))
                
                try:
                    # AIへのプロンプト
                    prompt = f"""
                    あなたは歴史の先生です。以下の条件で採点してください。
                    
                    【問題】: {current_question}
                    【模範解答】: {correct_answer}
                    
                    【指示】:
                    画像の手書き文字を読み取ってください。
                    多少の字の崩れや、歴史的に意味が通じる範囲（例：旧字体や同音の通じなど）であれば「正解」と判定してください。
                    
                    回答形式：
                    読み取った文字: (ここに文字)
                    判定: (正解 または 不正解)
                    理由: (簡潔に)
                    """
                    
                    response = client.models.generate_content(
                        model='gemini-2.0-flash',
                        contents=[img, prompt]
                    )
                    
                    result_text = response.text
                    st.session_state.feedback = result_text
                    
                    if "正解" in result_text:
                        st.balloons()
                        st.success(result_text)
                    else:
                        st.error(result_text)
                        
                except Exception as e:
                    st.error(f"エラーが発生しました: {e}")

with col2:
    if st.button("🗑️ クリア", use_container_width=True):
        st.session_state.canvas_key += 1
        st.session_state.feedback = ""
        st.rerun()

# --- 8. オーナー専用機能（同期の土台） ---
if st.session_state.user_role == "owner":
    st.divider()
    st.subheader("👑 管理者メニュー")
    if st.button("次の問題へ移動（全員分）"):
        # ※本来はここでデータベースを更新し、プレイヤー側の画面も自動で変えます。
        # 現状はローカルでの動作確認用です。
        st.session_state.current_q_idx += 1
        st.session_state.canvas_key += 1
        st.session_state.feedback = ""
        st.rerun()