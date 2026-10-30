# Lplayer Recovery Test 2 — sorting and filters

This is a test adaptation of the supplied Lplayer 1.9.6 XAPK, not a recovery of
the original Flutter source project. The original compiled Flutter code and
assets are retained. New native Java source and reproducible patch scripts are
included in the recovery kit.

## Added in Test 2

Open **Search** to use these controls on saved records:

- Sort by saved date, newest or oldest first; unknown dates appear last.
- Sort by title A–Z or Z–A.
- Filter by category, the last 7/30/365 days, unknown dates, or a custom date range.
- Tap **Dates…** to choose a custom start and end date. Both calendar dates are
  included, using the device timezone.
- Filter to records containing recognized video links/paths, combined with text
  search and the other filters.
- Each result shows its saved date. Counts distinguish displayed results from all
  matches. The best 200 matches are selected after sorting the scanned records.

These dates come from the recovered local database's `create_timestamp` or
`created_at` columns, accepting Unix seconds or milliseconds. They are **saved-record
 dates, not video publication dates**. Missing/invalid dates remain unknown.
The controls apply to the added Search screen, not the original Flutter feeds.

**100 original-app channel suggestions have not been added.** The current
cloud network policy does not allow the catalog API hosts, and the catalog
contract is still unverified. The request remains pending; no placeholder
channels or unrelated public-TV/Telegram directory was substituted.

## Included in this APK

- A **Search** button on the original app screen opens an on-device search dialog.
- Searches JSON values in the recovered `view_hub.db` tables `link_user_table`,
  `table_library`, `table_history`, and in `download.db` / `download_tasks`.
- Categories: all saved content, accounts & channels, library, history, downloads.
  Nested channel/video text can match when it is present in a saved record.
- Case-insensitive matching, Latin accent and Arabic diacritic normalization,
  and matching all query words in any order.
- Matching records can be inspected. Recognized saved video URLs can be opened
  in the native video viewer. Links requiring the original player or expired
  authentication may not play.
- Google Mobile Ads and AppLovin MAX load paths are replaced with normal failed-load
  callbacks; MAX readiness reports false and ad display/refresh paths are disabled.
  This is a binary-level change. Live ad suppression and its effect on the
  existing Flutter flow have **not** been tested on Android hardware.

## Not included / not verified

**Full online search is not implemented.** The APK does not contain a complete
channel, account, or video catalog. REA and further snapshot analysis identified
the service routes and encrypted configuration, but the actual catalog API
contract and its authentication have not been verified.

The main recovered API hosts are `api.cvibec.com` and `api.ecvibec.com`. Requests
to them are currently blocked by the cloud environment's network proxy. Both
were added to the saved network draft. The current runtime still has no custom API hosts enabled.
Review/save those settings and publish
the environment before continuing API inspection. The earlier reachable
`salami.vibecast-vistawave.com` and `vcast.vibecast-vistawave.com` endpoints did
not return a catalog. Enabling network access is a prerequisite, not proof that
the service provides a global-search or complete-enumeration API.

No Android device/emulator was available. Startup, original-screen navigation,
real saved-data compatibility, playback, and backend compatibility under the
new package/signature remain unverified. The original Flutter code is obfuscated;
decoded object metadata and assembly are not a rebuildable Dart source project.

## Install and data

- File: `Lplayer-1.9.6-Recovery-Test-2.apk`
- Version: `1.9.6-recovery-test2` (version code 203)
- Package: `com.vibecast.vistata.recoverytest`
- App label: **Lplayer Recovery Test**
- Requires Android 7.0 / API 24 or newer; ARM64 and ARMv7 libraries are included.
- Uses the public Android debug signing certificate; intended for testing only.
- Installs separately from the original app. **Do not uninstall the original.**
- Installing Test 2 over the previous recovery test version preserves its local
  storage through the normal Android update process. Do not uninstall the prior
  test app first.
- A fresh test-app installation has its own empty storage. It cannot access the original app's
  private databases. Browse/import/add accounts in the test installation before
  expecting local search results. No original records were migrated.
- Its Application uses the standard Android startup path instead of the
  original Play-distribution license check, because the debug signature differs.
  Billing entitlements were not modified or granted.

Search checks up to 50,000 saved records per query and displays up to 200 matches.
Large/deep fields are bounded; limited coverage, unreadable records and database
read errors are reported in the search screen. No records are uploaded by the
new local-search code.

## Validation

- Original three-part archive reassembled and every ZIP entry passed CRC testing.
- REA verified package identity/version and identified the Flutter snapshot.
- Native Java compiled and converted to DEX for minimum API 24.
- 48 checks passed: 34 date/filter/sorting checks plus the original 14 search
  behavior checks. These cover seconds/milliseconds, invalid/unknown dates,
  recent/custom range boundaries, ordering, ties, selecting late matches before
  truncation, nested JSON, account/channel/video
  matching, accents, Arabic diacritics, signed media URLs, malformed JSON,
  bounded fields and exclusion of recognized authentication fields.
- APK rebuild, alignment and APK Signature Scheme v2/v3 verification passed.
- Package checks confirmed the separate test identity and standalone manifest.
- These checks do not replace device testing or validate the missing online search.

## Rebuild the test adaptation

On Linux with Java 21 including its compiler module, Python 3, curl, unzip and
sha256sum, extract the recovery kit to a working folder and put the original
`lplayer-1-9-6.xapk` in that folder. Then run:

```sh
bash rebuild/prepare.sh
bash rebuild/build.sh
```

The scripts prepare the known tool versions, verify download hashes, extract and
merge the original split package, apply the patches, run search tests, build and
debug-sign the APK. Tool download hashes pin the files used in this session;
Google SDK archives were also checked against Google's repository metadata.
The kit does not bundle the original XAPK or third-party build tools.

Original XAPK SHA-256:
`6c54ff51d74724d411f4cb6d245d06f7fa38902f70bce68560dedb9f7e01d365`
