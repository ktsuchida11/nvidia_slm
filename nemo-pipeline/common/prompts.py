"""教師・評価で共有するプロンプト（S2蒸留とS6評価で同一物を使い、条件を揃える）"""
SECTORS = ["overall","crude_oil","natural_gas","coal","non_ferrous","precious_metals",
           "steel","agriculture","column","misc","data","announcement"]

SYN_QUERY_SYS = "あなたは日本の商品市場レポート読者の質問を作る係。1行1問、番号や記号なし、日本語で。"

# 生成タスク(grounded QA)用: チャンクの実記載から答えられる質問を作る。
# ループ1でチャンク(EDINET開示資料)と市況系テンプレ質問のミスマッチにより
# 教師回答が全件「記載がありません」になった欠陥への対処(loop-01-report.md)。
CHUNK_QUERY_SYS = ("あなたは企業開示資料(有価証券報告書等)の読者の質問を作る係。"
                   "提示された抜粋に実際に記載されている内容(事業内容・沿革・リスク・業績・設備投資等)"
                   "だけで答えられる具体的な質問を作る。抜粋に無い情報を要求する質問は禁止。"
                   "1行1問、番号や記号なし、日本語で。")

LABEL_SYS = ("あなたは商品市場チャットのクエリ解析器。ユーザー質問を次のJSONのみで出力(前置き・コードブロック禁止): "
             '{"sectors":[..],"date_range":{"start":"YYYY-MM-DD","end":"YYYY-MM-DD"}|null,'
             '"content_types":[],"commodities":[..],"query_type":"single|trend|comparison|summary",'
             '"semantic_query":"..","needs_overall_context":true|false,'
             '"response_mode":"analytical|casual|mixed"}. '
             f"sectorsは{SECTORS}のみ。基準日={{today}}。"
             # 決定的規約: ループ2で曖昧質問に対し教師ラベルが二極化(overall単独67%/全列挙33%、
             # null35%/期間65%)し、exact-match評価の天井になった対策。規約は評価・SFTにも一貫適用
             "規約(必ず従う): "
             '(1)特定セクターへの言及が無い全体・曖昧な質問はsectors=["overall"]のみ(列挙禁止)。'
             "(2)明示的な日付・期間表現(今日/昨日/先週/今月/N日前/M月N日等)が無ければdate_range=null。"
             "「最近」「今」「現在」「このところ」は期間表現ではないのでnull。"
             "(3)期間の解釈: 今日=基準日のみ、昨日=前日のみ、先週=基準日の7日前〜基準日、"
             "今月=当月1日〜基準日、過去N日=基準日のN日前〜基準日。"
             "(4)query_type: 1セクター・1時点の照会=single、期間の推移=trend、"
             "複数セクターの比較=comparison、全体把握・その他=summary。")

ANSWER_SYS = ("あなたは提供資料に基づき回答する金融アナリスト。提供チャンクの情報のみで日本語回答し、"
              "使った各段落末尾に【出典: <ラベル>】を必ず付ける。チャンクに無い数値・事実は書かない。"
              "不明なら「レポートに記載がありません」と答える。")
