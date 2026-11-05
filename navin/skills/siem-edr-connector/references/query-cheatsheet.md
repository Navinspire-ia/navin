# Query cheat sheet: SPL, KQL (Sentinel and Defender), EQL/ES|QL, FQL, Wazuh

Each hunt is written for the native console. Time filters are omitted except
where the syntax differs; add `earliest=-24h` (SPL), `| where Timestamp > ago(24h)`
(KQL), `@timestamp >= now()-24h` (ES|QL), `last_behavior:>'2026-03-11'` (FQL).

## 1. Encoded PowerShell (T1059.001)

- SPL: `index=wineventlog sourcetype="XmlWinEventLog:Microsoft-Windows-Sysmon/Operational" EventCode=1 Image="*\\powershell.exe" (CommandLine="* -enc *" OR CommandLine="* -EncodedCommand *" OR CommandLine="* -e *") | stats count min(_time) as first by host, User, CommandLine`
- Defender KQL: `DeviceProcessEvents | where FileName in~ ("powershell.exe","pwsh.exe") | where ProcessCommandLine has_any ("-enc","-EncodedCommand","FromBase64String") | project Timestamp, DeviceName, AccountName, ProcessCommandLine, InitiatingProcessFileName`
- Sentinel KQL: `SecurityEvent | where EventID == 4688 | where NewProcessName endswith "powershell.exe" and CommandLine has_any ("-enc","-EncodedCommand")`
- EQL: `process where process.name in~ ("powershell.exe","pwsh.exe") and process.args : ("-enc","-EncodedCommand","*FromBase64String*")`
- ES|QL: `FROM logs-endpoint.events.process-* | WHERE process.name IN ("powershell.exe","pwsh.exe") AND process.command_line LIKE "* -enc *" | KEEP @timestamp, host.name, user.name, process.command_line`
- FQL (Falcon detects): `behaviors.filename:'powershell.exe'+behaviors.cmdline:*'-enc'*`
- Wazuh (rule-based): `rule.id:92032 OR data.win.eventdata.commandLine:*-enc*` in the Discover filter on `wazuh-alerts-*`

## 2. Office application spawning a shell (T1566.001)

- SPL: `index=sysmon EventCode=1 ParentImage IN ("*\\WINWORD.EXE","*\\EXCEL.EXE","*\\POWERPNT.EXE","*\\OUTLOOK.EXE") Image IN ("*\\cmd.exe","*\\powershell.exe","*\\wscript.exe","*\\mshta.exe","*\\rundll32.exe")`
- Defender KQL: `DeviceProcessEvents | where InitiatingProcessFileName in~ ("winword.exe","excel.exe","powerpnt.exe","outlook.exe") | where FileName in~ ("cmd.exe","powershell.exe","wscript.exe","mshta.exe","rundll32.exe")`
- EQL: `process where process.parent.name in~ ("winword.exe","excel.exe","powerpnt.exe","outlook.exe") and process.name in~ ("cmd.exe","powershell.exe","wscript.exe","mshta.exe","rundll32.exe")`
- FQL: `behaviors.parent_details.parent_cmdline:*'WINWORD.EXE'*+behaviors.filename:['cmd.exe','powershell.exe']`

## 3. LSASS memory access (T1003.001)

- SPL: `index=sysmon EventCode=10 TargetImage="*\\lsass.exe" GrantedAccess IN ("0x1010","0x1410","0x1438","0x1fffff") NOT SourceImage IN ("*\\MsMpEng.exe","*\\csrss.exe")`
- Defender KQL: `DeviceEvents | where ActionType == "LsassMemoryAccess" or (ActionType == "OpenProcessApiCall" and FileName =~ "lsass.exe") | summarize count() by DeviceName, InitiatingProcessFileName, InitiatingProcessCommandLine`
- EQL: `process where event.action == "open" and process.target.name : "lsass.exe"` (Elastic Defend `api` events: `api where process.Ext.api.name == "OpenProcess" and Target.process.name : "lsass.exe"`)
- Sentinel KQL: `SecurityEvent | where EventID == 4656 and ObjectName endswith "lsass.exe"`
- Wazuh: `rule.id:92900` (Sysmon 10 on lsass) or `data.win.eventdata.targetImage:*lsass.exe`

## 4. Pass-the-hash and explicit-credential logons (T1550.002, T1078)

- SPL: `index=wineventlog EventCode=4624 Logon_Type=9 Authentication_Package=NTLM | stats count by src_ip, user, host`
- Sentinel KQL: `SecurityEvent | where EventID == 4624 and LogonType == 9 and AuthenticationPackageName == "NTLM" | summarize count() by Account, IpAddress, Computer`
- Defender KQL: `DeviceLogonEvents | where LogonType == "Network" and Protocol == "NTLM" and AccountName !endswith "$" | summarize dcount(DeviceName) by AccountName, RemoteIP | where dcount_DeviceName > 5`
- EQL: `authentication where event.code == "4624" and winlog.event_data.LogonType == "9" and winlog.event_data.AuthenticationPackageName == "NTLM"`
- Wazuh: `data.win.system.eventID:4624 AND data.win.eventdata.logonType:9 AND data.win.eventdata.authenticationPackageName:NTLM`

