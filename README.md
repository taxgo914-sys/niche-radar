# ニッチレーダー

各国の検索エンジンのサジェストから、少数の人だけが検索している新しいワードを毎朝集め、スマホで見るアプリです。

- 収集: GitHub Actions が毎朝6時(日本時間)に `niche_finder.py` を実行します。PCは不要です。
- 閲覧: GitHub Pages で公開される `docs/` がアプリ本体です。ホーム画面に追加すると、普通のアプリのように開けます。

## セットアップ(スマホだけでできます。10分ほど)

1. github.com でアカウントを作り、右上の「+」→「New repository」で新しいリポジトリを作ります。
   - 名前の例: `niche-radar`
   - Public を推奨します(Actions の実行時間が無制限。Private は月2000分までで、この設定なら収まります)
2. リポジトリ画面の「Add file」→「Upload files」で、このフォルダの中身をすべてアップロードします。
   - `.github/workflows/daily.yml` はフォルダ構造ごと必要です。スマホでフォルダが上げられない場合は「Add file」→「Create new file」で、名前欄に `.github/workflows/daily.yml` と打ち、中身を貼り付けてください。
3. 「Settings」→「Pages」→ Source を「Deploy from a branch」、Branch を `main` / `/docs` にして Save。
4. 「Settings」→「Actions」→「General」→ Workflow permissions を「Read and write permissions」にして Save。
5. 「Actions」タブ →「daily-niche-words」→「Run workflow」で初回の収集を実行します(30分ほど)。
6. スマホで `https://あなたのユーザー名.github.io/niche-radar/` を開き、共有ボタン →「ホーム画面に追加」。

## 毎日探す分野のしくみ

探す出発点(シード)は、毎日自動で入れ替わります。1か国あたり10個で、次の4つから選びます。

| 出どころ | 個数 | 内容 |
|---|---|---|
| 辞書 `seeds.json` | 残り(約5個) | IT・科学・経済を厚めに、約600語から回します。20日以内に同じ語は使いません |
| Wikipedia の新着記事 | 3個 | その日に作られたIT・科学・経済の記事名。人物は除きます |
| 前日までの高ニッチ度の語 | 2個 | 直近3日で見つかった語の続きを、さらに掘ります |
| `config.json` の `seeds` | 追加 | 毎日必ず探したい語。空のままで構いません |

アプリには「IT・科学・経済・その他」のボタンが出て、分野で絞り込めます。

## 設定を変える

辞書に言葉を足すときは `seeds.json`、そのほかの設定は `config.json` を編集します。アプリの「設定」タブから、どちらも直接編集画面に飛べます。

| 設定 | 意味 |
|---|---|
| `seeds_per_day` | 1か国で1日に探すシードの数 (初期値10) |
| `category_weights` | 辞書から選ぶときの分野の重み。初期値はIT・科学・経済が各3、その他が1 |
| `wiki_seeds_per_market` | Wikipedia の新着から選ぶ数 (0で無効) |
| `snowball_seeds_per_market` | 前日までの高ニッチ度の語から選ぶ数 (0で無効) |
| `seed_cooldown_days` | 辞書の同じ語を再び使うまでの日数 |
| `prefixes_per_seed_per_day` | シードごとに毎日試す展開パターンの数。毎日違う組み合わせを回します |
| `max_requests_per_run` / `max_requests_per_market` | 1回の実行、1か国あたりの最大リクエスト数 |
| `keep_days` | アプリで遡れる日数 |
| `translate` / `translate_skip_markets` | 外国語ワードに日本語訳を付けるか / 翻訳しない国 (初期値は日本) |

`seeds.json` は、国ごとに `it` `science` `economy` `other` の4つの分野に言葉を並べた形です。

## 注意

- 1回の収集は30〜40分ほどかかります。
- 日本語訳は Google 翻訳の無料窓口(非公式)を使って収集時に付けます。訳せなかったワードは、アプリの詳細画面の「Google翻訳で見る」から確認できます。
- サジェストの取得先は各社の非公式な窓口です。仕様変更やブロックで取れなくなることがあります。各サービスの利用規約はご自身で確認してください。
- GitHub のサーバーから取得するため、国別の結果が実際の現地の利用者向けと少し違う場合があります。
- 初日はすべての語が「新語」になります。2日目以降に、本当に新しく現れた語だけが残るようになります。
