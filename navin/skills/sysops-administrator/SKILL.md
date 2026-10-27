---
name: sysops-administrator
description: Administer Linux, macOS, and Windows servers and VMs - systemd services, Nginx, Traefik, HAProxy, IIS, disks, memory, processes, network, certificates, backups, patches. Whitelisted read commands; dangerous ops need approval.
metadata: {"navin":{"emoji":"🖥️","category":"devops"}}
---

# SysOps Administrator

## Overview

System administration with a strict read-first whitelist. Diagnostic commands run freely; anything that changes system state is announced and, when destructive, approved first.

**Detect the OS first** (Linux, macOS, or Windows) and use the matching command set below. Never run bash-only syntax in PowerShell or vice versa.

## Engine

Drive these CLIs through the `infra` tool (providers: ansible).

1. `infra action=status` for host CLIs and contexts.
2. `infra action=check command="systemctl restart nginx"` before any service, package, firewall or user change.
3. `infra action=run`, then `infra action=verify` (service active, port listening, config test passes). Fleets: `config-management`.

- Never run these CLIs through raw `exec`: the engine applies the autonomy policy (observe / prepare / execute), asks the user for critical operations (production, deletions, IAM, sensitive network, data, costly), snapshots before mutation, verifies after and audits.
- Multi-domain missions start from `infra-orchestrator`.

## Web servers, proxies and Windows Server

```bash
nginx -t && systemctl reload nginx; journalctl -u nginx --since -1h
traefik version; curl -s localhost:8080/api/http/routers | head
haproxy -c -f /etc/haproxy/haproxy.cfg && systemctl reload haproxy; echo 'show stat' | socat stdio /run/haproxy/admin.sock
systemctl list-units --failed; systemd-analyze blame; journalctl -p err -b
```

```powershell
Get-Service | Where-Object Status -eq 'Stopped'; Get-WindowsFeature | Where-Object Installed
Get-EventLog -LogName System -EntryType Error -Newest 50; Get-HotFix | Sort-Object InstalledOn -Descending | Select -First 10
Test-NetConnection <host> -Port 443; Get-IISSite
```

Always test the config (`nginx -t`, `haproxy -c`) before reload; prefer reload over restart.

## Safe diagnostic whitelist - Linux

```bash
uptime && free -h && df -h
systemctl status <service> --no-pager && systemctl --failed
journalctl -u <service> --since "1 hour ago" --no-pager
ps aux --sort=-%mem | head -15
ss -tulpn
ip addr && ip route
dig <host> && ping -c 3 <host> && traceroute <host>
docker ps && docker logs --tail 100 <container> && docker inspect <container>
openssl s_client -connect <host>:443 -servername <host> </dev/null 2>/dev/null | openssl x509 -noout -dates
last -20 && who
```

## Safe diagnostic whitelist - macOS

```bash
uptime && vm_stat && df -h
launchctl list | grep -i <service>
log show --last 1h --predicate 'process == "<service>"' --info
ps aux -m | head -15
lsof -iTCP -sTCP:LISTEN -n -P
ifconfig && netstat -rn
dig <host> && ping -c 3 <host> && traceroute <host>
system_profiler SPSoftwareDataType SPHardwareDataType
softwareupdate --list
```

## Safe diagnostic whitelist - Windows (PowerShell)

```powershell
Get-ComputerInfo | Select-Object OsName, OsUptime, CsTotalPhysicalMemory
Get-Service <service> ; Get-Service | Where-Object Status -eq 'Stopped' | Where-Object StartType -eq 'Automatic'
Get-WinEvent -LogName System -MaxEvents 50 | Where-Object LevelDisplayName -in 'Error','Critical'
Get-Process | Sort-Object WS -Descending | Select-Object -First 15
Get-NetTCPConnection -State Listen | Select-Object LocalAddress, LocalPort, OwningProcess
Get-NetIPAddress ; Get-NetRoute
Resolve-DnsName <host> ; Test-NetConnection <host> -Port 443
Get-PSDrive -PSProvider FileSystem
Get-ChildItem Cert:\LocalMachine\My | Select-Object Subject, NotAfter
Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 10
```

## Workflow

1. Detect the OS and shell (bash/zsh vs PowerShell), then baseline: load, memory, disk, failed services, recent error logs.
2. Narrow down: correlate the failing service with its logs, ports, dependencies, and recent package/config changes.
3. Fix with the smallest action: config reload before restart, restart before reboot.
4. Verify: the service answers, the symptom is gone, nothing else broke (re-check failed services).
5. For recurring issues, leave something durable: monitoring, a scheduled check (cron / launchd / Task Scheduler), log rotation, an alert.

## Service management per OS

| Action | Linux | macOS | Windows |
|---|---|---|---|
| Status | `systemctl status <svc>` | `launchctl list \| grep <svc>` | `Get-Service <svc>` |
| Logs | `journalctl -u <svc>` | `log show --predicate 'process == "<svc>"'` | `Get-WinEvent -ProviderName <svc>` |
| Restart* | `systemctl restart <svc>` | `launchctl kickstart -k <svc>` | `Restart-Service <svc>` |

*Restart of shared services requires `human-approval`.

## Rules

- Require `human-approval` for: `rm`/`Remove-Item` outside the workspace, `shutdown`/`reboot`/`Restart-Computer`, firewall changes (`iptables`/`nft`/`pfctl`/`Set-NetFirewallRule`), volume or partition operations, user/permission changes, package removals, and stopping shared services.
- Never edit a config file without keeping a `.bak` copy and stating the rollback command.
- Certificates: report expiry dates proactively when inspecting TLS services.
- On Windows prefer PowerShell over cmd.exe; on macOS remember BSD userland flags differ from GNU (e.g. `ps aux -m`, no `free`).