## 5. Brute force then success (T1110)

- SPL: `index=wineventlog EventCode IN (4625,4624) | transaction user maxspan=10m | where eventcount > 10 AND searchmatch("EventCode=4624")`
- Sentinel KQL: `SecurityEvent | where EventID in (4625, 4624) | summarize Failures=countif(EventID==4625), Success=countif(EventID==4624), Start=min(TimeGenerated) by Account, IpAddress, bin(TimeGenerated, 10m) | where Failures > 10 and Success > 0`
- ES|QL: `FROM logs-system.security-* | WHERE event.code IN ("4625","4624") | STATS failures = COUNT(CASE(event.code == "4625", 1, NULL)), success = COUNT(CASE(event.code == "4624", 1, NULL)) BY user.name, source.ip | WHERE failures > 10 AND success > 0`
- EQL (sequence): `sequence by user.name, source.ip with maxspan=10m [authentication where event.code == "4625"] with runs=10 [authentication where event.code == "4624"]`

## 6. New scheduled task or service (T1053.005, T1543.003)

- SPL: `index=wineventlog (EventCode=4698 OR EventCode=7045) | table _time host user Task_Name Service_Name Service_File_Name`
- Defender KQL: `DeviceEvents | where ActionType in ("ScheduledTaskCreated","ServiceInstalled") | project Timestamp, DeviceName, ActionType, AdditionalFields`
- EQL: `any where event.code in ("4698","7045")` or on Sysmon: `registry where registry.path : "HKLM\\SYSTEM\\CurrentControlSet\\Services\\*\\ImagePath"`
- Sentinel KQL: `SecurityEvent | where EventID in (4698, 7045) | extend Task = extract(@"<Command>(.*?)</Command>", 1, EventData)`

## 7. Outbound connection to rare destination from a signed-binary proxy (T1218, T1071)

- SPL: `index=sysmon EventCode=3 Image IN ("*\\rundll32.exe","*\\regsvr32.exe","*\\mshta.exe","*\\msiexec.exe") Initiated=true | stats dc(host) as hosts count by DestinationIp DestinationPort Image | where hosts < 3`
- Defender KQL: `DeviceNetworkEvents | where InitiatingProcessFileName in~ ("rundll32.exe","regsvr32.exe","mshta.exe","msiexec.exe") and RemoteIPType == "Public" | summarize Hosts=dcount(DeviceName), Hits=count() by RemoteIP, RemotePort, InitiatingProcessFileName | where Hosts < 3`
- EQL: `network where process.name in~ ("rundll32.exe","regsvr32.exe","mshta.exe","msiexec.exe") and not cidrmatch(destination.ip, "10.0.0.0/8","172.16.0.0/12","192.168.0.0/16")`
- FQL (Falcon alerts v2): `product:'epp'+tactic:'Command and Control'+pattern_disposition_details.detect:true`

## 8. Shadow copy deletion and backup tampering (T1490)

- SPL: `index=sysmon EventCode=1 (CommandLine="*vssadmin*delete shadows*" OR CommandLine="*wmic*shadowcopy delete*" OR CommandLine="*wbadmin*delete catalog*" OR CommandLine="*bcdedit*recoveryenabled no*")`
- Defender KQL: `DeviceProcessEvents | where ProcessCommandLine has_any ("delete shadows","shadowcopy delete","delete catalog","recoveryenabled no","bootstatuspolicy ignoreallfailures")`
- EQL: `process where process.command_line : ("*vssadmin*delete*shadows*","*wmic*shadowcopy*delete*","*wbadmin*delete*catalog*","*bcdedit*recoveryenabled*no*")`
- Wazuh: `rule.id:92214` (Sysmon vssadmin) or `data.win.eventdata.commandLine:*shadows*`

## 9. Credential dumping tools and LOLBins by hash or name (T1003, T1218)

- SPL: `index=sysmon EventCode=1 (OriginalFileName IN ("mimikatz.exe","procdump","nanodump.x64.exe") OR Hashes="*SHA256=<hash>*")`
- Defender KQL: `DeviceProcessEvents | where SHA256 in ("<hash1>","<hash2>") or FileName in~ ("mimikatz.exe","procdump.exe","procdump64.exe")`
- ES|QL: `FROM logs-* | WHERE process.hash.sha256 IN ("<hash1>","<hash2>") OR process.pe.original_file_name IN ("mimikatz.exe","procdump")`
- FQL (Falcon): `behaviors.sha256:'<hash>'` on `/detects/queries/detects/v1`, or `/indicators/queries/devices/v1?type=sha256&value=<hash>` for host prevalence
- SentinelOne PowerQuery: `event.type = "Process Creation" and tgt.process.image.sha256 in ("<hash1>","<hash2>") | columns endpoint.name, src.process.user, tgt.process.cmdline`

