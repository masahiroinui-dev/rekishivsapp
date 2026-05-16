import streamlit as st
from streamlit_drawable_canvas import st_canvas
import pandas as pd
from PIL import Image
import random
import os
import time
from google import genai
from supabase import create_client, Client

# --- 1. アプリの基本設定 ---
st.set_page_config(page_title="歴史・手書きリアルタイム対戦", layout="centered")

# --- 2. API & データベース初期化 ---
try:
    # Gemini API
    genai_client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])
    
    # Supabase (Secretsに SUPABASE_URL と SUPABASE_KEY を設定)
    url: str = st.secrets["SUPABASE_URL"]
    key: str = st.secrets["SUPABASE_KEY"]
    supabase: Client = create_client(url, key)
except Exception as e:
    st.error(f"⚠️ 接続設定エラー: {e}")
    st.info("StreamlitのSecrets設定を確認してください（GEMINI_API_KEY, SUPABASE_URL, SUPABASE_KEY）")
    st.stop()

# --- 3. デザイン（CSS） ---
st.markdown("""
    <style>
    .stCanvasContainer { border: 3px solid #4a4a4a; border-radius: 12px; }
    .status-box { padding: 10px; border-radius: 8px; margin-bottom: 15px; text-align: center; font-weight: bold; background-color: #f0f2f6; border: 1px solid #d1d5db; }
    .winner-announcement { background-color: #fff3cd; color: #856404; padding: 15px; border-radius: 10px; text-align: center; font-size: 1.5rem; margin-top: 10px; border: 2px solid #ffeeba; }
    .question-display { font-size: 1.4rem; font-weight: bold; color: #1e3a8a; padding: 15px; background: #eff6ff; border-radius: 10px; margin-bottom: 20px; border-left: 5px solid #3b82f6; }
    </style>
    """, unsafe_allow_html=True)

# --- 4. データ読み込み ---
@st.cache_data
def load_data():
    csv_file = "rekishi_questions.xlsx - Sheet1.csv"
    if os.path.exists(csv_file):
        # 日本語CSVでよく使われるエンコーディングを順に試す
        encodings = ['utf-8', 'cp932', 'shift_jis']
        for enc in encodings:
            try:
                df = pd.read_csv(csv_file, header=None, names=["question", "answer"], encoding=enc)
                return df.dropna()
            except UnicodeDecodeError:
                continue
            except Exception as e:
                st.error(f"読み込みエラー ({enc}): {e}")
                
    return pd.DataFrame({
        "question": ["魏志倭人伝の『魏』を書けますか？", "聖徳太子が送った使節（漢字1文字）"],
        "answer": ["魏", "隋"]
    })

df = load_data()

# --- 5. セッション状態の初期化 ---
if "user_id" not in st.session_state: st.session_state.user_id = f"User_{random.randint(100, 999)}"
if "room_id" not in st.session_state: st.session_state.room_id = ""
if "user_role" not in st.session_state: st.session_state.user_role = "player"
if "canvas_key" not in st.session_state: st.session_state.canvas_key = 0

# --- 6. サイドバー：入室・ルーム管理 ---
with st.sidebar:
    st.title("🎮 対戦コントロール")
    st.write(f"あなたのID: **{st.session_state.user_id}**")
    
    mode = st.radio("役割を選択", ["プレイヤー", "オーナー"])
    if mode == "オーナー":
        pw = st.text_input("管理者パスワード", type="password")
        if pw == st.secrets.get("ADMIN_PASSWORD", "admin123"):
            st.session_state.user_role = "owner"
            if st.button("新しいルームを作成"):
                new_room_id = str(random.randint(1000, 9999))
                try:
                    supabase.table("rooms").insert({
                        "id": new_room_id,
                        "current_q_idx": 0,
                        "is_active": True
                    }).execute()
                    st.session_state.room_id = new_room_id
                    st.success(f"ルーム {new_room_id} を作成しました！")
                except Exception as e:
                    st.error(f"ルーム作成に失敗しました。詳細: {e}")
        else:
            st.session_state.user_role = "player"
    else:
        st.session_state.user_role = "player"
        st.session_state.room_id = st.text_input("部屋番号を入力", value=st.session_state.room_id)

