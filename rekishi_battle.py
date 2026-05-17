import streamlit as st
from streamlit_drawable_canvas import st_canvas
import pandas as pd
from PIL import Image
import random
import os
import time
import json
import urllib.request
import urllib.parse
import base64

# より安定しているレガシーなGemini SDKをインポート（環境依存のエラーを低減）
try:
    import google.generativeai as genai
    HAS_GENAI = True
except ImportError:
    HAS_GENAI = False

from supabase import create_client, Client

# --- 1. アプリの基本設定 ---
st.set_page_config(page_title="歴史・手書きリアルタイム対戦", layout="centered")

# --- 2. API & データベース初期化 ---
try:
    # Gemini API 初期化 (古い方の安定ライブラリ仕様で構成)
    if HAS_GENAI:
        genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
    
    # Supabase 接続
    url: str = st.secrets["SUPABASE_URL"]
    key: str = st.secrets["SUPABASE_KEY"]
    supabase: Client = create_client(url, key)
except Exception as e:
    st.error(f"⚠️ 初期接続設定エラー: {e}")
    st.info("StreamlitのSecrets設定を確認してください（GEMINI_API_KEY, SUPABASE_URL, SUPABASE_KEY）")
    st.stop()

# --- 3. デザイン（CSS） ---
st.markdown("""
    <style>
    .stCanvasContainer { border: 3px solid #4a4a4a; border-radius: 12px; background-color: #ffffff; }
    .status-box { padding: 10px; border-radius: 8px; margin-bottom: 15px; text-align: center; font-weight: bold; background-color: #f0f2f6; border: 1px solid #d1d5db; }
    .winner-announcement { background-color: #fff3cd; color: #856404; padding: 15px; border-radius: 10px; text-align: center; font-size: 1.5rem; margin-top: 10px; border: 2px solid #ffeeba; }
    .question-display { font-size: 1.4rem; font-weight: bold; color: #1e3a8a; padding: 15px; background: #eff6ff; border-radius: 10px; margin-bottom: 20px; border-left: 5px solid #3b82f6; }
    .ocr-badge { background-color: #e0f2fe; color: #0369a1; padding: 2px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: bold; }
    </style>
    """, unsafe_allow_html=True)

# --- 4. データ読み込み ---
@st.cache_data
def load_data():
    csv_file = "rekishi_questions.xlsx - Sheet1.csv"
    if os.path.exists(csv_file):
        encodings = ['utf-8', 'cp932', 'shift_jis']
        for enc in encodings:
            try:
                df = pd.read_csv(csv_file, header=None, names=["question", "answer"], encoding=enc)
                return df.dropna().reset_index(drop=True)
            except UnicodeDecodeError:
                continue
    return pd.DataFrame({"question": ["魏志倭人伝の『魏』は？"], "answer": ["魏"]})

df = load_data()

# --- 5. セッション状態の初期化 ---
if "user_id" not in st.session_state: 
    st.session_state.user_id = f"User_{random.randint(100, 999)}"
if "room_id" not in st.session_state: 
    st.session_state.room_id = ""
if "user_role" not in st.session_state: 
    st.session_state.user_role = "player"
if "canvas_key" not in st.session_state: 
    st.session_state.canvas_key = 0
if "is_submitting" not in st.session_state:
    st.session_state.is_submitting = False

