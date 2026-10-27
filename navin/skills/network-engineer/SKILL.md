---
name: network-engineer
description: Design and troubleshoot networks - cloud VPC/VNet, subnets, routing, VPN, NAT, DNS, firewalls and security groups, load balancers - and debug hosts with ip, ss, nft, dig, tcpdump, openssl. Use for connectivity issues, network design and firewall/DNS/TLS changes.
metadata: {"navin":{"emoji":"🕸️","category":"devops"}}
---

# Network Engineer

## Overview

You reason layer by layer (L3 routing, L4 ports/firewalls, DNS, TLS, L7) and prove each hop with a command. Network changes are critical by default and go through the `infra` engine.

## When to use

- "Service X cannot reach Y", timeouts, DNS failures, certificate errors.
- Designing VPC/VNet layouts, peering, transit, VPN, private endpoints.
- Firewall, security group, NACL, route or DNS changes.

## Engine

1. `infra action=discover provider=aws|gcp|azure` for network resources; `infra action=graph op=path node=<src> target=<dst>` to see the expected path.
2. `infra action=graph op=impact node=<sg|subnet|route-table>` before any change.
3. `infra action=check command="..."` - firewall and route changes come back as `network` and usually critical.
4. `infra action=plan` then `infra action=run` and `infra action=verify` (connectivity probe).

## Workflow

1. Define source, destination, port, protocol and expected path.
2. Check from the source: resolution (`dig`), route (`ip route get`), reachability (`nc -vz`, `curl -v`), TLS (`openssl s_client`).
3. Check the controls on the path: security groups, NACLs, firewall rules, NetworkPolicies, host firewall (`nft list ruleset`).
4. Capture when unclear: `tcpdump` on both ends.
5. Fix the narrowest rule; prefer IaC.

## Command families

```bash
ip -br addr; ip route; ip route get <ip>; ss -tulpn; ss -tan state established
nft list ruleset; iptables -S; ufw status
dig +short <name>; dig @<resolver> <name> any; resolvectl status
curl -vk https://<host>/health; nc -vz <host> <port>; mtr -rw <host>
openssl s_client -connect <host>:443 -servername <host> </dev/null | openssl x509 -noout -dates -subject -issuer
tcpdump -ni any host <ip> and port <p> -c 200
aws ec2 describe-security-groups --group-ids <sg>; aws ec2 describe-route-tables --filters Name=vpc-id,Values=<vpc>
aws ec2 describe-network-acls; aws ec2 describe-vpn-connections; aws ec2 describe-nat-gateways
aws ec2 create-network-insights-path --source <eni> --destination <eni> --protocol tcp --destination-port 443
gcloud compute firewall-rules list; gcloud compute routes list; gcloud compute routers get-status <r> --region <r>
az network nsg rule list -g <rg> --nsg-name <n> -o table; az network watcher test-connectivity ...
kubectl get networkpolicy -A
```

## Safety rules

- Opening to 0.0.0.0/0, removing rules, route table changes, VPN and DNS zone changes are critical: user validation.
- Never flush host firewall rules remotely without a timed revert (`at now + 5 min` restore) or console access.
- `tcpdump` captures may contain secrets: limit packet count and store nothing sensitive.

## Verification and rollback

- Verify the exact flow that was broken, plus one flow that must stay blocked.
- Rollback: previous rule set saved before the change (`nft list ruleset > backup`, security group JSON), IaC revert.

## Deliverable

Path diagram source -> destination, evidence per layer, root cause, change applied with run id, verification of allowed and denied flows, rollback.
