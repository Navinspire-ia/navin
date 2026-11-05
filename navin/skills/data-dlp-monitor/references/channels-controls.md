# Exfiltration channels, signals and controls

## Channels

| Channel | Typical exfiltration | Telemetry | Preventive control | Detective control |
|---|---|---|---|---|
| Corporate e-mail (Exchange Online, Gmail) | attachment to a personal address, auto-forward rule to an external mailbox | Exchange `MessageTrace`, Unified Audit Log `New-InboxRule`, `Set-Mailbox -ForwardingSmtpAddress`; Google Workspace audit `email_forwarding_out_of_domain` | Purview DLP policy on Exchange location (block external recipients for Confidential labels), disable external auto-forward (`Set-RemoteDomain Default -AutoForwardEnabled $false`), Google Workspace `Block auto-forwarding` | alert on new inbox rule with external forward; attachment size per recipient domain |
| SaaS upload (Google Drive, Dropbox, WeTransfer, Mega) | archive uploaded through the browser | proxy/CASB logs (Zscaler NSS, Netskope, Defender for Cloud Apps `CloudAppEvents`), browser DLP (Chrome Enterprise, Edge with Purview) | Defender for Cloud Apps session policy `Block upload` for unsanctioned apps, URL category block for personal storage, Purview Endpoint DLP `Upload to cloud service domains` | `CloudAppEvents | where ActionType == "FileUploaded" and IsExternalUser == false | summarize sum(toint(RawEventData.ObjectSize)) by AccountDisplayName, Application` |
| USB and removable media | copy to USB disk or phone (MTP) | Windows event 6416 (new device), Sysmon 11 to `\Device\HarddiskVolume` of removable class, Defender `DeviceEvents | where ActionType in ("UsbDriveMounted","RemovableStorageFileEvent")`, macOS `DADiskAppeared` through EDR | Intune / GPO device control (`Removable Storage Access` deny write, allowlist by vendor id), BitLocker To Go required, Purview Endpoint DLP `Copy to removable USB device` | file count and bytes to removable media per user per day; first use of a device id |
| Cloud storage owned by the company (S3, Blob, GCS) | presigned URL or public bucket, cross-account copy, snapshot share | S3 server access logs, CloudTrail `GetObject` with `bytesTransferredOut`, Azure Storage diagnostic logs, GCS data access logs | bucket policies with `aws:PrincipalOrgID`, Block Public Access, `s3:PutObjectAcl` denied, Macie automated discovery, Azure `allowBlobPublicAccess=false`, GCS `publicAccessPrevention=enforced` | CloudTrail: `GetObject` count and size by principal, `PutBucketPolicy`, `ModifySnapshotAttribute` to another account, `CreatePresignedUrl` is client-side so watch `GetObject` with `x-amz-signature` from unusual IPs |
| Public LLM prompts (ChatGPT, Claude, Gemini, Copilot personal) | pasting source code, customer lists or contracts into a prompt | browser DLP, proxy URL `chatgpt.com/backend-api/conversation` POST size, Purview `Data Security Posture Management for AI`, Netskope `Generative AI` category | enterprise tenant with data-retention opt-out, block personal AI domains on the proxy, Purview Endpoint DLP `Paste to supported browsers` with sensitive data | POST body size to AI domains per user; prompts above 50 KB; sensitive label detections in Purview AI hub |
| Git commits and pushes | secrets or datasets committed, push to a personal GitHub/GitLab | GitHub audit log `git.push`, `repo.create` public, Enterprise `git.clone` for bulk clone; `gitleaks detect --source . --report-format json --report-path gitleaks.json`, `gitleaks protect --staged` as pre-commit | GitHub secret scanning with push protection, pre-commit hook with gitleaks, allowlist of remotes (`url.<base>.insteadOf` policy, proxy block of `github.com/<personal>`), repository creation restricted to the org | push to a remote outside the organisation, repository visibility change, gitleaks findings per commit author |
| Printing | printing customer lists | Windows event 307 (PrintService Operational), Universal Print audit, print server logs with page counts | pull-printing with badge, watermark with user id, Purview Endpoint DLP `Print` restriction for labeled files | pages per user per day above baseline, printing outside office hours |
| DNS | tunneling (iodine, dnscat2, dns2tcp), data in subdomains or TXT | resolver logs (Windows DNS analytical log, Unbound, Umbrella, Route 53 Resolver query logs, Sysmon 22) | resolver allowlist, block TXT and NULL to non-allowlisted domains, egress firewall allowing DNS only to the corporate resolver | label length above 52, entropy, query count per second-level domain per host above 200/day, NXDOMAIN ratio |
| Personal webmail and messaging (Gmail web, Proton, Telegram, Discord, WhatsApp Web) | attachment sent from the browser | proxy and CASB (upload POST to `mail.google.com/upload`, `cdn.discordapp.com/attachments`) | URL category block or read-only (allow browsing, block upload) through CASB session control | uploads to messaging and webmail categories by size |
| Screen capture and photo | screenshot of a dashboard, phone photo | nothing reliable in telemetry; EDR sees `SnippingTool.exe` and clipboard | watermarking, screen-capture blocking in VDI, Purview sensitivity label `Screen capture blocked` is not available: use Windows Information Protection successor (MAM) on mobile | out of band: physical and HR controls |

