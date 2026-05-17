"""Generate synthetic test data with known 5-cluster structure for pipeline testing."""

import json, os, sys
from datetime import datetime, timezone

ROOT = r"C:\Users\Derek Yung\Market-Analysis"
sys.path.insert(0, os.path.join(ROOT, "src"))

from src.schemas.raw import RawPost
from src.schemas.enums import SignalType, SourceCategory

TOPIC = "MTR"
REGION = "HK"
now = datetime.now(timezone.utc)

# 5 known clusters with ~20 posts each + noise
cluster_data = [
    # Cluster 1: Price complaints (Cantonese)
    (["lihkg"]*20, [
        ("MTR票價越來越貴", "而家搭一個站都要$5，以前$3.5咋，年年加價真係頂唔順。政府應該補貼多啲。", "zh"),
        ("港鐵加價2026", "今次加價3.2%，仲貴過通脹。打工仔人工都未加，交通費就不斷加。", "zh"),
        ("MTR price increase again", "Every year MTR increases fares. This time 3.2% while inflation is only 2%. Not fair.", "en"),
        ("月票都加價", "連月票都加咗$20蚊，一個月慳得幾多？仲要成日延誤。", "zh"),
        ("expensive MTR", "HK MTR is now more expensive than Tokyo metro. How did this happen.", "en"),
        ("港鐵加價離譜", "港鐵賺咗咁多錢仲要加價，應該回饋乘客。政府監管不力。", "zh"),
        ("東鐵線票價", "東鐵過海後貴咗好多，以前搭巴士仲平過佢。", "zh"),
        ("mtr fare comparison", "Compared to Singapore MRT, HK MTR is 40% more expensive for the same distance.", "en"),
        ("加價無改善服務", "成日加價但服務又無改善，延誤照樣發生，冷氣又唔夠。", "zh"),
        ("學生半價唔夠", "學生得半價，應該全免或者$2一程啦。日日搭咁貴點頂。", "zh"),
        ("mtr should freeze fares", "MTR Corp made 15 billion profit last year. They should freeze fares for 3 years.", "en"),
        ("票價調整機制", "票價調整機制根本就係為港鐵度身訂造，永遠都係加價。", "zh"),
        ("西鐵線太貴", "屯門出九龍$25蚊，一個月成千蚊車費，打工都係為咗搭車。", "zh"),
        ("MTR fare structure broken", "The distance-based fare model doesn't work for a city this dense.", "en"),
        ("交通津貼唔夠", "政府嗰$400蚊交通津貼根本唔夠，港鐵一個月車費都過千。", "zh"),
        ("輕鐵都加價", "輕鐵都跟住加，仲要成日脫班，等車等到傻。", "zh"),
        ("mtr vs bus cost", "Taking bus is now cheaper than MTR for cross-harbour trips. KMB wins.", "en"),
        ("港鐵年報賺幾百億", "港鐵年報話賺咗158億，但係仲要加價，良心何在？", "zh"),
        ("長者$2優惠", "$2乘車優惠都縮水，長者都要捱貴車。", "zh"),
        ("cross harbour expensive", "Crossing the harbour used to be $9 now it's $14. That's a 55% increase in 5 years.", "en"),
    ]),
    # Cluster 2: Crowding / rush hour (Cantonese)
    (["lihkg"]*15 + ["reddit_old"]*5, [
        ("東鐵線逼到死", "朝早八點東鐵線逼到好似沙甸魚罐頭咁，企都無位企。", "zh"),
        ("放工時間金鐘站", "金鐘站放工時間人山人海，轉車要等三四班先上到。", "zh"),
        ("MTR rush hour hell", "Tsuen Wan line at 8:30am is literally impossible. You have to let 2-3 trains pass.", "en"),
        ("觀塘線逼爆", "觀塘線成日都逼爆，九龍灣返工真係慘。加班次啦。", "zh"),
        ("港島線早上", "港島線朝早中環方向，北角開始已經上唔到車。", "zh"),
        ("too many mainland tourists", "MTR is overcrowded because of too many mainland tourists flooding in daily.", "en"),
        ("屯馬線開通後", "屯馬線開通咗仲逼過以前西鐵，因為多咗轉車客。", "zh"),
        ("MTR needs more trains", "They should run trains every 90 seconds during peak like Tokyo does.", "en"),
        ("月台迫到跌落路軌", "繁忙時間月台迫到差啲跌咗落路軌，完全無安全意識。", "zh"),
        ("周末都迫", "依家周末都迫過以前平日，成個城市迫爆咗。", "zh"),
        ("East Rail overcrowding", "East Rail line is worse after the cross-harbour extension. 9-car trains are not enough.", "en"),
        ("地鐵站空氣不流通", "繁忙時間站內空氣好焗，成日覺得會焗暈。", "zh"),
        ("紅磡站轉車", "紅磡站轉車行好遠，成日都要跑先趕到下班車。", "zh"),
        ("sardine can MTR", "I've lived in Tokyo, London, NYC. HK MTR at peak is the worst for personal space.", "en"),
        ("荃灣線故障", "荃灣線一早故障，成個九龍交通癱瘓。港鐵有無後備方案？", "zh"),
        ("大圍站人潮", "大圍站轉車真係地獄級，三條線交匯逼死。", "zh"),
        ("mtr platform dangerous", "Platform edge is too close to the crowd. Someone will fall one day.", "en"),
        ("港鐵要檢討運力", "港鐵根本唔夠車，新車又遲到，成日話買車買咗幾年。", "zh"),
        ("旺角站轉車", "旺角站荃灣線轉觀塘線，日日都要逼餐死。", "zh"),
        ("MTR peak hour survival guide", "Tips: stand at car 8 end, board at Admiralty not Central, avoid 8:15-9:00.", "en"),
    ]),
    # Cluster 3: Service quality / delays (mixed EN/ZH)
    (["lihkg"]*10 + ["reddit_old"]*5 + ["app_store_hk"]*5, [
        ("MTR又故障", "今朝又信號故障，遲咗半個鐘返工。一個月最少兩三次。", "zh"),
        ("MTR delay again", "Third delay this month on Island Line. Signal fault. When will they fix this?", "en"),
        ("荃灣線延誤", "荃灣線又延誤，港鐵淨係識廣播話信號故障，但從來唔解釋原因。", "zh"),
        ("app review MTR mobile", "The MTR mobile app is useless during delays. No real-time updates, just generic messages.", "en"),
        ("MTR station AC broken", "It's 35 degrees and the AC in Prince Edward station is broken. Everyone sweating.", "en"),
        ("港鐵廁所好污糟", "港鐵站啲廁所永遠都係濕立立，清潔做得極差。", "zh"),
        ("升降機成日壞", "港鐵站啲升降機成日都維修中，輪椅人士點算？", "zh"),
        ("MTR escalator always broken", "Every week there's one escalator out of service. The maintenance is terrible.", "en"),
        ("港鐵客服態度差", "打去港鐵熱線投訴延誤，客服態度好差，完全唔想理你。", "zh"),
        ("cleanliness declining", "MTR used to be spotless. Now I see trash on trains daily. Standards dropped.", "en"),
        ("MTR mobile app sucks", "1 star. App crashes when checking train schedule. Useless during rush hour.", "en"),
        ("東鐵線訊號系統", "東鐵線換咗新訊號系統仲衰過舊嗰個，成日誤點。", "zh"),
        ("扶手電梯事故", "上次港鐵扶手電梯倒後行，好彩無人受傷。但係好驚。", "zh"),
        ("MTR WiFi never works", "The station WiFi is basically useless. Always connects but no internet.", "en"),
        ("港鐵無障礙設施不足", "好多舊站無升降機，推BB車嘅家長好辛苦。", "zh"),
        ("MTR app review", "App needs complete redesign. Can't even buy tickets properly.", "en"),
        ("將軍澳線慢到嘔", "將軍澳線成日都要慢駛，明明新界東去港島應該好快。", "zh"),
        ("service recovery nonexistent", "When there's a delay, no staff to help. Just pre-recorded announcements.", "en"),
        ("港鐵站指示不清", "金鐘站啲指示牌亂到爆，遊客一定蕩失路。", "zh"),
        ("MTR cleanliness review", "Trains used to be cleaned every night. Now you see stains and litter all the time.", "en"),
    ]),
    # Cluster 4: New lines / expansion (mixed)
    (["lihkg"]*8 + ["reddit_old"]*4 + ["app_store_hk"]*8, [
        ("屯馬線全線通車", "屯馬線全線通車之後，出九龍真係方便咗好多，唔使再轉車。", "zh"),
        ("East Rail cross harbour", "Finally East Rail goes to Admiralty. No more changing at Hung Hom!", "en"),
        ("北環線進度", "北環線講咗咁耐，幾時先動工？新界北發展等緊。", "zh"),
        ("Tuen Ma line review", "Best thing MTR did in 20 years. Connects NW New Territories directly to Kowloon.", "en"),
        ("東涌線延線", "東涌線延線去東涌西，等咗好多年啦。希望快啲通車。", "zh"),
        ("new station app review", "App shows new Tuen Ma line stations but routing is still wrong sometimes.", "en"),
        ("沙中線終於搞掂", "沙中線搞咗十幾年，終於通車。雖然遲咗好多但係真係方便。", "zh"),
        ("future MTR expansion", "What HK really needs is a line connecting all New Territories towns without going through Kowloon.", "en"),
        ("南港島線西段", "南港島線西段仲未有聲氣，香港仔居民等到頸都長。", "zh"),
        ("Northern Link needed", "The Northern Link connecting Kam Sheung Road to Lok Ma Chau is critical for Northern Metropolis.", "en"),
        ("啟德站好靚", "啟德站設計好靚，空間感好大，希望將來多啲新站跟呢個設計。", "zh"),
        ("MTR new trains review", "The new R-train on East Rail is so much better. Quieter, smoother, more space.", "en"),
        ("古洞站", "古洞站幾時起好？新界東北發展得好快。", "zh"),
        ("MTR app route planner", "Route planner finally shows Tuen Ma line correctly. Update was overdue.", "en"),
        ("港鐵未來路線圖", "2030年港鐵路線圖好令人期待，但係有幾多真係會準時完工？", "zh"),
        ("South Island line success", "South Island Line transformed Aberdeen. Property prices doubled. Mixed blessing.", "en"),
        ("洪水橋站", "洪水橋站應該同屯馬線同步起，而家先開始規劃太遲。", "zh"),
        ("MTR station design modern", "New stations like Hin Keng are actually beautiful. The old ones need renovation.", "en"),
        ("小蠔灣站", "小蠔灣站對東涌居民好重要，可以分流。", "zh"),
        ("light rail expansion", "Light Rail should extend to more areas. It's perfect for last-mile connections.", "en"),
    ]),
    # Cluster 5: Octopus / payment (mixed)
    (["lihkg"]*10 + ["reddit_old"]*5 + ["app_store_hk"]*5, [
        ("八達通app好難用", "八達通個app成日都connect唔到，增值又慢，不如用Alipay。", "zh"),
        ("Octopus vs Alipay", "I switched to AlipayHK for MTR. Faster top-up, better promos. Octopus is outdated.", "en"),
        ("八達通自動增值", "八達通自動增值成日失敗，銀行話無問題，八達通又話唔關佢事。", "zh"),
        ("MTR should accept credit card", "Why can't I just tap my Visa at the gate like in London or Singapore?", "en"),
        ("支付寶搭車優惠", "Alipay搭車有回贈，八達通乜都無。港鐵幾時先肯搞優惠？", "zh"),
        ("octopus app review", "App is ancient. Can't even see transaction history properly. Needs a rewrite.", "en"),
        ("八達通負錢問題", "八達通負咗錢都入到閘，但係出閘要補錢好麻煩。點解唔學日本？", "zh"),
        ("Apple Pay for MTR", "Tokyo has Suica on Apple Watch. HK still needs a physical Octopus card in 2026.", "en"),
        ("二維碼入閘好慢", "用QR code入閘成日scan唔到，後面啲人眼望望好尷尬。", "zh"),
        ("MTR fare payment future", "They should just let us use any payment method. Open loop system like TfL.", "en"),
        ("八達通負錢", "八達通負錢上限$35太少，有時唔記得增值就入唔到閘。", "zh"),
        ("octopus tourist problem", "As a tourist, getting an Octopus card is confusing. Why can't I just use my phone?", "en"),
        ("學生八達通申請", "申請學生八達通要填十幾頁表，仲要等兩個月，效率極低。", "zh"),
        ("MTR app payment review", "App payment integration is terrible. Crashes half the time when buying tickets.", "en"),
        ("八達通自動增值", "八達通自動增值收$2.5手續費，點解要俾錢？銀行轉賬都唔使。", "zh"),
        ("contactless payment needed", "MTR should take contactless cards directly. Octopus monopoly needs to end.", "en"),
        ("手機八達通", "手機八達通成日detect唔到，Samsung同iPhone都係咁。", "zh"),
        ("octopus replacement", "Lost my Octopus card. No way to recover the balance. In 2026 this is ridiculous.", "en"),
        ("八達通退款麻煩", "退八達通要親身去客務中心，仲要等7日先收到錢。", "zh"),
        ("MTR payment innovation", "Shanghai metro lets you pay with palm scan. HK MTR still uses a 1997 card system.", "en"),
    ]),
]