## 10. DNS tunneling and long TXT queries (T1071.004, T1048.003)

- SPL: `index=dns sourcetype="stream:dns" | eval qlen=len(query) | where qlen > 60 OR record_type="TXT" | stats count dc(query) as unique by src_ip, domain | where unique > 200`
- Defender KQL: `DeviceNetworkEvents | where ActionType == "DnsConnectionInspected" | extend q = tostring(parse_json(AdditionalFields).query) | where strlen(q) > 60 | summarize dcount(q) by DeviceName, tostring(split(q, ".")[-2]) | where dcount_q > 200`
- EQL: `dns where length(dns.question.name) > 60 or dns.question.type == "TXT"`
- Sentinel KQL (DnsEvents): `DnsEvents | where strlen(Name) > 60 | summarize dcount(Name) by ClientIP, tostring(split(Name, ".")[-2]) | where dcount_Name > 200`

## 11. New local admin or group change (T1136.001, T1098)

- SPL: `index=wineventlog EventCode IN (4720,4728,4732,4756) | table _time host Subject_Account_Name Target_Account_Name Group_Name`
- Sentinel KQL: `SecurityEvent | where EventID in (4720, 4728, 4732, 4756) | project TimeGenerated, Computer, SubjectAccount, TargetAccount, TargetUserName`
- Defender KQL: `DeviceEvents | where ActionType in ("UserAccountCreated","UserAccountAddedToLocalGroup")`
- EQL: `iam where event.code in ("4720","4728","4732","4756")`

## 12. Lateral movement with PsExec, WMI, WinRM (T1021.002, T1047, T1021.006)

- SPL: `index=sysmon EventCode=1 (ParentImage="*\\WmiPrvSE.exe" OR ParentImage="*\\wsmprovhost.exe" OR Image="*\\PSEXESVC.exe")`
- Defender KQL: `DeviceProcessEvents | where InitiatingProcessFileName in~ ("wmiprvse.exe","wsmprovhost.exe") or FileName =~ "psexesvc.exe"`
- EQL (sequence): `sequence by host.id with maxspan=1m [network where destination.port == 445 and event.action == "connection_accepted"] [process where process.parent.name == "services.exe" and process.name : "PSEXESVC.exe"]`
- Wazuh: `rule.id:(92013 OR 92015)` (PsExec service, WMI spawn)

## API calls that run these queries

| Platform | Call | Notes |
|---|---|---|
| Splunk | `POST /services/search/jobs` with `search=search index=...`, `exec_mode=normal`, `earliest_time`, `latest_time`; then `GET /services/search/jobs/{sid}/results?output_mode=json&count=0` | poll `GET /services/search/jobs/{sid}` until `dispatchState=DONE`; `exec_mode=oneshot` returns results in one call for small searches |
| Elastic | `POST /<index>/_search` (KQL converted to Query DSL or `query_string`), `POST /_eql/search` with `{"query": "..."}`, `POST /_query` with `{"query": "FROM ..."}` for ES|QL | detection rules: `GET /api/detection_engine/rules/_find?per_page=100`, header `kbn-xsrf: true` |
| Sentinel | `POST https://api.loganalytics.io/v1/workspaces/{workspaceId}/query` body `{"query": "...", "timespan": "P1D"}` | bearer token scope `https://api.loganalytics.io/.default` |
| Defender | `POST https://api.securitycenter.microsoft.com/api/advancedqueries/run` body `{"Query": "..."}` | 45 calls/min, 10k rows per result; Microsoft Graph alternative `POST /security/runHuntingQuery` |
| Falcon | `GET /detects/queries/detects/v1?filter=<FQL>&limit=500` then `POST /detects/entities/summaries/GET/v1` with `{"ids": [...]}`; `GET /alerts/queries/alerts/v2?filter=<FQL>` then `POST /alerts/entities/alerts/v2` with `{"composite_ids": [...]}` | OAuth2 `POST /oauth2/token` (client credentials); Event Streams `GET /sensors/entities/datafeed/v2?appId=<id>` |
| SentinelOne | `POST /web/api/v2.1/dv/events/pq` body `{"query": "...", "fromDate": "...", "toDate": "..."}` then `GET /web/api/v2.1/dv/events/pq-ping?queryId=...` | header `Authorization: ApiToken <token>`; threats: `GET /web/api/v2.1/threats?createdAt__gte=...` |
| Wazuh | `POST /security/user/authenticate` (basic auth) returns a JWT; alerts come from the indexer `GET /wazuh-alerts-*/_search` with Query DSL | `GET /agents?status=active`, `GET /rules?search=lsass` |
