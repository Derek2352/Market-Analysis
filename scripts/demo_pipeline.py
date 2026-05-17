"""Full pipeline demo: synthetic data -> embed -> cluster -> diag."""
import sys, json, os, hashlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from datetime import datetime, timezone
import numpy as np

now = datetime.now(timezone.utc)
data_dir = Path("data/synthetic_demo")
data_dir.mkdir(parents=True, exist_ok=True)
db_path = data_dir / "demo.duckdb"

def _hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()

# ── Synthetic MTR Mobile posts (70 posts, 3 themes) ────────────────────────
all_posts = [
    # Pricing / Payment (34 posts)
    ("reddit_old", "MTR Mobile app payment always fails", "Tried to pay via Alipay in the app, got stuck on loading screen 3 times. Had to use Octopus card at the gate instead.", "en"),
    ("reddit_old", "Fare is cheaper than bus now?", "Compared my monthly commute. MTR fare adjustment actually made it cheaper than KMB for my route.", "en"),
    ("reddit_old", "Bought monthly pass through app", "The new monthly pass purchase flow on MTR Mobile is surprisingly smooth. Took less than 2 minutes.", "en"),
    ("reddit_old", "Hidden surcharge on MTR Mobile", "Be careful. When you top up Octopus through the app there is a 2 dollar handling fee they do not show upfront.", "en"),
    ("reddit_old", "Why does MTR Mobile charge differently from the gate price?", "The app showed 14.5 but the gate deducted 15.2. Not the first time this happened.", "en"),
    ("reddit_old", "MTR tourist day pass vs app purchase", "Tourist here. Bought the day pass through MTR Mobile. Way easier than queueing at the counter.", "en"),
    ("reddit_old", "Apple Pay integration broken?", "MTR Mobile used to support Apple Pay Express Transit. After the latest update, it prompts for Face ID every time.", "en"),
    ("reddit_old", "Fare subsidy collection through app", "The government transport subsidy is now redeemable through MTR Mobile. Just tap one button. Actually good UX.", "en"),
    ("reddit_old", "Octopus auto top-up vs manual through MTR Mobile", "Auto top-up from bank is more reliable than using MTR Mobile manual top-up. Had 3 failed transactions this month.", "en"),
    ("reddit_old", "Payment options too limited", "Only AlipayHK and WeChat Pay. What about FPS or credit card? MTR Mobile needs more payment channels.", "en"),
    ("lihkg", "MTR Mobile 買月票慳到錢", "用咗個月票一個月，成個月交通費慳咗差唔多兩百。真心推薦。", "yue"),
    ("lihkg", "八達通增值失敗好忟", "MTR Mobile 增值八達通成日出error，試咗五次先得。", "yue"),
    ("lihkg", "交通津貼終於可以喺app拎", "之前要去便利店拍卡，而家MTR Mobile一鍵搞掂。好方便。", "yue"),
    ("reddit_old", "MTR fare increase 2026", "Another 3.1 percent fare increase this year. At what point does it become unaffordable for regular HKers?", "en"),
    ("reddit_old", "Student discount through app", "Applied for student travel scheme via MTR Mobile. Uploaded student ID photo and got approved in a day.", "en"),
    ("lihkg", "港鐵加價3%真心貴", "年年加價，人工又唔見年年加。MTR 係咪當市民係提款機？", "yue"),
    ("lihkg", "月票vs八達通邊個抵啲", "計過條數，如果你每日搭超過25蚊，買月票係抵嘅。MTR Mobile有得比較。", "yue"),
    ("reddit_old", "MTR Mobile wallet top-up fee", "Just realized they charge 2 dollars per top-up through the app. That adds up if you top up weekly.", "en"),
    ("lihkg", "支付寶畀錢成日error", "用支付寶增值八達通次次都timeout，好垃圾。", "yue"),
    ("reddit_old", "Cross-border Octopus on MTR Mobile", "Can I use the Shenzhen cross-border Octopus through MTR Mobile? The app only shows regular Octopus.", "en"),
    ("lihkg", "學生優惠申請好快批", "網上申請學生乘車優惠，一個禮拜就批咗。比以前快好多。", "yue"),
    ("reddit_old", "Comparing HK MTR fares to other cities", "HK MTR is actually cheap compared to London Underground or Tokyo Metro. But service quality is worse.", "en"),
    ("lihkg", "MTR app成日彈app", "每次想check車費都會彈app，特別係update咗iOS之後。", "yue"),
    ("reddit_old", "Is the MTR City Saver worth it?", "MTR City Saver costs 435 for 40 rides. If you only use MTR on weekends, not worth it.", "en"),
    ("lihkg", "交通津貼門檻太高", "要一個月使超過400先有津貼，我只係搭去返工都唔夠數。", "yue"),
    ("reddit_old", "MTR Mobile dark pattern in payment", "They make one-time ticket default instead of Octopus payment. Classic dark pattern to charge more.", "en"),
    ("lihkg", "八達通自動增值好過人手", "用人手增值成日都唔記得，自動增值銀行搞掂晒，方便好多。", "yue"),
    ("reddit_old", "MTR point system in app", "The new MTR points program in the app gives you rewards. Redeemed a free ride after a month.", "en"),
    ("lihkg", "app買飛平過現場買", "喺MTR Mobile買單程飛平兩蚊，點解仲有人現場排隊買？", "yue"),
    ("reddit_old", "Refund for service delay", "MTR Mobile auto-refunded 5 dollars when my train was delayed 31 minutes. Nice surprise in the app.", "en"),
    ("lihkg", "學生八達通app申請失敗", "上載咗學生證三次都話唔清楚，MTR Mobile個相機功能好差。", "yue"),
    ("reddit_old", "Virtual Octopus on iPhone vs MTR Mobile", "Apple Wallet Octopus works at gates without opening any app. MTR Mobile still needs to be opened.", "en"),
    ("lihkg", "車費查詢功能好有用", "MTR Mobile嘅車費查詢好詳細，連轉車優惠都計埋。", "yue"),
    ("reddit_old", "MTR Mobile promo codes never work", "Every promo code I try in the app says expired. Are they even real?", "en"),
    # Service / Cleanliness (18 posts)
    ("lihkg", "港鐵廁所好污糟", "太子站個廁所臭到嘔，成地都係水。港鐵有冇人清潔㗎？", "yue"),
    ("lihkg", "扶手電梯事故", "尋日喺九龍塘站見到扶手電梯突然停咗，有個阿婆差啲跌低。好危險。", "yue"),
    ("lihkg", "交通津貼唔夠", "每個月交通津貼得300上限太少啦，我每個月搭成800。政府幾時加？", "yue"),
    ("reddit_old", "MTR staff attitude problem", "Asked a station staff for directions at Causeway Bay, the guy rolled his eyes and pointed vaguely. Not helpful.", "en"),
    ("lihkg", "車廂冷氣太凍", "大熱天時入到車廂凍到震，出返去又熱到死。溫差太大，好易病。", "yue"),
    ("lihkg", "App顯示班次唔準", "MTR Mobile話下班車兩分鐘到，結果等咗五分鐘都未見影。", "yue"),
    ("lihkg", "輪椅使用者投訴 lift太慢", "每個站得一部lift，成日要等好耐。港鐵可唔可以加多部？", "yue"),
    ("reddit_old", "Service recovery after delay", "Train delayed 15 min at Admiralty. MTR Mobile sent a push notification apologizing. At least they communicate now.", "en"),
    ("lihkg", "MTR 熱線打極唔通", "想投訴車站設施，打咗十次熱線都係留言信箱。", "yue"),
    ("lihkg", "港鐵站指示牌唔清晰", "旺角站轉車指示牌好亂，遊客一定迷路。應該學下日本咁整清楚啲。", "yue"),
    ("lihkg", "失物認領終於有進步", "唔見咗個背囊，用MTR Mobile報失，兩個鐘就搵返。效率比以前快好多。", "yue"),
    ("reddit_old", "Delayed refund process", "Applied for delay refund through MTR Mobile. Took 2 weeks to get 5 dollars back. Not worth the effort.", "en"),
    ("lihkg", "車站清潔工人好辛苦", "見到個清潔阿姐一個人抹全層，港鐵有冇請夠人？", "yue"),
    ("reddit_old", "MTR station announcements too loud", "The platform announcements are deafening at some stations. Mong Kok platform is the worst.", "en"),
    ("lihkg", "巴士站廁所仲乾淨過地鐵", "對面巴士總站個廁所乾淨過港鐵多多聲。MTR好心檢討下。", "yue"),
    ("reddit_old", "Lost property app tracking", "Reported lost wallet through MTR Mobile. Got an update every few hours until it was found. Good system.", "en"),
    ("lihkg", "港鐵站冷氣滴水", "金鐘站大堂天花冷氣成日滴水，淋濕晒啲人。反映咗幾個月都冇人理。", "yue"),
    ("reddit_old", "MTR customer service chatbot useless", "Tried the in-app chatbot for a complaint. Looped the same 3 responses. Just let me talk to a human.", "en"),
    # Infrastructure / Crowding (18 posts)
    ("reddit_old", "South Island Line still not extended", "Been waiting 5 years for SIL to extend to Aberdeen. When will it happen?", "en"),
    ("lihkg", "觀塘線逼爆", "朝早八點觀塘線迫到爆，等三班車先上到。起咗咁多條新線都冇幫助。", "yue"),
    ("lihkg", "屯馬線全線通車後方便咗好多", "以前由屯門去馬鞍山要轉三次車，而家一程過。屯馬線真係改變咗我嘅生活。", "yue"),
    ("reddit_old", "New train cars have better design", "The new Q-train on Kwun Tong Line has wider doors and more standing space. Finally some thought put into it.", "en"),
    ("lihkg", "東鐵線過海後人流分散咗", "東鐵過海之後，荃灣線冇咁逼。分流效果明顯。", "yue"),
    ("reddit_old", "Platform screen doors finally at all stations", "Took them decades but now every station has platform screen doors. About time.", "en"),
    ("reddit_old", "Wheelchair access at old stations", "Sham Shui Po station has no lift from street level. My grandma in wheelchair cannot use MTR there.", "en"),
    ("lihkg", "啟德站出口太少", "啟德站得兩個出口，體育園開咗之後一定唔夠用。規劃失誤。", "yue"),
    ("reddit_old", "Airport Express is overpriced", "Airport Express costs 115 one way. The same distance on Tung Chung Line is 25. Total rip-off.", "en"),
    ("lihkg", "港鐵站商舖越嚟越少", "留意到好多車站嘅商舖都執咗，得返7-11同美心。選擇越嚟越單一。", "yue"),
    ("reddit_old", "Escalator maintenance takes forever", "The escalator at exit B of Mong Kok station has been under repair for 3 months. 3 months.", "en"),
    ("lihkg", "北環線幾時起好", "北環線講咗十年，仲係得個講字。新界北居民等到花兒也謝了。", "yue"),
    ("reddit_old", "Crowding prediction in MTR Mobile", "The app now shows real-time platform crowding. Useful but sometimes laggy.", "en"),
    ("lihkg", "高鐵站同地鐵站轉車行到死", "西九龍高鐵站行去柯士甸站要成十分鐘，拖住行李行到想死。", "yue"),
    ("reddit_old", "MTR weekend maintenance closures", "Every weekend some line section is closed for maintenance. East Rail was closed for 3 weekends straight.", "en"),
    ("lihkg", "西鐵線班次太疏", "非繁忙時間西鐵線要等八分鐘一班，屯門去元朗都要等咁耐。", "yue"),
    ("reddit_old", "New station entrances opening", "Tin Hau station got a new exit that connects to Victoria Park. Makes weekend commute so much better.", "en"),
    ("lihkg", "港鐵信號系統成日故障", "今年第三次信號故障啦！成條線停咗半個鐘。MTR幾時先肯換系統？", "yue"),
]

