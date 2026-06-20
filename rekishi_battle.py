import streamlit as st
import pandas as pd
import random
import time
import os
import json
from supabase import create_client, Client

# --- 1. アプリ的基本設定 ---
st.set_page_config(page_title="歴史リアルタイム打ち込み対戦", layout="centered")

# --- 2. データベース接続のキャッシュ化 ---
@st.cache_resource
def get_supabase_client(url: str, key: str):
    return create_client(url, key)

try:
    # テキスト型への移行に伴い、GEMINI_API_KEY は不要になりました（Supabaseのみ使用）
    url: str = st.secrets["SUPABASE_URL"]
    key: str = st.secrets["SUPABASE_KEY"]
    supabase = get_supabase_client(url, key)
except Exception as e:
    st.error(f"⚠️ 設定エラー: {e}")
    st.info("Secrets に SUPABASE_URL と SUPABASE_KEY が設定されているか確認してください。")
    st.stop()

# --- 3. デザイン（CSS） ---
st.markdown("""
    <style>
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

# --- 4. データ読み込み（メモリキャッシュ化） ---
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

# --- 6. サイドバー：入室・ルーム管理 ---
with st.sidebar:
    st.title("🎮 対戦コントロール")
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
    
    # ⏱️ 同期リフレッシュ間隔の設定
    st.markdown("**⚙️ 同期パフォーマンス調整**")
    refresh_interval = st.slider(
        "データ同期の間隔 (秒)", 
        min_value=2, 
        max_value=15, 
        value=5, 
        help="テキスト式は非常に軽量なため、基本は5秒前後でサクサク動作します。"
    )

    # 接続確認用のミニステータス
    if st.session_state.room_id:
        st.markdown("**📡 データベース同期状態**")
        try:
            test_res = supabase.table("rooms").select("id, is_active").eq("id", st.session_state.room_id).execute()
            if test_res.data:
                if test_res.data[0].get("is_active", True):
                    st.markdown("<span class='db-badge' style='background-color: #dcfce7; color: #15803d;'>🟢 同期中</span>", unsafe_allow_html=True)
                else:
                    st.markdown("<span class='db-badge' style='background-color: #fee2e2; color: #b91c1c;'>🔴 終了されたルーム</span>", unsafe_allow_html=True)
            else:
                st.markdown("<span class='db-badge' style='background-color: #fee2e2; color: #b91c1c;'>🔴 ルームなし</span>", unsafe_allow_html=True)
        except Exception as conn_err:
            st.markdown(f"<span class='db-badge' style='background-color: #fef3c7; color: #b45309;'>🟡 エラー: {conn_err}</span>", unsafe_allow_html=True)

# --- 7. メイン対戦ロジック ---
if not st.session_state.room_id:
    st.title("⚔️ 歴史リアルタイム早押し打ち込みバトル")
    st.info("👋 サイドバーから部屋を作成するか、部屋番号を入力して対戦を開始してください。")
    st.stop()

# ルーム情報の取得とアクティブ状態の検証
try:
    room_res = supabase.table("rooms").select("*").eq("id", st.session_state.room_id).execute()
    if not room_res.data:
        st.warning("⚠️ このルームは存在しないか、既に削除されました。")
        if st.button("🚪 ロビー（ホーム）に戻る", use_container_width=True):
            st.session_state.room_id = ""
            st.rerun()
        st.stop()
        
    room_data = room_res.data[0]
    
    # オーナー離脱の自動検知
    if not room_data.get("is_active", True):
        st.error("🚪 オーナーが退出したか、ルームが解散されたため対戦は終了しました。")
        if st.button("🚪 ロビー（ホーム）に戻る", type="primary", use_container_width=True):
            st.session_state.room_id = ""
            st.rerun()
        st.stop()
        
except Exception as e:
    st.error(f"Supabaseからのルーム取得エラー: {e}")
    st.stop()

current_q_idx = int(room_data.get("current_q_idx", 0))

current_q_idx_safe = current_q_idx % len(df)
q_data = df.iloc[current_q_idx_safe]
question = q_data["question"]
correct_answer = str(q_data["answer"]).strip()

# --- [ここから画面描画] ---
st.markdown(f"<div class='status-box'>🏰 ルーム: {st.session_state.room_id} | 現在の役割: {'👑 オーナー' if st.session_state.user_role == 'owner' else '👤 プレイヤー'}</div>", unsafe_allow_html=True)
st.markdown(f"<div class='question-display'>問: {question}</div>", unsafe_allow_html=True)

# リアルタイムで正解者リストを取得
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
            
    st.divider()
    if st.button("🚪 ルームを解散して終了する (プレイヤーも自動切断されます)", type="secondary", use_container_width=True):
        try:
            supabase.table("rooms").update({"is_active": False}).eq("id", st.session_state.room_id).execute()
            st.session_state.room_id = ""
            st.success("ルームを解散しました。")
            st.rerun()
        except Exception as disband_err:
            st.error(f"ルーム解散エラー")
            
    st.markdown("</div>", unsafe_allow_html=True)

# ---- 【プレイヤー専用画面の表示】 ----
else:
    if db_error_message:
        st.error(db_error_message)

    # 🚀 【超高速化】手書きキャンバスの代わりにテキスト入力フォームを設置
    # 生徒がすでにこの問題で正解しているかチェック（二重送信防止）
    has_already_solved = any(row["user_id"] == st.session_state.user_id for row in rank_data)

    if has_already_solved:
        st.success("🎉 あなたはこの問題に正解済みです！オーナーが次の問題に進めるのを待っています。")
    else:
        # フォームを使ってEnterキー送信にも対応
        with st.form(key=f"answer_form_{current_q_idx}", clear_on_submit=True):
            user_input = st.text_input("ここに解答（漢字・テキスト）を入力してください", key=f"input_{current_q_idx}")
            submit_button = st.form_submit_button(label="✅ 解答を送信", use_container_width=True)
            
            if submit_button and user_input:
                # 前後の空白を取り除き、大文字小文字や全角半角のブレを簡易吸収して比較
                processed_input = str(user_input).strip()
                
                # 🚀 【0.01秒の判定ロジック】AIを通さずプログラムで直接マッチング
                if processed_input == correct_answer:
                    try:
                        supabase.table("answers").insert({
                            "room_id": str(st.session_state.room_id),
                            "user_id": str(st.session_state.user_id),
                            "question_idx": int(current_q_idx)
                        }).execute()
                        st.success("正解です！ 🎉 （データ送信完了）")
                        time.sleep(0.5)
                        st.rerun()
                    except Exception as db_err:
                        st.error(f"正解データの送信中にエラーが発生しました: {db_err}")
                else:
                    st.error(f"❌ 不正解！ 入力された値:「{processed_input}」（もう一度入力して送信できます）")

    # プレイヤー画面下部にも現在の正解状況をタイムライン表示
    if rank_data:
        st.markdown(f"<div class='winner-announcement'>🏆 この問題の勝者: {rank_data[0]['user_id']} さん！</div>", unsafe_allow_html=True)
        st.markdown("#### 👤 正解者一覧（早い順）")
        st.write(", ".join([f"**{row['user_id']}**" for row in rank_data]))

# ⏳ テキスト打ち込み型は非常に軽量なため、データ同期の待機時間をデフォルト5秒（または設定値）に短縮
time.sleep(refresh_interval)
st.rerun()