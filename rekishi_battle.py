import streamlit as st
from streamlit_drawable_canvas import st_canvas
import pandas as pd
from PIL import Image
import random
import time
import os
import json
from google import genai
from supabase import create_client, Client

# --- 1. アプリ的基本設定 ---
st.set_page_config(page_title="歴史・手書きリアルタイム対戦", layout="centered")

# --- 2. API & データベース初期化 ---
try:
    # 最新の google-genai クライアントの初期化 (requirements.txt に準拠)
    client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])
    
    # Supabase 接続設定
    url: str = st.secrets["SUPABASE_URL"]
    key: str = st.secrets["SUPABASE_KEY"]
    supabase: Client = create_client(url, key)
except Exception as e:
    st.error(f"⚠️ 設定エラー: {e}")
    st.info("Secrets に GEMINI_API_KEY, SUPABASE_URL, SUPABASE_KEY が設定されているか確認してください。")
    st.stop()

# --- 3. デザイン（CSS） ---
st.markdown("""
    <style>
    .stCanvasContainer { border: 3px solid #4a4a4a; border-radius: 12px; background-color: #ffffff; }
    .status-box { padding: 12px; border-radius: 8px; margin-bottom: 15px; text-align: center; font-weight: bold; background-color: #f0f2f6; border: 1px solid #d1d5db; font-size: 1.1rem; }
    .winner-announcement { background-color: #fff3cd; color: #856404; padding: 15px; border-radius: 10px; text-align: center; font-size: 1.6rem; margin-top: 10px; border: 2px solid #ffeeba; font-weight: bold; }
    .question-display { font-size: 1.5rem; font-weight: bold; color: #1e3a8a; padding: 18px; background: #eff6ff; border-radius: 10px; margin-bottom: 20px; border-left: 5px solid #3b82f6; }
    .owner-section { background-color: #f8fafc; border: 2px solid #e2e8f0; border-radius: 12px; padding: 20px; margin-top: 10px; }
    .rank-table { width: 100%; border-collapse: collapse; margin-top: 10px; }
    .rank-table th, .rank-table td { padding: 10px; border: 1px solid #cbd5e1; text-align: center; }
    .rank-table th { background-color: #f1f5f9; }
    .db-badge { padding: 4px 8px; border-radius: 20px; font-size: 0.8rem; font-weight: bold; }
    </style>
    """, unsafe_allow_html=True)

# --- 4. データ読み込み ---
@st.cache_data
def load_data():
    csv_file = "rekishi_questions.xlsx - Sheet1.csv"
    if os.path.exists(csv_file):
        for enc in ['utf-8', 'cp932', 'shift_jis']:
            try:
                df = pd.read_csv(csv_file, header=None, names=["question", "answer"], encoding=enc)
                return df.dropna().reset_index(drop=True)
            except:
                continue
    return pd.DataFrame({
        "question": ["魏志倭人伝の『魏』を書けますか？", "聖徳太子が送った使節（漢字1文字）"],
        "answer": ["魏", "隋"]
    })

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
if "is_processing" not in st.session_state:
    st.session_state.is_processing = False

# --- 6. サイドバー：入室・ルーム管理 ---
with st.sidebar:
    st.title("🎮 对戦コントロール")
    st.session_state.user_id = st.text_input("あなたの名前（ID）", value=st.session_state.user_id, help="対戦結果に表示される名前です")
    
    st.divider()
    
    mode = st.radio("役割を選択", ["プレイヤー", "オーナー"])
    if mode == "オーナー":
        pw = st.text_input("管理者パスワード", type="password")
        if pw == st.secrets.get("ADMIN_PASSWORD", "admin123"):
            st.session_state.user_role = "owner"
            if st.button("新しいルームを作成"):
                new_room_id = str(random.randint(1000, 9999))
                first_q_idx = random.randint(0, len(df) - 1)
                try:
                    supabase.table("rooms").insert({
                        "id": new_room_id,
                        "current_q_idx": first_q_idx,
                        "used_indices": json.dumps([first_q_idx]),
                        "is_active": True
                    }).execute()
                    st.session_state.room_id = new_room_id
                    st.success(f"ルーム {new_room_id} を作成しました！")
                except Exception as e:
                    st.error(f"ルーム作成失敗: {e}")
        else:
            st.session_state.user_role = "player"
    else:
        st.session_state.user_role = "player"
        st.session_state.room_id = st.text_input("部屋番号を入力", value=st.session_state.room_id)

    st.divider()
    
    # ⏱️ 大人数プレイ時の負荷対策設定
    st.markdown("**⚙️ 大人数向けパフォーマンス調整**")
    refresh_interval = st.slider(
        "データ同期の間隔 (秒)", 
        min_value=3, 
        max_value=15, 
        value=8, 
        help="参加人数が多いときは、この値を「8秒〜12秒」に増やすことで、サーバーの負荷を下げて動作を軽くできます。"
    )

    # 接続確認用のミニステータス
    if st.session_state.room_id:
        st.markdown("**📡 データベース同期状態**")
        try:
            # 疎通確認テスト
            test_res = supabase.table("rooms").select("id").eq("id", st.session_state.room_id).execute()
            if test_res.data:
                st.markdown("<span class='db-badge' style='background-color: #dcfce7; color: #15803d;'>🟢 接続完了（同期中）</span>", unsafe_allow_html=True)
            else:
                st.markdown("<span class='db-badge' style='background-color: #fee2e2; color: #b91c1c;'>🔴 ルームが見つかりません</span>", unsafe_allow_html=True)
        except Exception as conn_err:
            st.markdown(f"<span class='db-badge' style='background-color: #fef3c7; color: #b45309;'>🟡 エラー: {conn_err}</span>", unsafe_allow_html=True)

