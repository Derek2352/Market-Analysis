"""Demo: embed + cluster on synthetic MTR Mobile posts.

Prerequisites:
    pip install -e ".[dev]"

Run with:
    python -m scripts.demo2

Imports `src.pipeline` and `src.schemas` via the installed package — no
sys.path hacks. Writes a one-shot DB to data/synthetic_demo/demo2.duckdb.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np

from src.pipeline.cluster import cluster_embeddings
from src.pipeline.embed import EmbeddingStore
from src.schemas.enums import SignalType, SourceCategory
from src.schemas.raw import RawPost

data_dir = Path("data/synthetic_demo")
db_path = data_dir / "demo2.duckdb"
now = datetime.now(timezone.utc)

def _h(s): return hashlib.sha256(s.encode()).hexdigest()

# 55 synthetic MTR posts (balanced EN/ZH, 3 themes)
DATA = [
    # Pricing / Payment (22 posts)
    ("reddit_old","MTR Mobile app payment always fails","Tried to pay via Alipay in the app, got stuck on loading screen. Had to use Octopus card at gate instead.","en"),
    ("reddit_old","Fare cheaper than bus now?","Compared my monthly commute. MTR fare adjustment made it cheaper than KMB for my route.","en"),
    ("reddit_old","Bought monthly pass through app","The new monthly pass purchase flow on MTR Mobile is surprisingly smooth. Took less than 2 minutes.","en"),
    ("reddit_old","Hidden surcharge on MTR Mobile","When you top up Octopus through the app there is 2 dollar handling fee not shown upfront.","en"),
    ("reddit_old","MTR Mobile charges differently from gate","The app showed 14.5 but gate deducted 15.2. Not the first time this happened.","en"),
    ("reddit_old","Tourist day pass vs app purchase","Bought the day pass through MTR Mobile. Way easier than queueing at the counter.","en"),
    ("reddit_old","Apple Pay integration broken?","MTR Mobile used to support Apple Pay Express Transit. After latest update it prompts for Face ID every time.","en"),
    ("reddit_old","Fare subsidy collection through app","Government transport subsidy now redeemable through MTR Mobile. Just tap one button. Good UX.","en"),
    ("reddit_old","Octopus auto top-up vs manual","Auto top-up from bank is more reliable than MTR Mobile manual top-up. Had 3 failed transactions.","en"),
    ("reddit_old","Payment options too limited","Only AlipayHK and WeChat Pay. What about FPS or credit card? MTR Mobile needs more payment channels.","en"),
    ("lihkg","MTR Mobile買月票慳到錢","用左個月票一個月，成個月交通費慳左差唔多兩百。真心推薦。","yue"),
    ("lihkg","八達通增值失敗好忟","MTR Mobile增值八達通成日出error，試左五次先得。","yue"),
    ("lihkg","交通津貼終於可以喺app拎","之前要去便利店拍卡，而家MTR Mobile一鍵搞掂。好方便。","yue"),
    ("reddit_old","MTR fare increase 2026","Another 3 percent fare increase this year. When does it become unaffordable for regular HKers?","en"),
    ("lihkg","港鐵加價真心貴","年年加價，人工又唔見年年加。MTR係咪當市民係提款機？","yue"),
    ("lihkg","月票vs八達通邊個抵","計過條數，如果你每日搭超過25蚊，買月票係抵的。MTR Mobile有得比較。","yue"),
    ("lihkg","支付寶畀錢成日error","用支付寶增值八達通次次都timeout，好垃圾。","yue"),
    ("reddit_old","Comparing HK MTR fares to other cities","HK MTR actually cheap compared to London Underground or Tokyo Metro. But service quality worse.","en"),
    ("lihkg","MTR app成日彈app","每次想check車費都會彈app，特別係update左iOS之後。","yue"),
    ("lihkg","交通津貼門檻太高","要一個月使超過400先有津貼，我只係搭去返工都唔夠數。","yue"),
    ("reddit_old","MTR Mobile dark pattern in payment","They make one-time ticket default instead of Octopus payment. Classic dark pattern to charge more.","en"),
    ("lihkg","八達通自動增值好過人手","用人手增值成日都唔記得，自動增值銀行搞掂晒，方便好多。","yue"),
    # Service / Cleanliness (16 posts)
    ("lihkg","港鐵廁所好污糟","太子站個廁所臭到嘔，成地都係水。港鐵有冇人清潔？","yue"),
    ("lihkg","扶手電梯事故","尋日九龍塘站見到扶手電梯突然停左，有個阿婆差d跌低。好危險。","yue"),
    ("reddit_old","MTR staff attitude problem","Asked station staff for directions at Causeway Bay, guy rolled his eyes and pointed vaguely.","en"),
    ("lihkg","車廂冷氣太凍","大熱天時入到車廂凍到震，出返去又熱到死。溫差太大，好易病。","yue"),
    ("lihkg","App顯示班次唔準","MTR Mobile話下班車兩分鐘到，結果等左五分鐘都未見影。","yue"),
    ("lihkg","輪椅使用者投訴lift太慢","每個站得一部lift，成日要等好耐。港鐵可唔可以加多部？","yue"),
    ("reddit_old","Service recovery after delay","Train delayed 15 min at Admiralty. MTR Mobile sent push notification apologizing.","en"),
    ("lihkg","MTR熱線打極唔通","想投訴車站設施，打左十次熱線都係留言信箱。","yue"),
    ("lihkg","港鐵站指示牌唔清晰","旺角站轉車指示牌好亂，遊客一定迷路。應該學下日本咁整清楚d。","yue"),
    ("lihkg","失物認領終於有進步","唔見左個背囊，用MTR Mobile報失，兩個鐘就搵返。效率比以前快好多。","yue"),
    ("reddit_old","Delayed refund process","Applied for delay refund through MTR Mobile. Took 2 weeks to get 5 dollars back.","en"),
    ("lihkg","港鐵站冷氣滴水","金鐘站大堂天花冷氣成日滴水，淋濕晒d人。反映左幾個月都冇人理。","yue"),
    ("reddit_old","MTR customer service chatbot useless","Tried in-app chatbot for complaint. Looped same 3 responses. Just let me talk to human.","en"),
    ("reddit_old","MTR station announcements too loud","Platform announcements are deafening at some stations. Mong Kok platform is the worst.","en"),
    ("lihkg","巴士站廁所仲乾淨過地鐵","對面巴士總站個廁所乾淨過港鐵多多聲。MTR好心檢討下。","yue"),
    ("reddit_old","Lost property app tracking","Reported lost wallet through MTR Mobile. Got update every few hours until found. Good system.","en"),
    # Infrastructure / Crowding (17 posts)
    ("reddit_old","South Island Line still not extended","Been waiting 5 years for SIL to extend to Aberdeen. When will it happen?","en"),
    ("lihkg","觀塘線逼爆","朝早八點觀塘線迫到爆，等三班車先上到。起左咁多條新線都冇幫助。","yue"),
    ("lihkg","屯馬線全線通車後方便好多","以前由屯門去馬鞍山要轉三次車，而家一程過。屯馬線真係改變左我生活。","yue"),
    ("reddit_old","New train cars have better design","The new Q-train on Kwun Tong Line has wider doors and more standing space.","en"),
    ("lihkg","東鐵線過海後人流分散左","東鐵過海之後，荃灣線冇咁逼。分流效果明顯。","yue"),
    ("reddit_old","Platform screen doors finally","Took decades but now every station has platform screen doors. About time.","en"),
    ("reddit_old","Wheelchair access at old stations","Sham Shui Po station has no lift from street level. My grandma cannot use MTR there.","en"),
    ("lihkg","啟德站出口太少","啟德站得兩個出口，體育園開左之後一定唔夠用。規劃失誤。","yue"),
    ("reddit_old","Airport Express is overpriced","Airport Express costs 115 one way. Same distance on Tung Chung Line is 25.","en"),
    ("lihkg","港鐵站商舖越來越少","留意到好多車站商舖都執左，得返便利店同美心。選擇越來越單一。","yue"),
    ("reddit_old","Escalator maintenance forever","The escalator at exit B of Mong Kok station has been under repair for 3 months.","en"),
    ("lihkg","北環線幾時起好","北環線講左十年，仲係得個講字。新界北居民等到花兒也謝了。","yue"),
    ("reddit_old","Crowding prediction in MTR Mobile","App now shows real-time platform crowding. Useful but sometimes laggy.","en"),
    ("lihkg","高鐵站轉地鐵行到死","西九龍高鐵站行去柯士甸站要成十分鐘，拖住行李行到想死。","yue"),
    ("lihkg","西鐵線班次太疏","非繁忙時間西鐵線要等八分鐘一班，屯門去元朗都要等咁耐。","yue"),
    ("reddit_old","New station entrances opening","Tin Hau station got new exit connecting to Victoria Park. Weekend commute much better.","en"),
    ("lihkg","港鐵信號系統成日故障","今年第三次信號故障！成條線停左半個鐘。MTR幾時先肯換系統？","yue"),
]

posts = []
for i, (src, title, body, lang) in enumerate(DATA):
    cat = SourceCategory.FORUMS
    posts.append(RawPost(
        id=f"post_{i:03d}", source=src, source_category=cat,
        region="HK", language="zh-Hant" if src=="lihkg" else "en",
        language_detected=lang,
        url=f"https://example.com/post_{i:03d}",
        author_hash=_h(f"user_{i%10}"), title=title, body=body,
        posted_at=now, signal_type=SignalType.OPINION,
    ))

print(f"=== Step 1: {len(posts)} synthetic posts ({sum(1 for p in posts if p.language_detected=='en')} EN, {sum(1 for p in posts if p.language_detected=='yue')} ZH) ===")

# Embed
if db_path.exists(): db_path.unlink()
store = EmbeddingStore(db_path=db_path)
store.embed_posts(posts, topic="MTR Mobile", region="HK")

# Get vectors
con = duckdb.connect(str(db_path))
con.execute("LOAD vss;")
rows = con.execute("SELECT post_id, vector, source FROM embeddings WHERE topic='MTR Mobile' ORDER BY post_id").fetchall()
vectors = np.array([np.array(r[1]) for r in rows])
post_ids_list = [r[0] for r in rows]

print(f"\n=== Step 2: Embedded {len(post_ids_list)} posts into {vectors.shape[1]} dimensions ===")

# Build metadata
source_map = {p.id: p.source for p in posts}
lang_map = {p.id: p.language_detected for p in posts}
post_texts = {p.id: (p.title or "") + " " + p.body for p in posts}

# Cluster with balanced config
config = {
    "umap": {"n_neighbors": 8, "min_dist": 0.0, "n_components": 5, "random_state": 42, "metric": "cosine"},
    "hdbscan": {"min_cluster_size": 8, "min_samples": 2},
    "outlier_threshold": 0.10,
}

result = cluster_embeddings(
    vectors, post_ids_list, "MTR Mobile", "HK",
    config=config, source_map=source_map, lang_map=lang_map, post_texts=post_texts,
)

noise_pct = result.noise_count / len(post_ids_list) * 100
print(f"\n=== Step 3: {len(result.clusters)} clusters, {result.noise_count} noise ({noise_pct:.0f}%) ===")

for c in result.clusters:
    srcs = ", ".join(f"{k}={v}" for k,v in c.source_distribution.items())
    langs = ", ".join(f"{k}={v}" for k,v in c.language_distribution.items())
    reps = c.representative_post_ids[:3]
    rep_titles = [next((p.title for p in posts if p.id==rid), "?") for rid in reps]
    print(f"\n  {c.cluster_id} ({c.size} posts): {', '.join(c.keyword_summary[:6])}")
    print(f"    Sources: {srcs}")
    print(f"    Languages: {langs}")
    print(f"    Rep: \"{rep_titles[0]}\"")

store.close()
con.close()