from src.schemas.raw import RawPost
from src.schemas.enums import SignalType, SourceCategory

posts = []
for i, (src, title, body, lang) in enumerate(all_posts):
    cat = SourceCategory.FORUMS if src in ("lihkg", "reddit_old") else SourceCategory.SOCIAL
    posts.append(RawPost(
        id=f"post_{i:03d}",
        source=src,
        source_category=cat,
        region="HK",
        language="zh-Hant" if src == "lihkg" else "en",
        language_detected=lang,
        url=f"https://example.com/post_{i:03d}",
        author_hash=_hash(f"user_{i % 15}"),
        title=title,
        body=body,
        posted_at=now,
        signal_type=SignalType.OPINION,
        raw_metadata={"lang_hint": lang},
    ))

print(f"=== Step 1: Generated {len(posts)} synthetic posts ===")
en_count = sum(1 for p in posts if p.language_detected == "en")
zh_count = sum(1 for p in posts if p.language_detected == "yue")
print(f"  English: {en_count}, Cantonese: {zh_count}")

# ── Step 2: Embed ──────────────────────────────────────────────────────────
from src.pipeline.embed import EmbeddingStore

if db_path.exists():
    db_path.unlink()
store = EmbeddingStore(db_path=db_path)
store.embed_posts(posts, topic="MTR Mobile", region="HK")