# --- 7. メイン対戦ロジック ---
if not st.session_state.room_id:
    st.title("⚔️ 歴史・手書き早書きバトル")
    st.info("👋 サイドバーから部屋を作成するか、部屋番号を入力して対戦を開始してください。")
    st.stop()

# ルーム情報の取得
try:
    room_res = supabase.table("rooms").select("*").eq("id", st.session_state.room_id).execute()
    if not room_res.data:
        st.error(f"ルーム {st.session_state.room_id} が見つかりません。")
        st.stop()
    room_data = room_res.data[0]
except Exception as e:
    st.error(f"Supabaseからのルーム取得エラー: {e}")
    st.stop()

current_q_idx = int(room_data.get("current_q_idx", 0))
if "last_q_idx" not in st.session_state:
    st.session_state.last_q_idx = current_q_idx

# 問題が変わったらキャンバスをクリア
if st.session_state.last_q_idx != current_q_idx:
    st.session_state.canvas_key += 1
    st.session_state.last_q_idx = current_q_idx

current_q_idx_safe = current_q_idx % len(df)
q_data = df.iloc[current_q_idx_safe]
question = q_data["question"]
correct_answer = q_data["answer"]

# --- [ここから画面描画] ---
st.markdown(f"<div class='status-box'>🏰 ルーム: {st.session_state.room_id} | 現在の役割: {'👑 オーナー' if st.session_state.user_role == 'owner' else '👤 プレイヤー'}</div>", unsafe_allow_html=True)
st.markdown(f"<div class='question-display'>問: {question}</div>", unsafe_allow_html=True)

# リアルタイムで正解者リストをSupabaseから取得 (誰が一番早かったか)
rank_data = []
db_error_message = None
try:
    ans_res = supabase.table("answers")\
        .select("user_id, solved_at")\
        .eq("room_id", str(st.session_state.room_id))\
        .eq("question_idx", int(current_q_idx))\
        .order("solved_at", desc=False)\
        .execute()
    if ans_res.data:
        rank_data = ans_res.data
except Exception as e:
    db_error_message = f"正解データの取得中にデータベースエラーが発生しました: {e}"

# ---- 【オーナー専用画面の表示】 ----
if st.session_state.user_role == "owner":
    st.markdown("### 👑 オーナー管理コンソール")
    
    if db_error_message:
        st.error(db_error_message)
    
    # 1. リアルタイム回答状況
    st.markdown("#### ⏱️ 正解者リアルタイム順位表 (早い者勝ち)")
    if rank_data:
        first_winner = rank_data[0]["user_id"]
        st.markdown(f"<div class='winner-announcement'>🏆 最速正解者: {first_winner} さん！</div>", unsafe_allow_html=True)
        
        table_html = "<table class='rank-table'><tr><th>順位</th><th>プレイヤー名</th><th>正解時刻</th></tr>"
        for i, row in enumerate(rank_data):
            try:
                time_part = row["solved_at"].split("T")[1][:12]
            except:
                time_part = row["solved_at"]
                
            medal = "🥇 " if i == 0 else "🥈 " if i == 1 else "🥉 " if i == 2 else ""
            table_html += f"<tr><td>{medal}{i+1}</td><td><strong>{row['user_id']}</strong></td><td>{time_part} (UTC)</td></tr>"
        table_html += "</table>"
        st.markdown(table_html, unsafe_allow_html=True)
    else:
        st.info("⌛ まだ正解者はいません。プレイヤーの解答を待っています...")

    # 2. 次の問題へ進めるコントロールパネル
    st.markdown("<div class='owner-section'>", unsafe_allow_html=True)
    st.subheader("⚙️ ルーム進行コントロール")
    
    with st.expander("👁️ 正解（答え）を確認する"):
        st.write(f"答え: **{correct_answer}**")
    
    if st.button("➡️ 正解者を確定して「次のランダム問題」へ移動する", type="primary", use_container_width=True):
        try:
            used_indices = json.loads(room_data.get("used_indices", "[]"))
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
            
            st.success("問題を切り替えました！自動的に再読み込みされます。")
            st.rerun()
        except Exception as e:
            st.error(f"問題更新エラー: {e}")
    st.markdown("</div>", unsafe_allow_html=True)

