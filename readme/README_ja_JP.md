## kintone_file_datasource

英語版 README はプロジェクト直下の `README.md` を参照してください。

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/r3-yamauchi/dify-kb-kintone-datasource-plugin)

### 概要

このプラグインは kintone アプリレコードに添付されているファイルを Dify のオンラインストレージ型データソース（Online Drive Datasource）として公開します。
Difyのナレッジパイプラインにおいて kintoneアプリの添付ファイルをデータソースとして使用できます。

### 事前準備

1. kintone で対象アプリの API トークンを作成し、レコード閲覧権限を付与します。  
2. Dify にプラグインをインポートし、Provider 設定で以下を入力します。  
   - `kintone_base_url`: 例 `https://example.cybozu.com`  
   - `kintone_api_token`: 手順 1 で作成した API トークン。最大 9個まで カンマ区切り文字列で入力可能  
   - `app_id`: 対象 kintone アプリ ID
   - `attachment_field_codes`: 添付ファイルのフィールドコード（例: `添付ファイル, Attachment_B`）。

### 設定例

```yaml
# Provider credentials
kintone_base_url: https://example.cybozu.com
kintone_api_token: BuBNIwbRRaUvr33nWXcfUZ5VhaFsJxN0xH4NPN92, YuLjjdiOECJjV5ZDbFwh5BZoJJGDx3LtdCE1Dl7E
app_id: 999
attachment_field_codes: 添付ファイル, Attachment_B

# Datasource parameter
debug_logging: true
```

## プライバシーポリシー

このプラグインは kintone との連携に必要な以下の情報のみを収集します：

1. kintone ドメイン、アプリ ID、および適切な権限を持つ API トークン
2. レコード絞り込みおよびフィールド選択のためのユーザー提供クエリパラメータ

これらの情報は、指定された kintone アプリからレコードを取得し添付ファイルをダウンロードする目的にのみ使用され、他の目的で使用されたり第三者と共有されることはありません。

データ取得には kintone 公式 REST API エンドポイントを使用します：

- `https://{kintone-domain}/k/v1/records.json` (レコード一覧取得)
- `https://{kintone-domain}/k/v1/record.json` (単一レコード取得)
- `https://{kintone-domain}/k/v1/file.json` (ファイルダウンロード)

関連するプライバシーポリシーについては、[cybozu.com 利用規約](https://www.cybozu.com/jp/terms/)をご参照ください。

### データ保存

- プラグインはデータをローカルに保存しません
- 取得したファイルコンテンツは Dify ナレッジベースに送信され、Dify のデータ保持ポリシーに従って保存されます
- API URI は Dify のプラグインシステムによって管理されます。API シークレットは実行時に Dify ワークフローまたは環境シークレットを通じて注入されるべきであり、プロバイダー設定と共に永続化されません。

### セキュリティ

- API 通信は HTTPS で暗号化されます
- API シークレットはプロバイダーに保存されません。実行時にセキュアなワークフロー変数または環境シークレットを通じて注入してください。

### 第三者への開示

このプラグインは、ユーザーが指定した Dify API エンドポイント以外の第三者にデータを送信しません。

## ライセンス

MIT License

** 「kintone」はサイボウズ株式会社の登録商標です。

ここに記載している内容は情報提供を目的としており、個別のサポートはできません。
設定内容についてのご質問やご自身の環境で動作しないといったお問い合わせをいただいても対応はできませんので、ご了承ください。
