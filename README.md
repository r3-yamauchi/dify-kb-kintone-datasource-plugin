## kintone_file_datasource

**Author:** r3-yamauchi  
**Version:** 0.0.2  
**Type:** datasource

English | [Japanese](https://github.com/r3-yamauchi/dify-kb-kintone-datasource-plugin/blob/main/readme/README_ja_JP.md)

The source code of this plugin is available in the [GitHub repository](https://github.com/r3-yamauchi/dify-kb-kintone-datasource-plugin).

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/r3-yamauchi/dify-kb-kintone-datasource-plugin)

### Description
This datasource plugin exposes kintone attachments as an Online Drive datasource for Dify. The plugin queries kintone records, enumerates attachment-type fields, and surfaces each file as a downloadable asset inside Dify.

### Prerequisites

1. Generate an API token for the target kintone app with **view** permission (including attachment download).  
2. Import the plugin into Dify and configure the provider credentials:  
   - `kintone_base_url`: e.g. `https://example.cybozu.com`  
   - `kintone_api_token`: 1–9 API tokens separated by commas (created in step 1)  
   - Optional defaults (`default_app_id`, `default_attachment_field_codes`, etc.) to reuse across datasources.  
3. For each datasource instance, fill in the app-specific parameters (see table below). Any provider defaults act as fallbacks when the datasource parameter is left blank.

### Datasource Parameters

| Parameter | Required | Description |
| --- | --- | --- |
| `app_id` | ✅ | Numeric kintone App ID. |
| `attachment_field_codes` | ✅ | Comma-separated list of attachment field codes to scan (e.g. `Attachment_A,Attachment_B`). |
| `kintone_api_token` | ❌ | Optional per-datasource token list (comma separated). Leave empty to reuse the provider credential or its default. |
| `query` | ❌ | Optional kintone query string. Empty value fetches records in creation order. |
| `max_records` | ❌ | Maximum number of records to scan per browse call (1–5000, default 100). |
| `debug_logging` | ❌ | When true, emits masked debug logs containing query info, record counts, and attachment field lists. |

### Configuration Example

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

### Browse Workflow (`_browse_files`)

1. Validate and normalize `attachment_field_codes`; reject empty input.  
2. Resolve `app_id`, `query`, and pagination cursors from the request/environment.  
3. Page through kintone records using `max_records` as the per-browse ceiling. Each HTTP call respects the API’s 500-record cap, chaining requests as needed.  
4. For each record, collect attachments from the specified fields and emit `OnlineDriveFile` entries with composite IDs `<record_id>:<file_key>`.  
5. Return `OnlineDriveBrowseFilesResponse` with `is_truncated` and `next_page_parameters` set when more data remains (supports attachment-level cursoring).

### Download Workflow (`_download_file`)

1. Split the requested file ID into `record_id` and `file_key`.  
2. Fetch the record via `get_record` and locate the attachment within the allowed field codes.  
3. Download the binary through `k/v1/file.json` and emit a blob message with metadata (`file_name`, `mime_type`, `size`, plus kintone identifiers such as `record_id`, `app_id`, `field_code`, `bucket_id`). Downstream nodes treat it exactly like the Google Drive datasource output.  

## Privacy Policy

This plugin only collects the following necessary information for interacting with kintone:

1. kintone domain, app ID, and API token with appropriate permissions
2. User-provided query parameters for filtering records and selecting fields
3. User-provided record data for adding or updating records

This information is used solely for retrieving records from the specified kintone app and will not be used for other purposes or shared with third parties.

Data retrieval uses kintone's official REST API. For related privacy policies, please refer to: [cybozu.com Terms of Use](https://www.cybozu.com/jp/terms/).

### Data Storage

- The plugin does not store data locally
- All data is sent to the specified knowledge base via the Dify API
- API URI is managed by Dify's plugin system. API secrets should be injected at runtime through Dify workflows or environment secrets so they are not persisted with the provider configuration.

### Security

- API communications are encrypted via HTTPS
- API secrets are not stored by the provider; inject them via secure workflow variables or environment secrets at execution time.

### Third-Party Disclosure

This plugin does not send data to any third parties other than the Dify API endpoint specified by the user.

## License

This project is released under the [MIT License](LICENSE).

**"kintone" is a registered trademark of Cybozu, Inc.**

The information provided here is for reference only. Support is not available for individual environments. We are unable to respond to inquiries about configuration details or cases where the plugin does not work in your environment.
