## dify-kb-kintone-datasource-plugin

**Author:** r3-yamauchi  
**Version:** 0.0.1  
**Type:** datasource

For the Japanese guide, see [README_ja_JP.md](readme/README_ja_JP.md).

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/r3-yamauchi/dify-kb-kintone-datasource-plugin)

### Description
This datasource plugin pulls records from a kintone app and syncs them into Dify as Online Document entries. Each record becomes one page: `workspace_id` is the kintone app ID, `page_id` is the record ID, and `content` is a normalized, plain-text dump of the record fields.

### How to Use

1. Create an API token for the target kintone app with **view** permission.  
2. In the provider settings, enter:  
   - `base_url`: e.g., `https://example.cybozu.com`  
   - `api_token`: the token generated in step 1  
3. For each datasource instance (each app you want to sync) configure:  
   - `app_id`: numeric kintone app ID; mapped to `workspace_id`  
   - `query`: optional kintone query string; default is creation order  
   - `max_records`: number of records per sync (1–500; default 100)  
   - `title_field`: optional field code used as the Dify page title; defaults to the record number
   - `debug_logging`: optional boolean switch; when true, the plugin emits detailed progress logs

### Data Mapping

- `workspace_id`: mirrors the provided `app_id`.  
- `page_id`: taken from the kintone record `$id`.  
- `content`: each field formatted as `FIELD_CODE: value` on separate lines. Subtables flatten into `column=value` pairs separated by `;`.

This structure keeps the kintone schema intact while making the records searchable inside Dify’s knowledge base.
