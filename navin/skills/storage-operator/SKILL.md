---
name: storage-operator
description: Operate object, block and file storage - S3, GCS, Azure Blob, MinIO, EBS, EFS, Persistent Volumes, Ceph, NFS - encryption, lifecycle, versioning, replication, quotas and backups. Use for bucket and volume management, storage audits and data protection.
metadata: {"navin":{"emoji":"📦","category":"devops"}}
---

# Storage Operator

## Overview

Storage must be private by default, encrypted, versioned where data matters, replicated to meet RPO and backed up somewhere a single mistake cannot reach.

## When to use

- Create or harden buckets and volumes, lifecycle and replication rules.
- Disk/volume full, PVC pending, Ceph health warnings, NFS stale mounts.
- Backup and restore of storage.

## Engine

1. `infra action=discover provider=aws|gcp|azure|kubernetes`, `infra action=graph op=find query=bucket|volume`.
2. `infra action=check` for every command (public access, deletion and lifecycle expiration are critical).
3. `infra action=plan` -> `infra action=run` -> `infra action=verify`; `infra action=recover` for restores.

## Workflow

1. Inventory: buckets/volumes, size, encryption, versioning, public access, owner tags.
2. Harden: Block Public Access / uniform bucket-level access, SSE-KMS, TLS-only policy, versioning + MFA delete or object lock for critical data.
3. Lifecycle: transitions to cheaper tiers, expiration of noncurrent versions and incomplete multipart uploads.
4. Protection: cross-region replication, AWS Backup / snapshots schedules, Velero for Kubernetes volumes.
5. Capacity: growth trend, quotas, volume expansion (allowVolumeExpansion).

## Command families

```bash
aws s3api list-buckets; aws s3api get-bucket-encryption --bucket <b>; aws s3api get-bucket-versioning --bucket <b>
aws s3api get-bucket-lifecycle-configuration --bucket <b>; aws s3api get-bucket-replication --bucket <b>
aws ec2 describe-volumes --query 'Volumes[].[VolumeId,Size,State,Encrypted]'; aws ec2 create-snapshot --volume-id <v>
aws efs describe-file-systems
gcloud storage buckets list; gcloud storage buckets describe gs://<b>
az storage account list -o table
mc admin info <alias>; mc ls <alias>; mc version info <alias>/<bucket>
kubectl get pv,pvc -A; kubectl get volumesnapshot -A; velero backup get
ceph -s; ceph osd df; ceph health detail
showmount -e <server>; df -hT; findmnt; lsblk -f
```

## Safety rules

- Making anything public, deleting buckets/objects/volumes, lifecycle expiration rules and disabling versioning are critical: user validation.
- Snapshot before resize or filesystem operations.
- Never `aws s3 rm --recursive` or `rb --force` without a listed object count and user approval.

## Verification and rollback

- Verify settings with the matching `get-*` command and an access test from an unauthorized principal.
- Rollback: restore previous object versions, snapshot restore, Velero restore, previous bucket policy JSON.

## Deliverable

Storage inventory with posture flags, changes with run ids, protection status (versioning, replication, last backup), rollback.