# --- 6. サイドバー ---
with st.sidebar:
    st.title("🎮 対戦コントロール")
    st.session_state.user_id = st.text_input("あなたの名前（ID）", value=st.session_state.user_id)
    
    st.divider()
    
    mode = st.radio("役割を選択", ["プレイヤー", "オーナー"])
    if mode == "オーナー":
        pw = st.text_input("管理者パスワード", type="password")
        if pw == st.secrets.get("ADMIN_PASSWORD", "admin123"):
            st.session_state.user_role = "owner"
            if st.button("新しいルームを作成"):
                new_id = str(random.randint(1000, 9999))
                first_q_idx = random.randint(0, len(df) - 1)
                try:
                    supabase.table("rooms").insert({
                        "id": new_id, 
                        "current_q_idx": first_q_idx, 
                        "used_indices": json.dumps([first_q_idx]),
                        "is_active": True
                    }).execute()
                    st.session_state.room_id = new_id
                    st.success(f"ルーム {new_id} を作成しました！")
                except Exception as e:
                    st.error(f"ルーム作成失敗: {e}")
        else:
            st.session_state.user_role = "player"
    else:
        st.session_state.user_role = "player"
        st.session_state.room_id = st.text_input("部屋番号を入力", value=st.session_state.room_id)
    
    st.divider()
    # 自動更新による画面ちらつきを防ぐためのセーフティスイッチ
    auto_refresh = st.checkbox("画面の自動更新を有効にする", value=True, help="他の人の正解状況を自動で反映します。手書き中に消える場合はオフにしてください。")

# --- 7. メインロジック ---
if not st.session_state.room_id:
    st.title("⚔️ 歴史・手書き早書きバトル")
    st.info("👋 サイドバーから部屋を作成するか、部屋番号を入力して対戦を開始してください。")
    st.stop()

# 部屋の状態取得
try:
    res = supabase.table("rooms").select("*").eq("id", st.session_state.room_id).execute()
    room_data = res.data[0] if res.data else None
    if not room_data:
        st.error("ルームが見つかりません。正しい部屋番号を入力してください。")
        st.stop()
except Exception as e:
    st.error(f"DB接続エラー: {e}")
    st.stop()

q_idx = room_data.get("current_q_idx", 0)

# 問題が切り替わったらキャンバスをリセット
if "last_q_idx" not in st.session_state:
    st.session_state.last_q_idx = q_idx
if st.session_state.last_q_idx != q_idx:
    st.session_state.canvas_key += 1
    st.session_state.last_q_idx = q_idx

q_data = df.iloc[q_idx % len(df)]
question = q_data["question"]
correct_answer = q_data["answer"]

st.markdown(f"<div class='status-box'>ルーム: {st.session_state.room_id} | 第 {q_idx + 1} 問 | プレイヤー: {st.session_state.user_id}</div>", unsafe_allow_html=True)
st.markdown(f"<div class='question-display'>問: {question}</div>", unsafe_allow_html=True)

# キャンバス表示
canvas_result = st_canvas(
    stroke_width=6, stroke_color="#000000", background_color="#ffffff",
    height=250, width=700, key=f"c_{st.session_state.room_id}_{q_idx}_{st.session_state.canvas_key}"
)

# 無料かつ登録不要の超高速オンラインOCR判定を試みるヘルパー関数
def try_free_ocr(img_obj, expected_text):
    """
    外部の無料OCR APIを使用して高速判定を試みる。
    エラーが発生した場合は絶対にクラッシュせず、Falseを返してGeminiへフォールバックする。
    """
    try:
        img_obj.save("temp_canvas.png")
        url = "https://api.ocr.space/parse/image"
        
        with open("temp_canvas.png", "rb") as image_file:
            base64_image = base64.b64encode(image_file.read()).decode('utf-8')
            
        payload = urllib.parse.urlencode({
            'apikey': 'helloworld',
            'language': 'jpn',
            'base64Image': f"data:image/png;base64,{base64_image}"
        }).encode('utf-8')
        
        req = urllib.request.Request(url, data=payload, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=3) as response:
            result = json.loads(response.read().decode('utf-8'))
            
        if result.get("OCRExitCode") == 1:
            parsed_text = result["ParsedResults"][0]["ParsedText"]
            cleaned_parsed = parsed_text.replace(" ", "").replace("\n", "").replace("\r", "")
            if expected_text in cleaned_parsed:
                return True, "OCR"
        return False, "NotDetected"
    except:
        # 外部APIのエラーは完全に黙殺してメイン処理を止めない
        return False, "Error"

