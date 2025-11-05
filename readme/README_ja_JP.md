## kintone_file_datasource

英語版 README はプロジェクト直下の `README.md` を参照してください。

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/r3-yamauchi/dify-kb-kintone-datasource-plugin)

### 概要

このプラグインは kintone アプリに保存された添付ファイルを、Dify のオンラインストレージ型データソース（Online Drive Datasource）として公開します。レコードに紐づくファイルを Dify 上から一覧・ダウンロードできるようになり、ナレッジベースやフローで再利用できます。

### 事前準備

1. kintone で対象アプリの API トークンを作成し、レコード閲覧および添付ファイル取得権限を付与します。  
2. Dify にプラグインをインポートし、Provider 設定で以下を入力します。  
   - `kintone_base_url`: 例 `https://example.cybozu.com`  
   - `kintone_api_token`: 手順 1 で作成した API トークンを 1〜9 個、カンマ区切りで入力  
   - 任意で `default_app_id` や `default_attachment_field_codes` などを設定しておくと、データソース側で省略した際の既定値として利用できます。  
3. 各データソースごとにアプリ固有のパラメータ（下表）を入力します。省略した項目はプロバイダで設定した既定値があればそちらが使われます。

### データソース設定項目

| パラメータ | 必須 | 説明 |
| --- | --- | --- |
| `app_id` | ○ | 対象 kintone アプリ ID。 |
| `attachment_field_codes` | ○ | 添付フィールドコード（例: `Attachment_A,Attachment_B`）。 |
| `kintone_api_token` | △ | データソース単位で 1〜9 個のトークンをカンマ区切りで指定できます。空欄の場合はプロバイダ設定や既定値を使用します。 |
| `query` | △ | 任意の kintone クエリ文字列。未指定時は作成順でレコードを取得します。 |
| `max_records` | △ | 1 回の browse 呼び出しで走査するレコード数の上限（1〜5000、既定値 100）。 |
| `debug_logging` | △ | true の場合、クエリ内容や取得件数をマスク付きでログ出力します。 |

### 設定例

```yaml
# Provider credentials
kintone_base_url: https://example.cybozu.com
kintone_api_token: BuBNIwbRRaUvr33nWXcfUZ5VhaFsJxN0xH4NPN92, YuLjjdiOECJjV5ZDbFwh5BZoJJGDx3LtdCE1Dl7E
app_id: "999"
attachment_field_codes: Attachment_A,Attachment_B

# Datasource parameters
max_records: 200
debug_logging: true
```

### ファイル一覧処理（_browse_files）

1. `attachment_field_codes` を検証し、空・重複を除外します。  
2. `app_id`・`query`・ページング情報（`offset` と `attachment_cursor`）を解決し、残りレコード数を追跡しながら kintone API を繰り返し呼び出します（1 リクエスト 500 件を超える場合は自動で分割）。  
3. 各レコードの指定フィールドから添付ファイルを抽出し、`OnlineDriveFile` として `id = "<record_id>:<file_key>"` 形式で返却します。  
4. `max_keys` に達したら `is_truncated=True` を設定し、`next_page_parameters` に次回取得用の `offset` と添付カーソル（同一レコード内の添付継続位置）を保存します。  

### ファイルダウンロード処理（_download_file）

1. `request.id` を `<record_id>:<file_key>` に分割し、該当レコードを取得します。  
2. 指定したフィールド群の中から `fileKey` が一致する添付を検索します。  
3. `/k/v1/file.json` から取得したバイナリをそのまま Blob として返却し、メタ情報（`file_name`, `mime_type`, `size`, `record_id`, `app_id`, `field_code`, `bucket_id` など）を付与します。  
4. 添付が見つからない場合や API でエラーが発生した場合は `ValueError` として利用者に通知し、ログには詳細を残します。  

## ライセンス

MIT License

** 「kintone」はサイボウズ株式会社の登録商標です。

ここに記載している内容は情報提供を目的としており、個別のサポートはできません。
設定内容についてのご質問やご自身の環境で動作しないといったお問い合わせをいただいても対応はできませんので、ご了承ください。