# --- 7. メイン対戦ロジック ---
if not st.session_state.room_id:
    st.info("👋 サイドバーから部屋を作成するか、部屋番号を入力して対戦を開始してください。")
    st.stop()

# リアルタイム同期: 部屋の状態を取得
room_data = None
try:
    response = supabase.table("rooms").select("*").eq("id", st.session_state.room_id).execute()
    if hasattr(response, 'data') and response.data and len(response.data) > 0:
        room_data = response.data[0]
    else:
        st.warning(f"部屋 {st.session_state.room_id} は存在しないか、読み込めません。")
        st.stop()
except Exception as e:
    st.error(f"データベース接続エラー: {e}")
    st.stop()

# データの安全な抽出
q_idx = room_data.get("current_q_idx", 0) if room_data else 0

# 問題が切り替わった時にキャンバスをリセットするための処理
if "last_q_idx" not in st.session_state:
    st.session_state.last_q_idx = q_idx
if st.session_state.last_q_idx != q_idx:
    st.session_state.canvas_key += 1
    st.session_state.last_q_idx = q_idx

q_data = df.iloc[q_idx % len(df)]
question = q_data["question"]
correct_answer = q_data["answer"]

st.markdown(f"<div class='status-box'>ルーム: {st.session_state.room_id} | 第 {q_idx + 1} 問</div>", unsafe_allow_html=True)
st.markdown(f"<div class='question-display'>問: {question}</div>", unsafe_allow_html=True)

# 手書きキャンバス
canvas_result = st_canvas(
    stroke_width=6, stroke_color="#000000", background_color="#ffffff",
    height=250, width=700, key=f"canvas_{st.session_state.room_id}_{q_idx}_{st.session_state.canvas_key}"
)

# 回答送信
if st.button("回答を送信", type="primary", use_container_width=True):
    if canvas_result.image_data is not None:
        with st.spinner("AIが採点中..."):
            try:
                img = Image.fromarray(canvas_result.image_data.astype('uint8'))
                prompt = f"問題: {question}, 正解: {correct_answer}. 画像の手書き文字が正解なら'正解'、違うなら'不正解'と判定してください。"
                ai_res = genai_client.models.generate_content(model='gemini-2.0-flash', contents=[img, prompt])
                
                if "正解" in ai_res.text:
                    st.success("正解です！")
                    supabase.table("answers").insert({
                        "room_id": st.session_state.room_id,
                        "user_id": st.session_state.user_id,
                        "question_idx": q_idx
                    }).execute()
                else:
                    st.error(f"不正解です (AI判定: {ai_res.text})")
            except Exception as e:
                st.error(f"AIまたはDB送信エラー: {e}")

# 勝者表示
try:
    ans_res = supabase.table("answers").select("user_id").eq("room_id", st.session_state.room_id).eq("question_idx", q_idx).order("solved_at", descending=False).execute()
    if hasattr(ans_res, 'data') and ans_res.data:
        winner = ans_res.data[0]["user_id"]
        st.markdown(f"<div class='winner-announcement'>🏆 この問題の勝者: {winner} さん！</div>", unsafe_allow_html=True)
except:
    pass

# オーナー操作
if st.session_state.user_role == "owner":
    st.divider()
    if st.button("次の問題へ移動"):
        try:
            supabase.table("rooms").update({"current_q_idx": q_idx + 1}).eq("id", st.session_state.room_id).execute()
            st.rerun()
        except Exception as e:
            st.error(f"問題更新エラー: {e}")

# 同期のための自動リフレッシュ
time.sleep(5)
st.rerun()