## Behavioural signals

| Signal | Threshold that works in practice | Why |
|---|---|---|
| Daily egress volume per user vs own baseline | z-score above 3 over at least 5 previous days, or above 10x the median | catches bulk exports, not routine work |
| New destination for a user | first upload to a domain never seen for that user in 30 days, weighted when the domain is personal storage | most incidents start with a destination the user never used |
| Off-hours and weekends | outside 07:00 to 20:00 local, weekends and public holidays | insiders act when nobody watches; attackers act in their own timezone |
| Archive then upload | `.zip`, `.7z`, `.rar`, `.gpg`, `.enc` above 10 MB created then sent within an hour; 7-Zip with `-p` password switch on the command line (T1560.001) | encryption defeats content inspection |
| Staging | large copy to `C:\Users\Public`, `%TEMP%`, `/tmp`, OneDrive sync folder before upload (T1074.001) | precedes exfiltration |
| Burst | more than 20 transfers in an hour to one destination (chunked upload) | WeTransfer and Mega chunk large files |
| Leaver and mover | any of the above within 30 days before a resignation date or after an HR mover event | the highest-yield correlation in insider cases |
| DNS tunneling | above 1 MB per day of DNS to one domain, or above 200 unique subdomains | normal DNS for one domain is kilobytes |
| Mass download before egress | SharePoint `FileDownloaded` or `FileSyncDownloadedFull` above 1 000 files in a day (Unified Audit Log) | collection precedes exfiltration (T1213.002) |
| Mail forwarding rule | `New-InboxRule` with `ForwardTo` or `RedirectTo` external | standard business-e-mail-compromise exfiltration |

`scripts/egress_baseline.py` implements the volume z-score, new destination,
off-hours, personal destination, archive, USB size, DNS volume and burst
signals on a CSV export; the HR correlation is done by hand with the HR list.

## Response playbook

1. Confirm the signal is not a sanctioned job (backup, migration, data-sharing agreement): check the change calendar and the data owner; 40 % of volume spikes are migrations.
2. Preserve evidence read-only: export the proxy, CASB, EDR and mail-trace records for the user and the window; hash the exports; record who exported them and when.
3. Qualify the data: run `scripts/classify_scan.py` on recovered copies (quarantine folder, mail attachment, git diff) to establish the tier and the data types; count data subjects.
4. Contain, after approval: disable the external forward, revoke the sharing link, suspend the OAuth token, block the destination domain, isolate the endpoint if malware is involved. Each action is a change with an owner and a rollback; account suspension of an employee involves HR and legal.
5. Assess notification duties: RGPD art. 33 notification to the CNIL within 72 hours when the breach is likely to result in a risk to data subjects, art. 34 communication to the persons when the risk is high; NIS2 art. 23 early warning in 24 hours for essential and important entities; DORA art. 19 major ICT incident report for financial entities; PCI DSS incident response to the acquirer for card data. The DPO decides, the report gives the facts: types, volume, subjects, destination, encryption state.
6. Document in the deliverable table and hand the detection gaps to the `siem-edr-connector` skill as proposed rules.