sources_set = []
posts = []
for i, (sources, texts) in enumerate(cluster_data):
    for j, s in enumerate(sources):
        title, body, lang = texts[j]
        src = sources[j]
        pid = f"test_{i}_{j}"
        post = RawPost(
            id=pid,
            source=src,
            source_category=SourceCategory.FORUMS,
            region=REGION,
            language=lang,
            language_detected=lang,
            url=f"https://example.com/post/{pid}",
            author_hash=f"hash_{i}_{j}",
            title=title,
            body=f"{title}\n{body}",
            posted_at=now,
            signal_type=SignalType.OPINION,
            engagement_metrics={"score": 10+i, "comments": 5},
            replies=[],
            raw_metadata={"cluster": i},
        )
        posts.append(post)

# Save to data/raw/mtr/HK/
import os
out_dir = os.path.join(ROOT, "data", "raw", "mtr", "HK")
os.makedirs(out_dir, exist_ok=True)
out_path = os.path.join(out_dir, "test_synthetic_20260517T000000Z.json")
posts_json = [p.model_dump(mode="json") for p in posts]
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(posts_json, f, ensure_ascii=False, indent=2)

print(f"Created {len(posts)} synthetic posts across 5 known clusters")
print(f"Source distribution: {len([p for p in posts if p.source == 'lihkg'])} lihkg, {len([p for p in posts if p.source == 'reddit_old'])} reddit_old, {len([p for p in posts if p.source == 'app_store_hk'])} app_store_hk")
print(f"Saved to: {out_path}")
