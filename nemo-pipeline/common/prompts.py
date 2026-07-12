"""教師・評価で共有するプロンプト（S2蒸留とS6評価で同一物を使い、条件を揃える）"""
SECTORS = ["overall","crude_oil","natural_gas","coal","non_ferrous","precious_metals",
           "steel","agriculture","column","misc","data","announcement"]

SYN_QUERY_SYS = "あなたは日本の商品市場レポート読者の質問を作る係。1行1問、番号や記号なし、日本語で。"

LABEL_SYS = ("あなたは商品市場チャットのクエリ解析器。ユーザー質問を次のJSONのみで出力(前置き・コードブロック禁止): "
             '{"sectors":[..],"date_range":{"start":"YYYY-MM-DD","end":"YYYY-MM-DD"}|null,'
             '"content_types":[],"commodities":[..],"query_type":"single|trend|comparison|summary",'
             '"semantic_query":"..","needs_overall_context":true|false,'
             '"response_mode":"analytical|casual|mixed"}. '
             f"sectorsは{SECTORS}のみ。日付なしはdate_range=null。基準日={{today}}。")

ANSWER_SYS = ("あなたはシニアコモディティアナリスト。提供チャンクの情報のみで日本語回答し、"
              "使った各段落末尾に【出典: <ラベル>】を必ず付ける。チャンクに無い数値・事実は書かない。"
              "不明なら「レポートに記載がありません」と答える。")