# 回答送信ボタン
if st.button("回答を送信", type="primary", use_container_width=True):
    if canvas_result.image_data is not None:
        st.session_state.is_submitting = True # リロード競合を防ぐフラグをオン
        with st.spinner("採点中..."):
            try:
                # 透明度チャネルを白背景に変換
                raw_img = Image.fromarray(canvas_result.image_data.astype('uint8'))
                img = Image.new("RGB", raw_img.size, (255, 255, 255))
                img.paste(raw_img, mask=raw_img.split()[3])
                
                # --- 第一段階: 無料OCRでの高速無制限判定 ---
                ocr_success, ocr_msg = try_free_ocr(img, correct_answer)
                
                if ocr_success:
                    st.success(f"正解です！ 🎉 (高速OCRにより即時判定しました)")
                    supabase.table("answers").insert({
                        "room_id": st.session_state.room_id, 
                        "user_id": st.session_state.user_id, 
                        "question_idx": q_idx
                    }).execute()
                else:
                    # --- 第二段階: OCRでダメな場合はGeminiへ安全にフォールバック ---
                    if HAS_GENAI:
                        prompt = f"歴史問題: {question}\n期待される解答漢字: {correct_answer}\n\n手書き画像にその文字が正しく書かれているか厳格に判定してください。正解の場合は必ず『正解』という単語を含めて回答してください。"
                        
                        success = False
                        # 負荷の低いモデルから順に実行してレートリミットを回避
                        for model_name in ['gemini-1.5-flash-8b', 'gemini-1.5-flash']:
                            try:
                                model = genai.GenerativeModel(model_name)
                                ai_res = model.generate_content([img, prompt])
                                if ai_res and ai_res.text:
                                    if "正解" in ai_res.text:
                                        st.success(f"正解です！ (AI判定: {ai_res.text})")
                                        supabase.table("answers").insert({
                                            "room_id": st.session_state.room_id, 
                                            "user_id": st.session_state.user_id, 
                                            "question_idx": q_idx
                                        }).execute()
                                    else:
                                        st.error(f"不正解です (AI判定: {ai_res.text})")
                                    success = True
                                    break
                            except Exception as model_err:
                                if "429" in str(model_err):
                                    continue # 次のモデルを試す
                                else:
                                    st.warning(f"モデル {model_name} でエラー: {model_err}")
                        
                        if not success:
                            st.error("⚠️ AIの利用制限（リクエスト超過）に達しました。文字をより大きく、はっきりと書いて、15秒ほど置いてから再度お試しください。")
                    else:
                        st.error("Gemini SDKが利用できません。システム管理者にお問い合わせください。")

            except Exception as e:
                st.error(f"判定中に予期せぬエラーが発生しました。もう一度お試しください。({e})")
            finally:
                st.session_state.is_submitting = False

# 勝者表示
try:
    ans_res = supabase.table("answers").select("user_id").eq("room_id", st.session_state.room_id).eq("question_idx", q_idx).order("solved_at", descending=False).execute()
    if ans_res.data:
        st.markdown(f"<div class='winner-announcement'>🏆 勝者: {ans_res.data[0]['user_id']} さん！</div>", unsafe_allow_html=True)
except: 
    pass

# オーナー操作：ランダム出題
if st.session_state.user_role == "owner":
    st.divider()
    if st.button("➡️ 次のランダム問題へ"):
        try:
            used_indices = json.loads(room_data.get("used_indices", "[]")) if room_data.get("used_indices") else []
            all_indices = list(range(len(df)))
            remaining = [i for i in all_indices if i not in used_indices]
            
            if not remaining:
                remaining = all_indices
                used_indices = []
                
            next_q_idx = random.choice(remaining)
            used_indices.append(next_q_idx)
            
            supabase.table("rooms").update({
                "current_q_idx": next_q_idx,
                "used_indices": json.dumps(used_indices)
            }).eq("id", st.session_state.room_id).execute()
            
            st.rerun()
        except Exception as e:
            st.error(f"問題更新エラー: {e}")

# 競合のない安全な自動リフレッシュ（判定処理中や、自動更新が無効な時はリフレッシュしない）
if auto_refresh and not st.session_state.is_submitting:
    time.sleep(5)
    st.rerun()