---
name: private-cloud-operator
description: Operate private clouds and virtualization - OpenStack (openstack CLI), VMware vSphere (govc), Proxmox VE (pvesh, qm, pct) - VMs, templates, networks, storage, snapshots and capacity. Use for on-premise VM lifecycle, inventory and maintenance.
metadata: {"navin":{"emoji":"🏢","category":"devops"}}
---

# Private Cloud Operator

## Overview

Same discipline as public cloud on premise: inventory first, snapshot before change, templates and IaC (Terraform providers openstack, vsphere, proxmox) over clicks.

## When to use

- Create, resize, migrate or delete VMs and containers on OpenStack, vSphere or Proxmox.
- Capacity and health of hosts, datastores, Ceph pools.
- Maintenance mode, host patching, snapshot cleanup.

## Engine

1. `infra action=status` then `infra action=discover provider=openstack|vsphere|proxmox`.
2. `infra action=catalog provider=<p> query=<vm|snapshot|network>` and `describe` for exact syntax.
3. `infra action=check` each command, `infra action=plan` for multi-VM work, `infra action=run`, `infra action=verify`.

## Workflow

1. Confirm the endpoint (OS_CLOUD / GOVC_URL / Proxmox node) and that it is the intended environment.
2. Inventory: hosts, VMs, templates, networks, datastores/pools, free capacity.
3. Change: snapshot, apply from template or IaC, verify guest health (ping, SSH, cloud-init done).
4. Maintenance: migrate VMs off (vMotion / live migration), enter maintenance, patch, exit, rebalance.

## Command families

```bash
openstack server list --long; openstack flavor list; openstack network list; openstack volume list
openstack hypervisor stats show; openstack server create --flavor <f> --image <i> --network <n> --key-name <k> <name>
openstack server image create --name <snap> <server>
govc about; govc ls /<dc>/vm; govc vm.info -r <vm>; govc datastore.info; govc host.info
govc snapshot.create -vm <vm> pre-change; govc vm.clone -vm <template> -on=false <new>
govc host.maintenance.enter <host>
pvesh get /cluster/resources --type vm; pvesh get /nodes/<node>/status
qm list; qm config <vmid>; qm snapshot <vmid> pre-change; qm clone <template> <newid> --full
pct list; pct snapshot <ctid> pre-change
pvecm status; ceph -s
```

## Safety rules

- VM/volume deletion, host maintenance, network changes and snapshot deletion are critical: user validation.
- Long-lived snapshots hurt performance: record and clean those you create.
- Never store hypervisor credentials in files you create; use the existing env/config.

## Verification and rollback

- Verify: VM running, guest agent reports IP, service health check, host/cluster quorum healthy.
- Rollback: revert to the pre-change snapshot (`govc snapshot.revert`, `qm rollback`, `pct rollback`, OpenStack rebuild from image).

## Deliverable

Inventory and capacity summary, actions with run ids, verification evidence, snapshots created (and their cleanup), rollback.