# ---- 【プレイヤー専用画面の表示】 ----
else:
    if db_error_message:
        st.error(db_error_message)

    canvas_result = st_canvas(
        stroke_width=6, stroke_color="#000000", background_color="#ffffff",
        height=250, width=700, key=f"canvas_{st.session_state.room_id}_{current_q_idx}_{st.session_state.canvas_key}"
    )

    if st.button("✅ 回答を送信", type="primary", use_container_width=True):
        if canvas_result.image_data is not None:
            # 同期リフレッシュの競合を防ぐフラグをセット
            st.session_state.is_processing = True
            with st.spinner("AIが採点中..."):
                try:
                    raw_img = Image.fromarray(canvas_result.image_data.astype('uint8'))
                    
                    # 🚀 【高速化の要】透過チャネルを白背景RGBに変換しつつ、解像度を350x125に縮小して送信サイズを1/4にする
                    # これにより、大人数の環境でもネットワークアップロード速度とGeminiの画像認識負荷が劇的に改善します
                    resized_raw = raw_img.resize((350, 125), Image.Resampling.LANCZOS)
                    img = Image.new("RGB", resized_raw.size, (255, 255, 255))
                    img.paste(resized_raw, mask=resized_raw.split()[3])
                    
                    prompt = (
                        f"歴史問題: {question}\n"
                        f"期待される正解（正しい文字）: {correct_answer}\n\n"
                        "【判定手順】\n"
                        "1. 画像に手書きされた文字が、期待される正解（正しい文字）と同じであるか厳格に確認してください。画数の省略、部首の間違い、別の部首の混入、誤字などはすべて「不正解」と判定してください。\n"
                        "2. 判定結果は必ず最初に「【正解】」または「【不正解】」という形式で明記してください。絶対にそれ以外の言葉から始めてはいけません。\n"
                        "3. その後、改行してからそのように判定した具体的な理由や判読された文字、アドバイスを日本語で記載してください。"
                    )
                    
                    ai_response = None
                    errors_logged = []
                    
                    for target_model in ['gemini-2.5-flash', 'gemini-1.5-flash']:
                        try:
                            ai_response = client.models.generate_content(
                                model=target_model,
                                contents=[img, prompt]
                            )
                            if ai_response and ai_response.text:
                                break
                        except Exception as model_err:
                            err_str = str(model_err)
                            if "429" in err_str:
                                try:
                                    time.sleep(1.0)
                                    ai_response = client.models.generate_content(
                                        model=target_model,
                                        contents=[img, prompt]
                                    )
                                    if ai_response and ai_response.text:
                                        break
                                except Exception as retry_err:
                                    errors_logged.append(f"{target_model} (retry): {retry_err}")
                            else:
                                errors_logged.append(f"{target_model}: {model_err}")
                    
                    if ai_response and ai_response.text:
                        if "【正解】" in ai_response.text:
                            st.success("正解です！ 🎉")
                            supabase.table("answers").insert({
                                "room_id": str(st.session_state.room_id),
                                "user_id": str(st.session_state.user_id),
                                "question_idx": int(current_q_idx)
                            }).execute()
                        else:
                            st.error(f"残念！不正解です。\n\n💡 AIからのフィードバック:\n{ai_response.text.replace('【不正解】', '').strip()}")
                    else:
                        st.error("⚠️ AIに接続できませんでした。以下を確認してください。")
                        with st.expander("詳細なシステムエラーログ"):
                            for err in errors_logged:
                                st.write(f"- {err}")
                            
                except Exception as e:
                    st.error(f"採点システムエラー: {e}")
                finally:
                    # 採点終了後に同期リフレッシュを再有効化
                    st.session_state.is_processing = False

    # プレイヤー画面下部にも現在の正解状況をタイムライン表示
    if rank_data:
        st.markdown(f"<div class='winner-announcement'>🏆 この問題の勝者: {rank_data[0]['user_id']} さん！</div>", unsafe_allow_html=True)
        st.markdown("#### 👤 正解者一覧")
        st.write(", ".join([f"**{row['user_id']}**" for row in rank_data]))

# ⏳ 大人数接続時のデータベース過負荷を防ぐため、採点処理中（is_processing = True）はスリープ＆リフレッシュをスキップ
if not st.session_state.is_processing:
    time.sleep(refresh_interval)
    st.rerun()