# Get vectors back from DuckDB
import duckdb
con = duckdb.connect(str(db_path))
con.execute("LOAD vss;")
rows = con.execute(
    "SELECT post_id, vector FROM embeddings WHERE topic = 'MTR Mobile' ORDER BY post_id"
).fetchall()

vectors = np.array([np.array(r[1]) for r in rows])
post_ids_list = [r[0] for r in rows]

print(f"\n=== Step 2: Embedded {vectors.shape[0]} posts ===")
print(f"  Dimensionality: {vectors.shape[1]}")
print(f"  Mean vector norm: {np.linalg.norm(vectors, axis=1).mean():.3f}")

# ── Step 3: Cluster ────────────────────────────────────────────────────────
from src.pipeline.cluster import cluster_embeddings

# Build metadata maps
source_map = {p.id: p.source for p in posts}
lang_map = {p.id: p.language_detected for p in posts}
post_texts = {p.id: (p.title or "") + " " + p.body for p in posts}

result = cluster_embeddings(
    vectors, post_ids_list, "MTR Mobile", "HK",
    source_map=source_map,
    lang_map=lang_map,
    post_texts=post_texts,
)

noise_pct = result.noise_count / len(posts) * 100
print(f"\n=== Step 3: Clustering Results ===")
print(f"  Clusters found: {len(result.clusters)}")
print(f"  Noise posts: {result.noise_count} ({noise_pct:.0f}%)")

for c in result.clusters:
    print(f"\n  ── {c.cluster_id} ({c.size} posts) ──")
    print(f"    Keywords: {', '.join(c.keyword_summary[:8])}")
    srcs = ", ".join(f"{k}={v}" for k, v in c.source_distribution.items())
    print(f"    Sources: {srcs}")
    langs = ", ".join(f"{k}={v}" for k, v in c.language_distribution.items())
    print(f"    Languages: {langs}")
    # Representative posts
    for rid in c.representative_post_ids[:3]:
        post = next((p for p in posts if p.id == rid), None)
        if post:
            print(f"    Rep: \"{post.title}\"")

# Save result
result_path = data_dir / "cluster_result.json"
result_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
print(f"\nSaved clustering result to {result_path}")

store.close()
con.close()
